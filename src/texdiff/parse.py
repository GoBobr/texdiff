"""Parser wrapper: LaTeX source → list of :class:`texdiff.nodes.Node`.

Built on :mod:`pylatexenc.latexwalker`. This module owns all knowledge
about pylatexenc: the rest of texdiff works only on our own Node type.

Design decisions (v0):

* ``\\input``/``\\include`` are NOT resolved here yet - v0 expects the
  caller to compare flattened sources or single files (flattening via
  latexpand stays an external step); see docs/roadmap.
* Math, verbatim environments, comments and unknown macros become
  ATOMIC nodes: exact-match comparison, never diffed inside.
* Known text-bearing environments and groups get children so the
  aligner can recurse into them.
* Source positions recorded by pylatexenc are dropped: ``Node.text``
  carries the exact span already.
"""

from __future__ import annotations

import re
from pathlib import Path

from pylatexenc.latexwalker import (
    LatexCommentNode,
    LatexEnvironmentNode,
    LatexGroupNode,
    LatexMacroNode,
    LatexCharsNode,
    LatexMathNode,
    LatexSpecialsNode,
    LatexWalker,
    get_default_latex_context_db,
)
from pylatexenc.macrospec import EnvironmentSpec, VerbatimArgsParser

from .nodes import Node

# Environments whose body is text-like: the aligner may recurse into
# them (marking up changes inside paragraphs). Everything else -
# verbatim (lstlisting, verbatim, minted...), math displays, tikz
# pictures - is an atomic block by default.
TEXT_ENVIRONMENTS = frozenset(
    {
        "document",
        "itemize",
        "enumerate",
        "description",
        "quote",
        "quotation",
        "center",
        "flushleft",
        "flushright",
    }
)

# Environments that must NEVER be diffed internally.
VERBATIM_ENVIRONMENTS = frozenset(
    {
        "verbatim",
        "verbatim*",
        "lstlisting",
        "minted",
        "alltt",
        "filecontents",
        "filecontents*",
    }
)

# Table environments: atomic in v0 (whole-block replace when their
# structure changes). Row-granular alignment is a v1 roadmap item.
TABLE_ENVIRONMENTS = frozenset(
    {"tabular", "tabular*", "tabularx", "longtable", "longtable*", "array"}
)


def _latex_context():
    """Default pylatexenc context extended with verbatim environments.

    pylatexenc's default specs only know ``verbatim`` as a verbatim
    environment; ``lstlisting``, ``minted``, ``alltt`` and friends are
    parsed as *regular* environments, so code listings containing
    unbalanced braces (C++ ``std::tuple<int, ...>`` constructors,
    ``#include <x>`` in ``alltt``...) make strict parsing fail and
    tolerant parsing produce garbage error nodes. Registering them
    with :class:`VerbatimArgsParser` makes the walker read their
    bodies verbatim, exactly as LaTeX does.
    """
    db = get_default_latex_context_db()
    db.add_context_category(
        "texdiff-verbatim",
        macros=[],
        environments=[
            EnvironmentSpec(
                envname,
                args_parser=VerbatimArgsParser(verbatim_arg_type="verbatim-environment"),
            )
            for envname in VERBATIM_ENVIRONMENTS
        ],
        specials=[],
        prepend=True,
    )
    return db


_LATEX_CONTEXT = _latex_context()


class ParseError(ValueError):
    """Raised when LaTeX source cannot be parsed even tolerantly."""


def parse(source: str) -> list[Node]:
    """Parse LaTeX source text into texdiff nodes.

    Args:
        source: LaTeX source of one file (already flattened if the
            document is spread over several files).

    Returns:
        Top-level node list. Concatenating the ``text`` of all returned
        nodes reproduces ``source`` exactly.

    Raises:
        ParseError: if the source has unbalanced group delimiters or
            cannot be parsed even tolerantly.

    Policy: parse strictly if possible (catches most malformed input),
    fall back to *tolerant* parsing when strict mode rejects a construct
    real documents use (e.g. ``\\makecell`` bodies with ``\\\\``). In
    tolerant mode a balance check over the resulting edit script keeps
    unbalanced braces out (pylatexexenc would silently swallow them,
    which must not reach the emitter).
    """
    nodes: list[Node]
    try:
        nodelist, _pos, _len = LatexWalker(
            source, latex_context=_LATEX_CONTEXT, tolerant_parsing=False
        ).get_latex_nodes()
        converted = [_convert(n, source) for n in nodelist]
    except Exception:
        # strict mode failed: real-world construct or genuinely broken?
        try:
            nodelist, _pos, length = LatexWalker(
                source, latex_context=_LATEX_CONTEXT, tolerant_parsing=True
            ).get_latex_nodes()
        except Exception as exc:
            raise ParseError(f"cannot parse LaTeX source: {exc}") from exc
        if length == len(source) and _has_error_nodes(nodelist, source):
            raise ParseError(
                "LaTeX source is malformed (tolerant parsing recovered, "
                "but with error tokens)"
            )
        converted = [_convert(n, source) for n in nodelist]

    if "".join(n.text for n in converted) != source:
        # round-trip broken: reject rather than emit wrong bytes
        raise ParseError("parse round-trip mismatch (node spans do not cover source)")
    return converted


def parse_file(path: str | Path) -> list[Node]:
    """Read and parse a ``.tex`` file (UTF-8)."""
    return parse(Path(path).read_text(encoding="utf-8"))


# --- pylatexenc → texdiff conversion ---------------------------------------


def _convert(node: LatexNode, source: str) -> Node:
    """Convert one pylatexenc node (recursively) to a texdiff Node."""
    text = _span(node, source)

    if isinstance(node, LatexCharsNode):
        return Node(kind="text", text=text, atom=True)

    if isinstance(node, LatexCommentNode):
        return Node(kind="comment", text=text, atom=True)

    if isinstance(node, LatexMathNode):
        return Node(kind="math", text=text, atom=True)

    if isinstance(node, LatexSpecialsNode):
        return Node(kind="specials", text=text, atom=True)

    if isinstance(node, LatexGroupNode):
        return Node(
            kind="group",
            text=text,
            children=[_convert(c, source) for c in node.nodelist],
            atom=False,
        )

    if isinstance(node, LatexMacroNode):
        # macros stay atomic in v0; recursable text macros (\textbf,
        # \makecell, \emph, ...) are a v1 configuration item
        return Node(kind="macro", text=text, name=node.macroname, atom=True)

    if isinstance(node, LatexEnvironmentNode):
        envname = node.envname
        if envname in VERBATIM_ENVIRONMENTS:
            return Node(kind="env", text=text, name=envname, atom=True)
        if envname in TABLE_ENVIRONMENTS:
            return _table_env_node(node, source, envname)
        recursable = envname in TEXT_ENVIRONMENTS
        return Node(
            kind="env",
            text=text,
            name=envname,
            atom=not recursable,
            children=[_convert(c, source) for c in node.nodelist] if recursable else [],
        )

    # unknown pylatexenc node class: keep as an opaque atomic block
    return Node(kind="specials", text=text, atom=True)  # pragma: no cover


# --- table ↔ row nodes -------------------------------------------------------


def _table_env_node(node: LatexEnvironmentNode, source: str, envname: str) -> Node:
    """Convert a table environment into a recursable env of row nodes.

    Row splitting is brace-aware (``\\\\makecell{a\\\\\\\\b}`` is one row)
    and turns ``\\\\endfirsthead``/``\\\\endhead``/``\\\\endfoot``/``\\\\endlastfoot``
    boundaries into row-kind segments, so the aligner can keep header
    and body regions separate.
    """
    children = _split_table_body(node, source)
    return Node(
        kind="env",
        text=source[node.pos : node.pos + node.len],
        name=envname,
        children=children,
        atom=False,
    )


def _split_table_body(node: LatexEnvironmentNode, source: str) -> list[Node]:
    """Split a table body (source span) into row nodes.

    A *row* ends at a ``\\\\`` which sits outside braces and outside
    comments. Everything between the environment's begin/end markup
    is split at those points; each segment (including its trailing
    ``\\\\`` and line breaks) becomes one atomic row node whose
    signature is its normalized content.
    """
    body_start = _env_body_start(node, source)
    body_end = _env_body_end(node, source)
    body = source[body_start:body_end]

    rows: list[Node] = []
    seg_start = 0
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == "\\":
            # comment: skip to end of line
            if i + 1 < n and body[i + 1] == "%":
                j = body.find("\n", i)
                i = n if j < 0 else j + 1
                continue
            # \\ (row terminator) outside braces
            if i + 1 < n and body[i + 1] == "\\":
                if depth == 0:
                    rows.append(_row_node(body, seg_start, i + 2))
                    seg_start = i + 2
                i += 2
                continue
            i += 1
            continue
        if c == "%":
            j = body.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, -1 + depth) if depth else 0
        i += 1
    if seg_start < n:
        rows.append(_row_node(body, seg_start, n))
    return rows


def _row_node(body: str, start: int, end: int) -> Node:
    """Build one row node from a body slice; content-based signature."""
    text = body[start:end]
    return Node(
        kind="row",
        text=text,
        name=_row_key(text),
        atom=True,
    )


# pure table structure: makes or breaks nothing visually by itself and
# code-generators freely move it across row boundaries (\hline before
# vs after a row); row signatures must ignore it or rows flip between
# "deleted" and "added" wholesale
_STRUCT_TOKEN_RE = re.compile(
    r"\\(?:hline|hdashline|toprule|midrule|bottomrule"
    r"|endfirsthead|endhead|endfoot|endlastfoot"
    r"|cline\s*\{[^{}]*\})"
)


def _row_key(text: str) -> str:
    """Content-based signature of a table row (ordering by content).

    Structural tokens (``\\\\hline``, ``\\\\``, ``\\\\endhead`` ...)
    are stripped first: two generators may emit ``row \\\\ \\hline``
    and ``\\hline row \\\\`` for the same logical row, and those must
    compare equal or the whole table degrades into delete+add runs.
    """
    stripped = re.sub(r"%[^\n]*", "", text)
    stripped = _STRUCT_TOKEN_RE.sub(" ", stripped)
    stripped = stripped.replace("\\\\", " ")
    return re.sub(r"\s+", " ", stripped).strip() or "\x00empty"


def _env_body_start(node: LatexEnvironmentNode, source: str) -> int:
    """Offset just past ``\\begin{env}`` plus its arguments.

    Skips the optional ``[...]`` AND the mandatory ``{colspec}`` group
    (brace-depth aware: ``{|W{.25}|W{.07}|}`` nests one level): the
    column specification belongs to the table structure, never to a
    row - markup between ``\\begin{env}`` and its colspec provokes
    ``Illegal pream-token`` in the array package.
    """
    m = re.match(r"\s*\\begin\{[a-zA-Z*]+\}", source[node.pos :])
    if not m:  # pragma: no cover - parse guarantees this exists
        return node.pos + 1
    i = node.pos + m.end()
    rest = source[i:]
    m_opt = re.match(r"\s*\[[^\]]*\]", rest)
    if m_opt:
        i += m_opt.end()
        rest = source[i:]
    m_open = re.match(r"\s*\{", rest)
    if m_open:
        start = i + m_open.end()
        depth = 1
        j = start
        while j < len(source) and depth > 0:
            c = source[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "%":  # pragma: no cover - degenerate colspec
                nl = source.find("\n", j)
                j = len(source) if nl < 0 else nl
                continue
            j += 1
        return j
    return i


def _env_body_end(node: LatexEnvironmentNode, source: str) -> int:
    """Offset of the ``\\end{env}`` (last occurrence)."""
    end = f"\\end{{{node.envname}}}"
    idx = source.rfind(end, node.pos, node.pos + node.len)
    if idx < 0:  # pragma: no cover - round-trip check would catch it
        return node.pos + node.len
    return idx


def _span(node: LatexNode, source: str) -> str:
    """Exact source span of a pylatexenc node, including delimiters."""
    return source[node.pos : node.pos + node.len]


def _has_error_nodes(nodelist: list[LatexNode], source: str) -> bool:
    """True if tolerant parsing recovered over malformed input.

    pylatexenc's tolerant mode inserts error placeholders in some
    cases; the one it *swallows silently* is an unclosed group: the
    returned node claims closing-delimiter ``}`` but the span does not
    contain it. Detect both.
    """
    for node in _walk_pylatexenc(nodelist):
        cls = type(node).__name__
        if cls == "LatexGroupNodeWithError" or getattr(node, "is_error_node", False):
            return True
        delimiters = getattr(node, "delimiters", None)
        if delimiters and len(delimiters) == 2:
            closing = delimiters[1]
            if closing:
                body = source[node.pos : node.pos + node.len]
                # a real closed group ends with its closing delimiter
                # inside the span (up to trailing whitespace/comments);
                # an unclosed one claims a '}' that is not there
                if not body.rstrip().endswith(closing):
                    return True
        if isinstance(node, LatexEnvironmentNode):
            end = f"\\end{{{node.envname}}}"
            body = source[node.pos : node.pos + node.len]
            if end not in body:
                # tolerant parsing produced an environment node whose
                # closing \end is missing - malformed input
                return True
    return False


def _walk_pylatexenc(nodelist: list[LatexNode]):
    for node in nodelist:
        yield node
        for child in getattr(node, "nodelist", []) or []:
            yield from _walk_pylatexenc([child])
