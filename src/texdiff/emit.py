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
from .parse import VERBATIM_ENVIRONMENTS
from . import tables
from .tables import RETIRE_MIN_ROWS


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

    Heavily restructured tables (>= 80 % data-row churn between the
    paired old/new table texts) bypass the row markup: they render
    wholesale - old table struck through in red, new table in blue -
    because per-row markup of that density is unreadable noise and
    the struck old table carries the "what was there" information
    the way the reference build does.
    """
    out: list[str] = []
    for edit in _coalesce(edits):
        if isinstance(edit, Match):
            out.append(edit.node.text)
        elif isinstance(edit, Insert):
            out.append(_wrap_node(edit.new, markup, added=True))
        elif isinstance(edit, Delete):
            span = _table_span(edit.old.text)
            if span is not None and tables.data_rows(span[2]) >= RETIRE_MIN_ROWS:
                # retired table: struck-through old version, visible
                out.append(_emit_deleted_table(edit.old))
            else:
                out.append(_wrap_node(edit.old, markup, added=False))
        elif isinstance(edit, Modify):
            if edit.old.kind == "env" and edit.old.name in VERBATIM_ENVIRONMENTS:
                # changed verbatim-like environment: latexdiff's
                # COLORLISTINGS line-by-line treatment (%DIF markers
                # hidden by the DIFcode listings language)
                merged = _render_verbatim_modify(edit)
                if merged is not None:
                    out.append(merged)
                    continue
            if _is_table_replacement(edit):
                old_start, old_end, old_text = _table_span(edit.old.text)
                new_start, new_end, new_text = _table_span(edit.new.text)
                if tables.is_restructured(old_text, new_text):
                    out.append(edit.old.text[:old_start])
                    out.append(
                        tables.render_restructured(old_text, new_text)
                    )
                    out.append(edit.new.text[new_end:])
                    continue
                if tables.is_pathological(old_text, new_text):
                    # poorly matched table pair: try one row-merged
                    # table, fall back to the wholesale old+new pair
                    out.append(edit.old.text[:old_start])
                    out.append(
                        tables.merge_tables(old_text, new_text)
                        or tables.render_restructured(old_text, new_text)
                    )
                    out.append(edit.new.text[new_end:])
                    continue
            if edit.inner is not None:
                # recurse into the changed environment/group, keeping
                # its \begin{...}/\end{...} (or brace) wrapper intact
                out.append(_render_recursed(edit, markup))
            else:
                out.append(_wrap_node(edit.old, markup, added=False))
                out.append(_wrap_node(edit.new, markup, added=True))
    return "".join(out)


_TABLE_SPAN_RE = re.compile(r"\\begin\{longtable\*?\}.*?\\end\{longtable\*?\}", re.S)


def _table_span(text: str) -> tuple[int, int, str] | None:
    """Locate the first longtable inside a node's text as (start, end, text)."""
    m = _TABLE_SPAN_RE.search(text)
    if m is None:
        return None
    return m.start(), m.end(), m.group(0)


def _is_table_replacement(edit: Modify) -> bool:
    """True when a Modify pair is table-bearing on both sides."""
    return bool(_table_span(edit.old.text) and _table_span(edit.new.text))


def _emit_deleted_table(node: Node) -> str:
    """Emit a retired table: struck-through old version, nothing new.

    The whole longtable span renders via the tables module (red
    struck-through); text outside the span (glue, group openers like
    ``{\\scriptsize``) passes through verbatim so the brace balance
    survives.
    """
    span = _table_span(node.text)
    if span is None:
        return node.text
    start, end, table_text = span
    return (
        node.text[:start]
        + "{\\color{red}\n"
        + tables.strike_through(table_text)
        + "}\n"
        + node.text[end:]
    )


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
    # a single-node run keeps its identity: coalescing must not
    # degrade e.g. a deleted verbatim environment (env + name) into
    # an anonymous text run - the verbatim deletion policy keys off it
    if len(nodes) == 1:
        return nodes[0]
    return Node(kind=kind, text="".join(n.text for n in nodes), atom=True)


def _wrap_node(node: Node, markup: LatexdiffMarkup, added: bool) -> str:
    """Wrap one node with inline or block markup as appropriate.

    Inline ``\\DIFadd{...}`` is LR-mode-only: it must never contain a
    macro-with-argument (the braces would steal the argument), an
    environment, or a line break. Those all take the block form, whose
    markers are no-op macros - they cannot break anything that
    compiled before.

    A whole deleted verbatim-like environment is commented out
    (``%DIFDELCMD``) instead of wrapped: rendering it visibly would
    double the document, and latexdiff's own COLORLISTINGS mode keeps
    deletions to the line-wise ``%DIF <`` markers inside *modified*
    listings only.
    """
    if node.kind == "env" and node.name in VERBATIM_ENVIRONMENTS and not added:
        return _comment_out_env(node)
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

    A deleted row region containing nested environment *bodies*
    (``\\begin{itemize}\\item ... \\end{itemize}`` inside a cell)
    cannot survive the split: the begin/end tokens are structural
    and stay verbatim while the ``\\item``s between them would be
    commented out, leaving ``\\begin{itemize}`` without any item -
    "Something's wrong--perhaps a missing \\item" for every such
    row. Such a region is commented out wholesale instead, which is
    also latexdiff's treatment of deleted rows.
    """
    b_open = markup.row_block_add_open if added else markup.row_block_del_open
    b_close = markup.row_block_add_close if added else markup.row_block_del_close
    open_, close = (
        (markup.add_open, markup.add_close)
        if added
        else (markup.del_open, markup.del_close)
    )

    if not added and _has_env_body(node.text):
        return b_open + _comment_out_rows(node.text) + b_close

    parts = _ROW_SPLIT_RE.split(node.text)
    marked: list[str] = []
    for part in parts:
        if part and _ROW_SPLIT_RE.fullmatch(part):
            marked.append(part)  # structural token: verbatim
        elif not part.strip():
            marked.append(part)  # whitespace: verbatim
        elif _is_safe_inline(part):
            marked.append(_wrap(part, open_, close))
        elif added:
            # unsafe added run: cannot take the LR-mode inline wrap;
            # colour each line blue instead (cell groups contain the
            # declaration, structurally safe like latexdiff's
            # degrade-to-block behaviour for multi-line cells)
            marked.append(_color_lines(part))
        else:
            # unsafe deleted run: comment the lines out (DIFDELCMD),
            # keeping structure tokens (already captured by the split)
            # outside; \sout cannot span \\ line breaks
            marked.append(_comment_out(part))
    body = "".join(marked)
    return f"{b_open}{body}{b_close}"


# list/paragraph primitives that cannot appear inside \uwave/\sout
_LIST_ITEM_RE = re.compile(r"\\(?:item|par|newline|linebreak|cr)\b")


def _comment_out(text: str) -> str:
    """Comment out each line, latexdiff ``%DIFDELCMD <`` convention.

    The result always ends in a newline: a structural token
    (``&``, ``\\\\``) captured by the row split is appended
    directly after the commented run, and a comment character at the
    start of the line would otherwise swallow it.
    """
    if not text.strip():
        return text
    lines = text.split("\n")
    # a trailing empty line from the split is dropped: the final newline
    # added below re-creates it
    while lines and not lines[-1].strip():
        lines.pop()
    out = [
        f"%DIFDELCMD < {line} \\%%" if line.strip() else line for line in lines
    ]
    return "\n".join(out) + "\n"


# nested environment whose *body* carries content a row split would
# destroy (itemize/enumerate \item lists inside a table cell): begin
# and end tokens are structural for the split, whatever sits between
# them is not - a commented-out body leaves the env empty
_ENV_BODY_RE = re.compile(r"\\(?:begin|end)\{(?:itemize|enumerate|description)\}")


def _has_env_body(text: str) -> bool:
    """True when a row region contains a list-environment body."""
    return bool(_ENV_BODY_RE.search(text))


def _comment_out_rows(text: str) -> str:
    """Comment out a whole row region, keeping ``\\hline`` visible.

    Table-only structure tokens (``\\hline`` and friends) share lines
    with nothing else in generated tables and must stay: dropping
    them would change the visual grid of the *remaining* rows. Any
    other line - row content, environment bodies inside cells - is
    neutralised with latexdiff's ``%DIFDELCMD <`` convention; the
    ``\\%%`` tail keeps a following structural token on the same
    line from being swallowed by the comment.
    """
    out: list[str] = []
    for line in text.split("\n"):
        code = re.sub(r"(?<!\\)%.*$", "", line)
        if _STRUCT_TOKEN_RE.search(code):
            out.append(line)
        elif not line.strip():
            out.append(line)
        else:
            out.append(f"%DIFDELCMD < {line} \\%%")
    return "\n".join(out) + ("\n" if not text.endswith("\n") else "")


# table-structure lines that must never receive a colour declaration
# (bare structure commands are legal only outside a cell's group)
_STRUCT_LINE_RE = re.compile(r"^\s*\\(?:hline|endhead|endfoot|rowcolor|caption)\b")


def _color_lines(text: str) -> str:
    """Colour each content line blue (degraded add markup).

    A colour declaration is legal at the start of a table cell but
    dies at the cell boundary, so it must be re-started after each
    ``&`` on the same line (refine-diff convention); structure-only
    lines (``\\\\hline`` ...) stay untouched.
    """
    out: list[str] = []
    for line in text.split("\n"):
        if line.strip() and not _STRUCT_LINE_RE.match(line):
            line = f"\\color{{blue}} {line}"
            line = re.sub(r"(?<!\\)&", r"& \\color{blue} ", line)
        out.append(line)
    return "\n".join(out)


# row-structure tokens that may share a line with row content but
# must survive a wholesale deletion of that row (they belong to the
# table grid, not to the deleted row): bare \hline and friends
_STRUCT_TOKEN_RE = re.compile(
    r"^\s*\\(?:hline|hdashline|toprule|midrule|bottomrule"
    r"|endfirsthead|endhead|endfoot|endlastfoot)\b"
)


# ---------------------------------------------------------------- verbatim --
# latexdiff COLORLISTINGS treatment of changed verbatim-like
# environments: both revisions merge into ONE environment whose body
# carries per-line markers. Inside lstlisting the markers themselves
# must stay source-visible (line comments would kill the diff), so the
# preamble defines a DIFcode ``listings`` language whose delimiters
# ``%DIF < `` / ``%DIF > `` typeset as hidden red-strike / blue runs -
# exactly latexdiff's ``moredelim=[il]`` trick.
_DIF_DEL_MARK = "%DIF < "
_DIF_ADD_MARK = "%DIF > "


def _split_verbatim(node: Node) -> tuple[str, list[str], str] | None:
    """Split a verbatim env node into (begin, body lines, end).

    Returns ``None`` when the begin/end delimiters cannot be located
    (tolerant parsing artifacts) so the caller can fall back.
    """
    text = node.text
    name = node.name
    if not name:
        return None
    m = re.match(
        r"^(?P<begin>\\begin\{" + re.escape(name) + r"\}(?:\[[^\]]*\])?)\n",
        text,
    )
    idx = text.rfind("\\end{" + name + "}")
    if not m or idx < 0 or idx <= m.end():
        return None
    begin = m.group("begin")
    body = text[m.end() : idx]
    end = text[idx:]
    return begin, body.split("\n"), end


def _render_verbatim_modify(edit: Modify) -> str | None:
    """Render a modified verbatim env as one line-marked environment.

    Lines only in the old revision get the ``%DIF <`` prefix (the
    DIFcode language typesets them hidden in red strikeout), lines
    only in the new revision ``%DIF >`` (blue); equal lines stay as
    the new revision's bytes. The merged body keeps the NEW begin/end
    delimiters, extended with ``alsolanguage=DIFcode`` so the markers
    are interpreted (a plain verbatim env cannot host them: returned
    unchanged from ``_split_verbatim`` handling in the caller).
    """
    from difflib import SequenceMatcher

    old = _split_verbatim(edit.old)
    new = _split_verbatim(edit.new)
    if old is None or new is None:
        return None
    _, old_lines, _ = old
    begin, new_lines, end = new

    sm = SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    out: list[str] = []
    changed = False
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(new_lines[j1:j2])
        else:
            changed = True
            for line in old_lines[i1:i2]:
                out.append(_DIF_DEL_MARK + line if line.strip() else _DIF_DEL_MARK.rstrip())
            for line in new_lines[j1:j2]:
                out.append(_DIF_ADD_MARK + line if line.strip() else _DIF_ADD_MARK.rstrip())
    if not changed:
        return edit.new.text

    # extend the optional argument with alsolanguage=DIFcode
    begin = _add_alsolanguage(begin)
    return f"\\DIFmodbegin\n{begin}\n" + "\n".join(out) + f"\n{end}\\DIFmodend"


def _add_alsolanguage(begin: str) -> str:
    """Insert ``alsolanguage=DIFcode`` into a lstlisting begin marker.

    ``\\begin{lstlisting}[opts,alsolanguage=DIFcode]`` - the option is
    appended to an existing optional argument or one is created; a
    begin without options keeps ``\\end`` counterpart untouched. For
    non-option-taking plain ``verbatim`` the marker stays as-is: its
    ``%DIF`` markers then degrade to ordinary (visible) comments,
    which is latexdiff's own fallback.
    """
    m = re.match(r"^(\\begin\{[^}]+\})(\[.*\])?$", begin, re.S)
    if not m:
        return begin
    opener, opts = m.group(1), m.group(2)
    if opts is None:
        return f"{opener}[alsolanguage=DIFcode]"
    return f"{opener}{opts[:-1]},alsolanguage=DIFcode]"


def _comment_out_env(node: Node) -> str:
    """Comment out a whole deleted verbatim environment line-wise.

    Each line becomes ``%DIFDELCMD < line`` so nothing renders - the
    reference build's behaviour for deleted listings (they would
    otherwise inflate the document by their full body).
    """
    lines = node.text.split("\n")
    # trailing blank lines carry no information and would render as
    # bare marker lines; every interior blank line keeps one so the
    # environment's structure stays recognisable in the source
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(
        f"%DIFDELCMD < {line}".rstrip() for line in lines
    ) + "\n"


def _is_safe_inline(text: str) -> bool:
    """True when a text run can live inside \\DIFadd{...}/\\DIFdel{...}.

    Inline markup expands to ``\\uwave``/``\\sout`` (LR mode): the run
    must be single-line, contain no environment boundaries and no
    macro-with-argument - **including macros with optional
    ``[..]`` arguments** (``\\makecell[tl]{..}``, ``\\cite[x]{..}``):
    wrapping those inline would let the markup's closing brace
    terminate the macro argument instead. Used by the regular node
    wrap and the per-cell wraps inside table row regions - a
    multi-line run keeps the region colour (added) or gets commented
    out (deleted), which is latexdiff's degraded-mode behaviour for
    such cells.
    """
    if "\\begin" in text or "\\end" in text:
        return False
    if "\n" in text:
        return False
    if _ARG_MACRO_RE.search(text) or _ARG_MACRO_BR_RE.search(text):
        return False
    if _LIST_ITEM_RE.search(text):
        # \item / \par inside a strikeout/wave is LR-mode illegal
        # ("Lonely \item") - cells with nested lists keep the region
        # colour instead of inline markup
        return False
    return True


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


# a control sequence directly followed by an argument brace (possibly
# after optional ``[…]`` arguments): wrapping it inline would let the
# markup's closing brace terminate the macro argument instead
# (arity breakage)
_ARG_MACRO_RE = re.compile(r"\\[a-zA-Z]+\*?\s*\{")
_ARG_MACRO_BR_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\[\]]*\])+\s*\{")


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
\\providecommand{\\DIFaddbegin}{\\color{blue}} %DIF PREAMBLE
\\providecommand{\\DIFaddend}{\\color{black}} %DIF PREAMBLE
\\providecommand{\\DIFdelbegin}{\\color{red}} %DIF PREAMBLE
\\providecommand{\\DIFdelend}{\\color{black}} %DIF PREAMBLE
%DIF row-region markers: no-op block delimiters (latexdiff FL
%DIF style). Visibility inside rows comes from the per-cell inline
%DIF markup and from line-wise colouring of runs that cannot take
%DIF the inline wrap; a colour-switching marker after a row end and
%DIF before a rule would break the table parser
\\providecommand{\\DIFaddbeginFL}{} %DIF PREAMBLE
\\providecommand{\\DIFaddendFL}{} %DIF PREAMBLE
\\providecommand{\\DIFdelbeginFL}{} %DIF PREAMBLE
\\providecommand{\\DIFdelendFL}{} %DIF PREAMBLE
%DIF modified-block markers: no-op delimiters around line-marked
%DIF regions (verbatim environments diffed line by line)
\\providecommand{\\DIFmodbegin}{} %DIF PREAMBLE
\\providecommand{\\DIFmodend}{} %DIF PREAMBLE
%DIF COLORLISTINGS: language whose line delimiters typeset the
%DIF markers themselves as hidden colour runs, so %DIF < / %DIF >
%DIF prefixes inside listings render as strikeout / wavy text
%DIF instead of literal source noise
\\RequirePackage{listings} %DIF PREAMBLE
\\lstdefinelanguage{DIFcode}{ %DIF PREAMBLE
  moredelim=[il][\\color{red}\\sout]{\\%DIF\\ <\\ }, %DIF PREAMBLE
  moredelim=[il][\\color{blue}]{\\%DIF\\ >\\ } %DIF PREAMBLE
} %DIF PREAMBLE
%DIF END PREAMBLE EXTENSION ADDED BY texdiff
"""
