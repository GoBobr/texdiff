"""Markup emitter: edit script → LaTeX source with \\DIFadd/\\DIFdel.

Walks the edit script produced by :mod:`texdiff.align` (after optional
recursion) and renders both old and new content with markup compatible
with latexdiff's UNDERLINE type, so existing review conventions and
preambles (``\\RequirePackage{ulem}\\providecommand{\\DIFadd}...``) work
unchanged.

Emitters are markup-agnostic through the :class:`Markup` protocol -
easy to add CFONT-style, color-only, or XML output later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

from .align import Delete, Edit, Insert, Match, Modify
from .nodes import Node


@dataclass(frozen=True)
class LatexdiffMarkup:
    """latexdiff-underline-style markup (blue wavy underline / red strike).

    Two markup forms exist, mirroring latexdiff's own distinction:

    * *inline* (``\\DIFadd{...}``) for text runs - wavy underline /
      strike-through; LR-mode only, must not contain block structure;
    * *block* (``\\DIFaddbegin ... \\DIFaddend``) around whole
      nodes whose content cannot live inside a macro argument
      (environments, tables, multi-line runs). Block markers switch
      the active colour declaration-style (no group!): a *colour
      group* between ``\\\\`` and ``\\hline`` provokes
      ``Misplaced \\noalign`` in tables, but a plain declaration
      (``\\color{blue}``) is legal there - LaTeX keeps it in force
      until the next ``\\DIFdelbegin``/group boundary.
    """

    add_open: str = "\\DIFadd{"
    add_close: str = "}"
    del_open: str = "\\DIFdel{"
    del_close: str = "}"
    # declarations, not groups: visible outside tables
    block_add_open: str = "\\DIFaddbegin\n"
    block_add_close: str = "\\DIFaddend\n"
    block_del_open: str = "\\DIFdelbegin\n"
    block_del_close: str = "\\DIFdelend\n"
    # row-region block markers: no-op, like latexdiff's *FL variants.
    # Anything expandable after \\ starts the next table cell and
    # provokes "Misplaced \noalign" before a following \hline (a
    # colour declaration included); rows stay visible through the
    # per-cell inline markup instead.
    row_block_add_open: str = "\\DIFaddbeginFL\n"
    row_block_add_close: str = "\\DIFaddendFL\n"
    row_block_del_open: str = "\\DIFdelbeginFL\n"
    row_block_del_close: str = "\\DIFdelendFL\n"


def render(edits: list[Edit], markup: LatexdiffMarkup = LatexdiffMarkup()) -> str:
    """Render an edit script to LaTeX source.

    Guarantees (v0 contract):

    * unchanged (``Match``) node text is emitted byte-identically;
    * markup never appears inside an atomic node's span;
    * the output compiles whenever both inputs compile.
    """
    out: list[str] = []
    for edit in _coalesce(edits):
        if isinstance(edit, Match):
            out.append(edit.node.text)
        elif isinstance(edit, Insert):
            out.append(_wrap_node(edit.new, markup, added=True))
        elif isinstance(edit, Delete):
            out.append(_wrap_node(edit.old, markup, added=False))
        elif isinstance(edit, Modify):
            if edit.inner is not None:
                # recurse into the changed environment/group, keeping
                # its \begin{...}/\end{...} (or brace) wrapper intact
                out.append(_render_recursed(edit, markup))
            else:
                out.append(_wrap_node(edit.old, markup, added=False))
                out.append(_wrap_node(edit.new, markup, added=True))
    return "".join(out)


def _coalesce(edits: list[Edit]) -> Iterator[Edit]:
    """Merge neighbouring edits of the same kind into one region.

    A single logical insertion is often split across node boundaries
    (``\\subsubsubsection`` macro + ``{arg}`` group + newline): marking
    each fragment separately both looks noisy and, for inline markup,
    breaks macro arity (``\\DIFadd{\\subsubsubsection}`` steals the
    argument braces). Coalescing glued fragments into one marked
    region fixes both problems at once.
    """
    run: list[Insert] = []
    run_del: list[Delete] = []
    for edit in edits:
        if isinstance(edit, Match):
            if run_del:
                yield Delete(old=_merge_nodes([e.old for e in run_del]))
                run_del.clear()
            if run:
                yield Insert(new=_merge_nodes([e.new for e in run]))
                run.clear()
            yield edit
        elif isinstance(edit, Insert):
            if run_del:
                yield Delete(old=_merge_nodes([e.old for e in run_del]))
                run_del.clear()
            run.append(edit)
        elif isinstance(edit, Delete):
            if run:
                yield Insert(new=_merge_nodes([e.new for e in run]))
                run.clear()
            run_del.append(edit)
        else:  # Modify
            if run_del:
                yield Delete(old=_merge_nodes([e.old for e in run_del]))
                run_del.clear()
            if run:
                yield Insert(new=_merge_nodes([e.new for e in run]))
                run.clear()
            yield edit
    if run_del:
        yield Delete(old=_merge_nodes([e.old for e in run_del]))
    if run:
        yield Insert(new=_merge_nodes([e.new for e in run]))


def _merge_nodes(nodes: list[Node]) -> Node:
    """Concatenate nodes into one synthetic node.

    A run made only of row nodes (plus pure-whitespace glue between
    them) stays ``kind="row"``: it is then marked up by
    :func:`_wrap_row`, which keeps ``&``/``\\\\``/``\\hline`` outside
    the inline markup and uses the table-safe no-op block markers.
    Losing that identity (as in earlier versions, which produced a
    plain text node) sent whole-row insertions through the generic
    block path with expandable markers glued after ``\\\\`` — a
    recipe for ``Misplaced \\noalign``.
    """
    all_rows_or_space = all(
        n.kind == "row" or (n.kind == "text" and not n.text.strip()) for n in nodes
    )
    kind = "row" if (all_rows_or_space and any(n.kind == "row" for n in nodes)) else "text"
    return Node(kind=kind, text="".join(n.text for n in nodes), atom=True)


def _wrap_node(node: Node, markup: LatexdiffMarkup, added: bool) -> str:
    """Wrap one node with inline or block markup as appropriate.

    Inline ``\\DIFadd{...}`` is LR-mode-only: it must never contain a
    macro-with-argument (the braces would steal the argument), an
    environment, or a line break. Those all take the block form, whose
    markers are no-op macros - they cannot break anything that
    compiled before.
    """
    if node.kind == "row":
        # table rows: wrap each cell's text but keep & and \\
        # outside markup - \DIFdel{a & b} is illegal in alignment
        return _wrap_row(node, markup, added)
    if _needs_block(node):
        if added:
            return f"{markup.block_add_open}{node.text}{markup.block_add_close}"
        return f"{markup.block_del_open}{node.text}{markup.block_del_close}"
    if added:
        return _wrap(node.text, markup.add_open, markup.add_close)
    return _wrap(node.text, markup.del_open, markup.del_close)


# split a row region into structural tokens and text runs. `(?<!\\)&`
# so an escaped literal ampersand (``\&`` in cell text) is NOT a
# cell separator; ``\begin{...}`` is structural alongside ``\end`` so
# nested environments in cells are never inline-wrapped.
_ROW_SPLIT_RE = re.compile(
    r"((?<!\\)&|\\\\|\\hline|\\begin\{[a-zA-Z*]+\}|\\end(?:firsthead|head|foot|lastfoot)|\\end\{[a-zA-Z*]+\})"
)


def _wrap_row(node: Node, markup: LatexdiffMarkup, added: bool) -> str:
    """Mark up a table row region: block markers + per-cell inline.

    Row regions may contain several physical rows (a delete run);
    tokens that carry table structure (``&``, ``\\\\``, ``\\hline``,
    boundaries) are kept outside the inline markup; surrounding text
    runs get the inline wrap. Block markers (no-op) delimit the
    region as a whole.
    """
    open_, close = (markup.add_open, markup.add_close) if added else (
        markup.del_open,
        markup.del_close,
    )
    b_open = markup.row_block_add_open if added else markup.row_block_del_open
    b_close = markup.row_block_add_close if added else markup.row_block_del_close

    parts = _ROW_SPLIT_RE.split(node.text)
    marked: list[str] = []
    for part in parts:
        if part and _ROW_SPLIT_RE.fullmatch(part):
            marked.append(part)  # structural token: verbatim
        elif part and part.strip() and _is_safe_inline(part):
            marked.append(_wrap(part, open_, close))
        else:
            # whitespace, or run still too structured for \uwave/\sout:
            # stays inside the region's colour, no inline wrap
            marked.append(part)
    body = "".join(marked)
    return f"{b_open}{body}{b_close}"


def _is_safe_inline(text: str) -> bool:
    """True when a text run can live inside \\DIFadd{...}/\\DIFdel{...}.

    Inline markup expands to ``\\uwave``/``\\sout`` (LR mode): the run
    must be single-line, contain no environment boundaries and no
    macro-with-argument. Used by both the regular node wrap and the
    per-cell wraps inside table row regions - a multi-line run inside
    a color-declaration block keeps the region colour instead, which
    is exactly latexdiff's degraded-mode behaviour.
    """
    if "\\begin" in text or "\\end" in text:
        return False
    if "\n" in text:
        return False
    if _ARG_MACRO_RE.search(text):
        return False
    if _LIST_ITEM_RE.search(text):
        # \item / \par inside a strikeout/wave is LR-mode illegal
        # ("Lonely \item") - cells with nested lists keep the region
        # colour instead of inline markup
        return False
    return True


# list/paragraph primitives that cannot appear inside \uwave/\sout
_LIST_ITEM_RE = re.compile(r"\\(?:item|par|newline|linebreak|cr)\b")


def _needs_block(node: Node) -> bool:
    """True when the node's text cannot live inside an LR-mode macro.

    * macro nodes: an argument-following macro wrapped inline would
      have its argument stolen by the markup braces
      (``\\DIFadd{\\subsubsubsection}`` breaks the arity);
    * environments and multi-line runs: ``\\uwave``/``\\sout`` cannot
      span paragraph or table structure.
    Content check covers macro text hidden in merged (coalesced)
    runs and synthetic text nodes.
    """
    if node.kind in {"macro", "env"}:
        return True
    return not _is_safe_inline(node.text)


# a control sequence directly followed by an argument brace: wrapping
# it inline would let the markup's closing brace terminate the macro
# argument instead (arity breakage)
_ARG_MACRO_RE = re.compile(r"\\[a-zA-Z]+\*?\s*\{")


def _render_recursed(edit: Modify, markup: LatexdiffMarkup) -> str:
    """Render a recursed Modify: wrapper + inner script + wrapper."""
    wrapped = _wrappers(edit.old)
    if wrapped is None:
        # body not locatable in the source span: safest fallback is
        # the whole-block del/add replacement
        return _wrap(edit.old.text, markup.del_open, markup.del_close) + _wrap(
            edit.new.text, markup.add_open, markup.add_close
        )
    prefix, suffix = wrapped
    return prefix + render(edit.inner or [], markup) + suffix


def _wrappers(node: Node) -> tuple[str, str] | None:
    """Locate a recursable node's delimiters around its children.

    Children span the node body contiguously; the wrapper is whatever
    precedes/follows that body span in ``node.text``. Returns ``None``
    when the body cannot be located (paranoia: fall back to block
    replace rather than emit wrong bytes).
    """
    if not node.children:
        return None
    body = "".join(c.text for c in node.children)
    text = node.text
    if node.kind == "group":
        prefix, suffix = "{", "}"
    elif node.kind == "env" and node.name:
        idx = text.rfind(f"\\end{{{node.name}}}")
        if idx < 0:
            return None
        suffix = text[idx:]
        prefix = text[: idx - len(body)]
    else:
        return None
    if prefix + body + suffix != text:
        return None
    return prefix, suffix


def _wrap(text: str, open_: str, close: str) -> str:
    """Wrap text, keeping leading/trailing whitespace outside the macro.

    ``\\DIFdel{ word }`` would render the underlined span with the
    surrounding spaces, blowing up line lengths; hoisting them out
    keeps the marked region tight and the source readable.
    """
    stripped = text.strip()
    if not stripped:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{open_}{stripped}{close}{trail}"


PREAMBLE_TEMPLATE = """\
%DIF PREAMBLE EXTENSION ADDED BY texdiff
\\RequirePackage[normalem]{ulem} %DIF PREAMBLE
\\RequirePackage{color} %DIF PREAMBLE
\\providecommand{\\DIFadd}[1]{{\\protect\\color{blue}\\uwave{{#1}}}} %DIF PREAMBLE
\\providecommand{\\DIFdel}[1]{{\\protect\\color{red}\\sout{{#1}}}} %DIF PREAMBLE
%DIF block markers: colour declarations (visible outside tables)
\providecommand{\DIFaddbegin}{\color{blue}} %DIF PREAMBLE
\providecommand{\DIFaddend}{\color{black}} %DIF PREAMBLE
\providecommand{\DIFdelbegin}{\color{red}} %DIF PREAMBLE
\providecommand{\DIFdelend}{\color{black}} %DIF PREAMBLE
%DIF row-region markers: no-op (latexdiff FL style); per-cell inline
%DIF markup carries the visibility inside table rows
\providecommand{\DIFaddbeginFL}{} %DIF PREAMBLE
\providecommand{\DIFaddendFL}{} %DIF PREAMBLE
\providecommand{\DIFdelbeginFL}{} %DIF PREAMBLE
\providecommand{\DIFdelendFL}{} %DIF PREAMBLE
%DIF END PREAMBLE EXTENSION ADDED BY texdiff
"""
