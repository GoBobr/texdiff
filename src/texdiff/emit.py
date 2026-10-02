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
from .nodes import Node, _SECTIONING_RE
from .parse import VERBATIM_ENVIRONMENTS
from . import oldlines
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


def _mark_heading_arg(text: str) -> str:
    """Reinforce a sectioning heading: ``\\DIFadd`` inside the title.

    Block markers colour the typeset heading text, but the table of
    contents entry is written at ``\\subsection`` expansion time from
    the *argument* - a colour declaration outside the braces never
    reaches the .toc file. Wrapping the argument content in
    ``\\DIFadd{...}`` (as the reference build does) carries the markup
    into the TOC line as well.

    Applied only when the title is inline-safe (no macro-with-arg,
    no nested environments): anything fancier falls back to the plain
    block colouring, which still colours the body text.
    """
    m = _SECTIONING_RE.match(text)
    if not m:
        return text
    body = text[m.end() - 1 :]  # from the opening brace
    depth = 0
    end = -1
    for i, ch in enumerate(body):
        if ch == "{" and (i == 0 or body[i - 1] != "\\"):
            depth += 1
        elif ch == "}" and (i == 0 or body[i - 1] != "\\"):
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 1:
        return text
    title = body[1:end]
    if not _is_safe_inline(title) or not title.strip():
        return text
    return (
        text[: m.end() - 1]
        + "{\\DIFadd{"
        + title
        + "}}"
        + body[end + 1 :]
    )


def _mark_heading_args_in_run(text: str) -> str:
    """Mark sectioning headings inside a coalesced insert run.

    Long insert runs are merged into one synthetic text node, so the
    per-macro heading marking never sees them. Large added regions are
    still block-coloured overall; the only visible gap is the TOC,
    which takes its content from the heading argument. This helper
    walks the run line-wise and applies :func:`_mark_heading_arg` to
    every line that is exactly one sectioning macro call.
    """
    if _SECTIONING_RE.search(text) is None:
        return text
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        head_call = _find_heading_span(stripped) if _SECTIONING_RE.match(stripped) else None
        if head_call:
            pos = line.find(head_call)
            out.append(
                line[:pos]
                + _mark_heading_arg(head_call)
                + line[pos + len(head_call) :]
            )
        else:
            out.append(line)
    return "\n".join(out)


def _find_heading_span(text: str) -> str:
    """Return the first sectioning macro call substring of `text`."""
    m = _SECTIONING_RE.search(text)
    start = m.start()
    depth = 0
    i = m.end() - 1  # at the opening brace
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return text[start:]


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

    A large added block colouring does not reach into verbatim-like
    environments: ``listings`` resets the current text colour at
    ``\\begin{lstlisting}`` and applies its own ``basicstyle`` (and
    ``commentstyle``/``morecomment`` on top), so an added YAML sample
    inside a blue chapter comes out black with wildly-coloured
    ``#``-comments. The reference build marks every line of such a
    listing ``%DIF >`` inside a DIFcode ``alsolanguage`` environment -
    the markers themselves typeset the whole line blue, including
    comment lines.
    """
    if node.kind == "env" and node.name in VERBATIM_ENVIRONMENTS and not added:
        return _comment_out_env(node)
    if node.kind == "row":
        # table rows: wrap each cell's text but keep & and \\
        # outside markup - \DIFdel{a & b} is illegal in alignment
        return _wrap_row(node, markup, added)
    if _needs_block(node):
        if added:
            body = _mark_added_listings(node.text)
            body = _mark_heading_args_in_run(body)
            return f"{markup.block_add_open}{body}{markup.block_add_close}"
        return f"{markup.block_del_open}{node.text}{markup.block_del_close}"
    if added:
        body = _mark_heading_args_in_run(node.text)
        return _wrap(body, markup.add_open, markup.add_close)
    return _wrap(node.text, markup.del_open, markup.del_close)


# matches a whole verbatim-like environment span (begin..end) inside
# a coalesced block run; non-greedy so consecutive environments match
# separately. Only lstlisting-like envs with an optional [...] arg.
_VERBATIM_SPAN_RE = re.compile(
    r"\\begin\{(lstlisting|minted|alltt)\}"
    r"(\[[^\]]*\])?"
    r"\n(.*?)\\end\{\1\}",
    re.S,
)


def _mark_added_listings(text: str) -> str:
    """Line-mark verbatim environments inside an added block.

    Every non-blank body line gets the ``%DIF >`` prefix and the
    begin marker gains ``alsolanguage=DIFcode`` (when it does not
    already), matching what a *modified* listing renders like - an
    inserted chapter then typesets its listings blue line by line
    instead of falling back to the listings language styles
    (magenta ``#``-comments and friends stick out against a blue
    chapter otherwise). Environments already carrying a ``%DIF``
    marker line (a nested modify render that leaked into the run)
    stay untouched.
    """
    if "\\begin{lstlisting" not in text and "minted" not in text and "alltt" not in text:
        return text

    def _mark(m: re.Match) -> str:
        env, opts, body = m.group(1), m.group(2), m.group(3)
        begin = f"\\begin{{{env}}}{opts or ''}"
        begin = _add_alsolanguage(begin)
        if _DIF_ADD_MARK in body or _DIF_DEL_MARK in body:
            # already line-marked (nested modify markup): keep as-is
            return m.group(0)
        lines = [(_DIF_ADD_MARK + l if l.strip() else l) for l in body.split("\n")]
        # a trailing marker-only line (empty last line) is dropped:
        # listings would typeset a blank marker line at the env end
        while lines and not lines[-1].strip():
            lines.pop()
        return begin + "\n" + "\n".join(lines) + f"\\end{{{env}}}"

    return _VERBATIM_SPAN_RE.sub(_mark, text)


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
    """Colour added lines blue, existing ones black (refine-diff).

    A colour declaration is legal at the start of a table cell but
    dies at the cell boundary, so it must be re-started after each
    ``&`` on the same line (refine-diff convention); structure-only
    lines (``\\hline`` ...) stay untouched.

    A line whose normalised content already existed in the OLD
    revision is re-emitted content, not a genuine addition: it takes
    ``\\color{black}`` so unstated-content stays visually unchanged
    (refine-diff.pl semantics).
    """
    from . import oldlines

    out: list[str] = []
    for line in text.split("\n"):
        if line.strip() and not _STRUCT_LINE_RE.match(line):
            color = "blue" if not oldlines.in_old(line) else "black"
            line = f"\\color{{{color}}} {line}"
            if color == "blue":
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

    Lines compare on *stripped* text: re-indentation (a code block
    moved one nesting level deeper) must not retire and re-add every
    line of the block.

    Moved blocks: a line group that exists in both revisions but at
    different positions falls out of the primary alignment as a
    delete run plus an insert run. A second match over the unmatched
    lines of both sides finds those pairs and cancels them - the
    block emits once, unmarked, at its new position, instead of
    being retired and re-added wholesale (the "retired and
    reintroduced" look generic text diffs avoid by word matching).
    """
    from difflib import SequenceMatcher

    old = _split_verbatim(edit.old)
    new = _split_verbatim(edit.new)
    if old is None or new is None:
        return None
    _, old_lines, _ = old
    begin, new_lines, end = new

    def key(line: str) -> str:
        return line.strip()

    sm = SequenceMatcher(
        a=[key(l) for l in old_lines], b=[key(l) for l in new_lines], autojunk=False
    )
    ops = sm.get_opcodes()

    # second pass: match unmatched old lines against unmatched new
    # lines to catch moved blocks (see docstring)
    un_a = [i for op in ops if op[0] in ("delete", "replace") for i in range(op[1], op[2])]
    un_b = [j for op in ops if op[0] in ("insert", "replace") for j in range(op[3], op[4])]
    cancels_a: set[int] = set()
    cancels_b: set[int] = set()
    if un_a and un_b:
        sm2 = SequenceMatcher(
            a=[key(old_lines[i]) for i in un_a],
            b=[key(new_lines[j]) for j in un_b],
            autojunk=False,
        )
        for blk in sm2.get_matching_blocks():
            if blk.size:
                cancels_a.update(un_a[blk.a + k] for k in range(blk.size))
                cancels_b.update(un_b[blk.b + k] for k in range(blk.size))

    out: list[str] = []
    changed = False
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            out.extend(new_lines[j1:j2])
            continue
        changed = True
        # old-only lines of this region, minus the moved (cancelled)
        for i in range(i1, i2):
            if i in cancels_a or not old_lines[i].strip():
                continue
            out.append(_DIF_DEL_MARK + old_lines[i])
        # new lines stay in new revision order; cancelled ones are
        # moved context and emit unmarked at their new position
        for j in range(j1, j2):
            if not new_lines[j].strip():
                continue
            out.append(
                new_lines[j] if j in cancels_b else _DIF_ADD_MARK + new_lines[j]
            )
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
    return prefix + _render_row_region(edit.inner or [], markup) + suffix


def _render_row_region(edits: list[Edit], markup: LatexdiffMarkup) -> str:
    """Render an inner edit list, merging Delete/Insert row pairs.

    A changed longtable row normally renders as a deleted row region
    followed by a re-added one. For rows made of multi-line makecell
    cells that layout visibly breaks the table: the deleted region's
    commented-out lines leave the row-end ``\\`` dangling, and the
    re-added row duplicates the row (and its struck cells jam a ghost
    row with double the column count into the grid).

    The reference build instead emits ONE row: common cell lines kept
    (``\\color{black}``), genuinely new lines blue, old-only lines
    ``%DIFDELCMD`` inside a ``\\DIFdelbegin...\\DIFdelend`` span just
    before the new span. :func:`_merge_row_text` produces that
    layout; anything but a qualifying row pair takes the normal
    :func:`render` path.
    """
    out: list[str] = []
    i = 0
    while i < len(edits):
        e = edits[i]
        if (
            i + 1 < len(edits)
            and isinstance(e, Delete)
            and isinstance(edits[i + 1], Insert)
            and e.old.kind == "row"
            and edits[i + 1].new.kind == "row"
            and _row_pair_matches(e.old.text, edits[i + 1].new.text)
        ):
            merged, consumed = _merge_row_pair(
                edits[i], edits[i + 1], i, edits
            )
            out.append(merged)
            i += consumed
            continue
        # single edit: normal render path, but recurse for inner lists
        if isinstance(e, Modify) and e.inner is not None:
            out.append(_render_recursed(e, markup))
        elif isinstance(e, Match):
            out.append(e.node.text)
        elif isinstance(e, Insert):
            out.append(_wrap_node(e.new, markup, added=True))
        elif isinstance(e, Delete):
            out.append(_wrap_node(e.old, markup, added=False))
        i += 1
    return "".join(out)


def _row_pair_matches(old_row: str, new_row: str) -> bool:
    """Do a deleted/inserted row pair look like the same logical row?"""
    old_lines = {l for l in (oldlines.norm_line(x) for x in old_row.split("\n")) if l}
    new_lines = {l for l in (oldlines.norm_line(x) for x in new_row.split("\n")) if l}
    if not old_lines or not new_lines:
        return False
    shared = old_lines & new_lines
    return len(shared) >= max(len(old_lines), len(new_lines)) * _ROW_MERGE_SIMILARITY


_ROW_MERGE_SIMILARITY = 0.35  # shared-line fraction below which pairs stay separate


def _merge_row_pair(
    delete: Delete, insert: Insert, idx: int, edits: list[Edit]
) -> tuple[str, int]:
    """Render a deleted+inserted row pair as one merged row (reference form)."""
    from .textdiff import word_diff
    from difflib import SequenceMatcher

    old_row, new_row = delete.old.text, insert.new.text
    old_lines = old_row.split("\n")
    new_lines = new_row.split("\n")
    new_norms = {oldlines.norm_line(x) for x in new_lines}

    out: list[str] = []
    # leading structure lines of the old row (\hline) stay visible
    first_content = next(
        (l for l in old_lines if l.strip() and not _STRUCT_LINE_RE.match(l)),
        None,
    )
    lead = old_row[: old_row.find(first_content)] if first_content else ""
    out.append(lead)

    # old-only content lines: commented out inside \DIFdelbegin...\DIFdelend;
    # lines also present in the new row are re-emitted (colour-marked) below.
    # A similar old/new line *pair* (one-char fix, small rewording) is
    # instead emitted once with word-level DIFdel/DIFadd marks - the
    # reference treatment of the "Inpu[p]t product files" typo fix.
    del_only = [
        l
        for l in old_lines
        if l.strip()
        and not _STRUCT_LINE_RE.match(l)
        and oldlines.norm_line(l) not in new_norms
    ]
    add_only = [
        l
        for l in new_lines
        if l.strip()
        and not _STRUCT_LINE_RE.match(l)
        and oldlines.norm_line(l) not in {oldlines.norm_line(x) for x in old_lines}
    ]
    pair_map: dict[int, str] = {}  # index in del_only -> rendered replacement
    new_pair_map: dict[int, str] = {}  # index in add_only -> rendered replacement
    used_new: set[int] = set()
    for oi, ol in enumerate(del_only):
        best_j, best_ratio = -1, 0.0
        for aj, al in enumerate(add_only):
            if aj in used_new:
                continue
            ratio = SequenceMatcher(
                None, oldlines.norm_line(ol), oldlines.norm_line(al)
            ).ratio()
            if ratio > best_ratio:
                best_ratio, best_j = ratio, aj
        if best_j >= 0 and best_ratio >= 0.80:
            rendering = _word_marked_line(ol, add_only[best_j])
            if rendering:
                pair_map[oi] = rendering
                new_pair_map[best_j] = rendering
                used_new.add(best_j)
    replaced = set(pair_map)
    rendered_added = set(new_pair_map)

    if any(oi not in replaced for oi in range(len(del_only))):
        out.append("\\DIFdelbeginFL\n")
        for oi, l in enumerate(del_only):
            if oi not in replaced:
                out.append(f"%DIFDELCMD < {l} \\%%\n")
        out.append("\\DIFdelendFL\n")

    out.append("\\DIFaddbeginFL\n")
    ai = 0
    for line in new_lines:
        s = line.strip()
        if not s:
            out.append(line)
            continue
        # position of this line within add_only (if it is one)
        is_add_only = ai if (ai < len(add_only) and line == add_only[ai]) else None
        if is_add_only is not None:
            if is_add_only in rendered_added:
                # word-level pair: emit the marked pair line instead
                out.append(new_pair_map[is_add_only])
            elif oldlines.in_old(line):
                out.append(re.sub(r"^(\s*)(\S.*)$", r"\1\\color{black} \2", line))
            else:
                out.append(re.sub(r"^(\s*)(\S.*)$", r"\1\\color{blue} \2", line))
            ai += 1
        elif _STRUCT_LINE_RE.match(line):
            out.append(" " + s if not line[:1].isspace() else line)
        elif oldlines.in_old(line):
            out.append(re.sub(r"^(\s*)(\S.*)$", r"\1\\color{black} \2", line))
        else:
            out.append(re.sub(r"^(\s*)(\S.*)$", r"\1\\color{blue} \2", line))
    out.append("\\DIFaddendFL\n")
    return "".join(out), 2


def _norm_core(s: str) -> str:
    """Minimal normalisation for contained-in checks of marked lines."""
    return re.sub(r"\\DIF(?:add|del)?(?:begin|end)?(?:FL)?|\\color\{(?:black|blue)\}|\s+", "", s)


def _word_marked_line(old_line: str, new_line: str) -> str:
    """One line carrying word-level DIFdel/DIFadd marks.

    Only for lines whose content is inline-safe on both sides (no
    ``&`` cell separators with unsafe runs, no macros-with-args in the
    changed region) - the wrap would otherwise break the row.
    """
    from .textdiff import (DELETE, INSERT, Chunk, word_diff)

    chunks = word_diff(old_line, new_line)
    parts: list[str] = []
    for c in chunks:
        if c.op == "equal":
            parts.append(c.text)
        elif c.op == "delete":
            if not _is_safe_inline(c.text):
                return ""
            parts.append(f"\\DIFdelbegin \\DIFdel{{{c.text}}}\\DIFdelend ")
        else:
            if not _is_safe_inline(c.text):
                return ""
            parts.append(f"\\DIFadd{{{c.text}}}")
    return "".join(parts)


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
