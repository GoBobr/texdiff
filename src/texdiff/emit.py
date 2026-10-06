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
from difflib import SequenceMatcher
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
    edits = _hoist_retired_tables(_coalesce(edits))
    edits = _reorder_inserted_sibling_tables(edits)
    # pull first: the punctuation Delete must be hoisted ahead of
    # the add run BEFORE the run is fused, or it permanently sits
    # between two Inserts and blocks the fusion
    edits = _pull_punctuation_deletes(edits)
    edits = _fuse_split_add_regions(edits)
    for edit in edits:
        if isinstance(edit, Match):
            out.append(edit.node.text)
        elif isinstance(edit, Insert):
            out.append(_wrap_node(edit.new, markup, added=True))
        elif isinstance(edit, Delete):
            span = _table_span(edit.old.text)
            rendered = None
            if span is not None and tables.data_rows(span[2]) >= RETIRE_MIN_ROWS:
                # retired table: struck-through old version, visible
                rendered = _emit_deleted_table(edit.old)
            else:
                rendered = _wrap_node(edit.old, markup, added=False)
            out.append(rendered)
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
                    # table, fall back to the wholesale old+new pair -
                    # unless the rows still pair by KEY (first cell):
                    # attribute-content churn (regenerated tables whose
                    # cells are rewritten wholesale) reads as low word
                    # similarity while the logical rows (one per
                    # variable: lat, lon, crs, ...) stayed the same.
                    # Such tables render fine with the inline row
                    # markup, so they fall through to the recursion
                    if tables.rows_pair_by_key(old_text, new_text):
                        pass  # inline row markup path handles it
                    else:
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
            elif _align_state(edit):
                # alignment glue: a Modify that pairs one side's
                # paragraph separator (whitespace-only text) with the
                # other side's real text is a pure insertion/deletion
                # in disguise. Rendering the whitespace side through
                # the block markup emits nothing visible but its
                # blank-lines content - a paragraph break in the
                # middle of a sentence ("...message";} ** here ** . The
                # stream..."). It is emitted as the plain separator it
                # is and only the content side takes markup.
                if edit.new.text.strip():
                    out.append(_wrap_node(edit.new, markup, added=True))
                else:
                    out.append(_wrap_node(edit.old, markup, added=False))
                    out.append(edit.new.text)
            else:
                out.append(_wrap_node(edit.old, markup, added=False))
                out.append(_wrap_node(edit.new, markup, added=True))
    return _normalize_endmark_colours("".join(out))


# inline end-marker tokens of longtable HEAD material; NOT preceded
# by an existing colour declaration (the lookbehind avoids doubling
# up on lines the row renderers already reset). Foot markers
# (\endfoot/\endlastfoot) are excluded: their material is drawn
# BEFORE the marker and a reset after the row would fight the row's
# own colouring contract tested in test_no_duplicated_endfoot.
_ENDMARK_COLOR_RE = re.compile(
    r"(?<!\\color\{black\} )(?<!\\color\{blue\} )"
    r"\\end(?:firsthead|head)\b"
)

# the row-material stretch an open colour declaration could bleed
# from: everything since the last row terminator or rule
_ROW_STRETCH_SPLIT_RE = re.compile(r"\\\\|\\hline|\\end(?:tabular|longtable[*]?)\b")
# colour-opening tokens that make a reset before \endfirsthead
# necessary (a colour declaration held open across the marker bleeds
# into the page-header rules of longtable)
_COLOR_OPEN_RE = re.compile(r"\\color\{blue\}|\\DIFadd(?:begin|FL)?\b")


def _endmark_needs_reset(text: str, marker_start: int) -> bool:
    """Would colour state be open when the marker is reached?"""
    prefix = text[:marker_start]
    # only material since the last row terminator can carry colour
    pieces = _ROW_STRETCH_SPLIT_RE.split(prefix)
    stretch = pieces[-1] if pieces else ""
    return bool(_COLOR_OPEN_RE.search(stretch))


def _normalize_endmark_colours(text: str) -> str:
    """Close open colour state at longtable page-boundary markers.

    A colour declaration (``\\DIFaddbegin`` expands to one) held
    open across an ``\\endfirsthead``/``\\endhead`` token bleeds
    into the rules the table's page header material draws
    afterwards - a lone blue line at the top of the following
    (otherwise near-blank) page. A ``\\color{black}`` reset right
    before the marker keeps row colouring up to the marker but
    draws all subsequent material black.

    The reset is only inserted when colour is genuinely open in the
    row material immediately preceding the marker: a reset token in
    head material after a structural ``\\hline`` makes longtable
    typeset a spurious empty (borderless) first row.
    """
    if (
        "\\DIFadd" not in text
        and "\\DIFdel" not in text
        and "\\color{blue}" not in text
    ):
        return text

    def _insert_reset(match: re.Match) -> str:
        if _endmark_needs_reset(text, match.start()):
            return f"\\color{{black}} {match.group(0)}"
        return match.group(0)

    return _ENDMARK_COLOR_RE.sub(_insert_reset, text)


def _hoist_retired_tables(edits: list[Edit]) -> list[Edit]:
    """Reorder ``Insert(new table) ... Delete(old table)`` pairs.

    The alignment can pair a restructured-table replacement as
    Insert(new)-before-Delete(old) (it anchors on which ever side the
    neighbouring matches sit). A replaced table reads old-then-new -
    the retired red table first, the blue replacement right after -
    so this pre-pass swaps the two, keeping any whitespace-only
    matches between them attached after the hoisted deletion.

    The same insert-first order appears when the new revision holds a
    *duplicated* table: the alignment emits Insert(copy of the new
    table) before the Modify(old table -> new table) that carries the
    replacement. The Modify already renders red-then-blue, but its
    retired half still lands after the inserted copy; the swap moves
    the Modify ahead of the insert so all retired tables precede
    their blue counterparts and numbering reads 33 (blue pair
    replacement) then 34 (the additional new copy).

    The swap is narrow on purpose: the insert must carry a longtable,
    the retired node must hold a retireable longtable (data-row
    count) - either a Delete or a table-replacement Modify - and only
    whitespace may sit between the two. Anything else - several
    edits in between, prose deletions - is untouched.
    """
    out: list[Edit] = []
    glue: list[Edit] = []
    pending: list[Insert] = []
    from . import newlines as _newlines
    for edit in edits:
        if isinstance(edit, Insert) and "\\begin{longtable" in edit.new.text:
            # tabs in generated XML attribute values make byte-fuzzy
            # probes ("<tab>0 = good") compare unequal to the blob's
            # newlines; melt all whitespace in both sides of the
            # probe before text-searching the (already-melted) blob
            probe = _MELT_PROBE_RE.sub(" ", edit.new.text)
            if probe and _newlines.new_blob and probe in _MELT_PROBE_RE.sub(
                " ", _newlines.new_blob
            ):
                pass  # locatable: no reordering intelligence needed
            pending.append(edit)
            continue
        elif pending and (
            (isinstance(edit, Insert) and _is_retirable_table(edit.new.text))
            or (
                isinstance(edit, Modify)
                and _is_table_replacement(edit)
                and _is_retirable_table(edit.old.text)
            )
        ):
            # only hoist when the NEW revision itself puts the
            # retirement before the pending inserted tables: the
            # aligner anchors on the old side and can queue an
            # insert that genuinely precedes the replaced table in
            # the new document (a metadata "Global dimensions"
            # table placed before its data table) - swapping then
            # would REORDER the output relative to the new source
            from . import newlines

            # compare the two regions by parser-recorded source
            # position in the NEW revision (text search collapses
            # byte-identical "Global dimensions" tables onto the
            # first occurrence and mis-orders later sections);
            # retire-side position comes from the replacement's new
            # node when it is a Modify, from the flattened blob as
            # text search otherwise (a Delete has no new node)
            retire_node = edit.new if isinstance(edit, Modify) else None
            insert_node = pending[0].new
            if retire_node is not None:
                insert_before_retire = newlines.node_first(insert_node, retire_node)
            else:
                insert_before_retire = None
            if insert_before_retire is True:
                # the new document places the inserted table first:
                # no swap - flush the queue and render in this order
                out.extend(pending)
                pending.clear()
                out.extend(glue)
                glue.clear()
                out.append(edit)
                continue
            if insert_before_retire is None:
                # position unknown (Delete has no new node, or
                # synthetic nodes): legacy text-search fallback.
                # Byte-identical metadata tables all collapse onto
                # the FIRST occurrence, which already biased the
                # legacy behaviour towards "insert first" - keep the
                # same bias so the fallback stays compatible
                anchor = (
                    edit.new.text if isinstance(edit, Modify) else edit.old.text
                )
                txt_says = newlines.new_first(
                    _retriable_caption_key(insert_node.text),
                    _retriable_caption_key(anchor),
                )
                if txt_says is True:
                    out.extend(pending)
                    pending.clear()
                    out.extend(glue)
                    glue.clear()
                    out.append(edit)
                    continue
            # either a retired Delete or a replacement Modify whose
            # old side retires: its red half belongs before the
            # pending inserted table(s), so the two swap; glue
            # flushed between them. Several inserted tables may
            # queue up (a metadata "Global dimensions" table plus
            # its data table, both new in one region) - the retiring
            # edit hoists above the whole queue, keeping the queue's
            # order intact (GitHub #1).
            out.append(edit)
            out.extend(glue)
            glue.clear()
            out.extend(pending)
            pending.clear()
            continue
        if (
            isinstance(edit, Match)
            and not edit.node.text.strip()
        ) or (
            isinstance(edit, Modify)
            and _align_state(edit)
        ):
            # whitespace-only glue between the inserted table and
            # the retiring edit does not break the adjacency the
            # swap keys on; it queues after the pending insert so a
            # following retiring edit still swaps with it
            if pending:
                glue.append(edit)
            else:
                out.append(edit)
            continue
        if pending:
            out.extend(pending)
            pending.clear()
        out.extend(glue)
        glue.clear()
        out.append(edit)
    if pending:
        out.extend(pending)
    out.extend(glue)
    return out


def _reorder_inserted_sibling_tables(edits: list[Edit]) -> list[Edit]:
    """Move an inserted table ahead of a Modify the new source orders first.

    The "Global dimensions" metadata tables added before each data
    table in the new revision can align AFTER the Modify that pairs
    the old data table with its new counterpart (the aligner anchors
    on the old side, where no dimensions table existed). Rendering
    the aligner's order then REVERSES the new document - the data
    table appears first, the dimensions table after it.

    When an Insert carrying a longtable directly follows a
    table-replacement Modify (only whitespace between), and the
    parser-recorded new-source positions say the inserted table
    comes FIRST in the new revision, swap the two.
    """
    from . import newlines

    out = list(edits)
    i = 0
    while i + 1 < len(out):
        mod, ins = out[i], out[i + 1]
        if (
            isinstance(mod, Modify)
            and isinstance(ins, Insert)
            and _is_table_replacement(mod)
            and "\\begin{longtable" in ins.new.text
            and mod is not ins
        ):
            if newlines.node_first(ins.new, mod.new) is True:
                out[i], out[i + 1] = ins, mod
                i += 2
                continue
        i += 1
    return out


def _pull_punctuation_deletes(edits: list[Edit]) -> list[Edit]:
    """Pull a punctuation-only Delete ahead of its adjacent Insert.

    A rewritten sentence can leave the old full stop as a lone
    ``Delete('.')`` stranded BETWEEN two add regions
    (``\\DIFadd{...}\\DIFaddend \\DIFdel{.}\\DIFaddbegin ) and ...``):
    a single struck dot typeset mid-sentence reads as noise. Moving
    it to the head of the following add region renders the pair as
    ``\\DIFdel{.} \\DIFadd{) and ...}`` - the deletion visibly leads
    a single struck dot typeset mid-sentence reads as noise. Moving
    it ahead of the contiguous run of Inserts that precede it renders
    the pair as ``\\DIFdel{.}`` first, ``\\DIFadd{...replacement...}``
    after - the deletion visibly leads the replaced block, the
    track-changes convention users expect.
    """
    result: list[Edit] = []
    for edit in edits:
        if (
            isinstance(edit, Delete)
            and edit.old.kind == "text"
            and edit.old.text.strip(" \n\t") in _LONE_PUNCTUATION
            and not _table_span(edit.old.text)
            and result
        ):
            # walk backwards over the contiguous run of Inserts
            # (glued by whitespace-only Matches): if the stranded
            # punctuation sits after added text, hoist it to the
            # start of that added run
            k = len(result)
            while k > 0:
                prev = result[k - 1]
                if isinstance(prev, Insert):
                    k -= 1
                    continue
                if isinstance(prev, Match) and not prev.node.text.strip():
                    k -= 1
                    continue
                break
            if k < len(result) and any(isinstance(e, Insert) for e in result[k:]):
                result.insert(k, edit)
                continue
        result.append(edit)
    return result


def _fuse_split_add_regions(edits: list[Edit]) -> list[Edit]:
    """Fuse consecutive Inserts split by whitespace-only glue.

    The alignment can break one inserted sentence into two
    ``Insert`` regions glued by a whitespace-only ``Match``
    (``... repository (\texttt{repo})`` + ``) and shipped ...``).
    Each insert takes its own pair of block markers, so the emitted
    source reads ``...\DIFaddend\n\n\DIFaddbegin ) and ...`` - typeset
    as a spurious LINE FEED inside one blue sentence (the not-diffed
    document carries none there).

    The glue is OLD-anchored whitespace: it belongs to the old
    revision's paragraphing, not the new one - the new document's
    own breaks live inside the insert texts themselves. So the
    fused region replaces glue HOLDING a paragraph break with a
    single space (the new side had no break there) and keeps
    single-newline glue verbatim. Both inserts must be inline
    ``text``/``macro`` runs with no table span: environments, rows
    and paragraph-level blocks keep their separate regions.
    """
    out: list[Edit] = []
    glue: list[Match] = []  # whitespace Matches held since last inline Insert
    for edit in edits:
        if isinstance(edit, Match) and not edit.node.text.strip():
            if glue or (
                out and isinstance(out[-1], Insert) and _inline_kind(out[-1].new)
            ):
                # hold whitespace glue right after an inline insert:
                # a following inline insert fuses over it, anything
                # else (Match with content, Delete, Modify...) flushes
                # it back unchanged
                glue.append(edit)
                continue
            out.append(edit)
            continue
        if (
            isinstance(edit, Insert)
            and out
            and isinstance(out[-1], Insert)
            and glue
            and _inline_kind(out[-1].new)
            and _inline_kind(edit.new)
            and not _table_span(out[-1].new.text)
            and not _table_span(edit.new.text)
            and not _needs_block_env(edit.new)
        ):
            # old-anchored glue holding a paragraph break collapses
            # to one space: the new revision had no break there
            fused = "".join(m.node.text for m in glue)
            if "\n\n" in fused:
                fused = " "
            out[-1] = Insert(
                new=Node(
                    kind=out[-1].new.kind,
                    text=out[-1].new.text + fused + edit.new.text,
                    atom=True,
                    pos=out[-1].new.pos,
                )
            )
            glue.clear()
            continue
        if glue:
            out.extend(glue)
            glue.clear()
        out.append(edit)
    if glue:
        out.extend(glue)
    return out


# sentence punctuation a stranded Delete of which reads as noise
_LONE_PUNCTUATION = {".", ",", ";", ":", "!", "?", ")", "(", "]."}


def _inline_kind(node: Node) -> bool:
    """True for node kinds safe to fuse into one marked text region.

    Plain text runs and inline macros (\texttt{...}) glue into one
    block add region; environments, groups and rows keep their own
    block boundaries (their \begin/\end must not land inside inline
    markup).
    """
    return node.kind in ("text", "macro")


def _needs_block_env(node: Node) -> bool:
    """True when a node's text opens an environment/macro block.

    Fusion guards against merging an insert that STARTS a block
    construct (\begin{...}, \item, a sectioning macro): those need
    their own region so their markup pairs stay balanced. Line
    breaks alone do NOT block fusion - the fused region takes block
    markers as a whole anyway.
    """
    return bool(
        re.search(r"\\(?:begin|end)\{", node.text)
        or re.match(r"\s*\\(?:item|section|subsection|subsubsection)\b", node.text)
    )


def _retriable_caption_key(text: str) -> str:
    """Search key for a table region in the flattened new blob.

    Falls back to the caption line (the rows of byte-identical
    metadata tables share text): the \caption{...} line is the most
    distinctive fragment of a table region and preserves enough
    context for the legacy text-search path.
    """
    m = re.search(r"\\caption\{[^}\n]*\}", text)
    return m.group(0) if m else text[:120]


def _is_retirable_table(text: str) -> bool:
    """True when a deleted node's text is a retireable longtable region."""
    span = _table_span(text)
    if span is None:
        return False
    return tables.data_rows(span[2]) >= RETIRE_MIN_ROWS


def _align_state(edit: Modify) -> bool:
    """True when one side of a text-text Modify is whitespace-only."""
    return (edit.old.kind == "text" and edit.new.kind == "text") and (
        not edit.old.text.strip() or not edit.new.text.strip()
    )


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
    merged_pos = min(
        (n.pos for n in nodes if n.pos >= 0), default=-1
    )
    return Node(
        kind=kind,
        text="".join(n.text for n in nodes),
        atom=True,
        pos=merged_pos,
    )


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

    text = node.text
    hoisted = ""
    if added:
        # longtable HEAD material glued onto the first inserted data
        # row ("\hline \endfirsthead \hline" ahead of the row
        # content): a second \endfirsthead inside the table body
        # re-classifies everything from the table start as
        # first-head material and longtable silently discards the
        # whole head - the caption and header row vanish from the
        # PDF and the first body page opens with a blank row. The
        # OLD table's head (rendered by the deleted row region)
        # already carries the \endfirsthead that terminates
        # first-head material, so the duplicated head block of the
        # inserted row is dropped: \hline separators stay (grid
        # continuity), the \endfirsthead marker itself goes.
        lines = text.split("\n")
        k = 0
        while k < len(lines):
            s = lines[k].strip()
            if not s or _PURE_STRUCT_LINE_RE.fullmatch(s) or s == "\\hline":
                k += 1
                continue
            # mixed-token lead line ("\hline \endfirsthead \hline"):
            # pure head material the parser glued onto one line
            if _HEAD_MARKER_TOKEN_RE.search(s) and _ROW_STRUCTURE_ONLY_RE.fullmatch(
                s
            ):
                k += 1
                continue
            break
        if k and any(
            re.search(r"\\end(?:firsthead|head)\b", l) for l in lines[:k]
        ):
            hoisted = "".join(
                l + "\n" for l in lines[:k] if l.strip() == "\\hline"
            )
            text = "\n" + "\n".join(lines[k:]).lstrip("\n")

    parts = _ROW_SPLIT_RE.split(text)
    marked: list[str] = []
    for part in parts:
        if part and _ROW_SPLIT_RE.fullmatch(part):
            marked.append(part)  # structural token: verbatim
        elif not part.strip():
            marked.append(part)  # whitespace: verbatim
        elif "\n" not in part.strip() and _is_safe_inline(part.strip()):
            # surrounding whitespace (the row's own line breaks)
            # must not disqualify an otherwise inline-safe run: the
            # split part '\n\-_FillValue ' is the first column of an
            # ordinary row and commenting it out leaves the cell
            # visibly empty in the PDF
            core = part.strip()
            pad_l = part[: len(part) - len(part.lstrip())]
            pad_r = part[len(part.rstrip()) :]
            marked.append(pad_l + _wrap(core, open_, close) + pad_r)
        elif (
            part.strip()
            and (deco := _SAFE_DECOR_RE.fullmatch(part.strip()))
            and _is_safe_inline(deco.group(2))
        ):
            # decoration macro with inline-safe argument: wrap the
            # INSIDE (\textbf{\DIFdel{..}} is LR-safe) so retired
            # first-column variable names stay visible and struck
            # instead of disappearing into a %DIFDELCMD comment
            marked.append(
                f"\\{deco.group(1)}{{"
                f"{_wrap(deco.group(2), open_, close)}}}"
            )
        elif (
            part.strip()
            and (rc := _SAFE_ROWCOLOR_DECOR_RE.fullmatch(part.strip()))
            and _is_safe_inline(rc.group(3))
        ):
            # `\rowcolor{..} \textbf{name}` first cell: keep the
            # colour declaration verbatim and strike the name inside
            # the decoration argument
            marked.append(
                f"{rc.group(1)}\\{rc.group(2)}{{"
                f"{_wrap(rc.group(3), open_, close)}}}"
            )
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
    return f"{hoisted}{b_open}{body}{b_close}"


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

# bare page-boundary marker lines of longtable headers/footers
_ENDMARK_LINE_RE = re.compile(r"\\end(?:firsthead|head|foot|lastfoot)\b")

# lines that are structure AND NOTHING ELSE: '\\hline', '\\endhead'
# ... - a leading '\\rowcolor{..} & content..' or '\\caption' with
# trailing text on the same line carries content and must be diffed
_PURE_STRUCT_LINE_RE = re.compile(
    r"\\(?:endfirsthead|endhead|endfoot|endlastfoot)\s*$"
)
# the leading token of a table row line (kept outside the similarity
# measures above): a bare structure command or a colour-only row
_ROW_TOKEN_RE = re.compile(
    r"^(?:\\hline|\\hdashline|\\toprule|\\midrule|\\bottomrule"
    r"|\\rowcolor\{[^}]*\}\s*)$"
)

# an environment boundary line (\end{longtable}, \end{itemize} on
# its own line): stops logical-row joining - it never belongs to
# the row's cells. The generated attribute cells keep their
# \begin{itemize} glued to the cell text on the same line, so only
# closing boundaries need this guard.
_ENV_LINE_RE = re.compile(r"^\s*\\(?:end|begin)\{[a-zA-Z*]+\}\s*$")


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

    A blue ``\\color`` declaration left open across ``\\endfirsthead``
    bleeds into the page headers' rules (a solitary blue line at the
    top of the following page): the declaration only survives until
    the row end. Colour state after ``\\endfirsthead``/``\\endhead``/
    ``\\endfoot``/``\\endlastfoot`` is reset to black.
    """
    from . import oldlines

    out: list[str] = []
    blue_open = False
    for line in text.split("\n"):
        if _ENDMARK_LINE_RE.search(line):
            # table page-boundary marker line(s): close any open
            # blue, emit the markers, restore black. The reset is
            # only inserted when blue is actually open (previous
            # line was coloured content): a reset after a
            # structural line (\hline) starts a phantom empty
            # borderless cell row in longtable.
            if line.strip() and blue_open:
                line = _ENDMARK_LINE_RE.sub(r"\\color{black} \g<0>", line)
                blue_open = False
            out.append(line)
            continue
        if line.strip() and not _STRUCT_LINE_RE.match(line):
            # exact standalone-line match only: a short fragment
            # like `\item grid_mapping: Projection` occurs inside
            # many longer old attribute cells (substring) yet is
            # genuinely new content of an added row - substring
            # matching here would paint one cell of an all-blue
            # added row black
            color = "blue" if not oldlines.in_old_line(line) else "black"
            blue_open = color == "blue"
            # \hline/\rowcolor lead-ins must stay ahead of the colour
            # declaration ("Misplaced \noalign" otherwise)
            m = re.match(
                r"^(\s*)((?:\\hline|\\hdashline|\\toprule|\\midrule|\\bottomrule"
                r"|\\rowcolor\s*\{[^}]*\})\s*)+",
                line,
            )
            if m:
                head, rest = m.group(0), line[m.end() :]
                line = f"{head}\\color{{{color}}} {rest}"
            else:
                line = f"\\color{{{color}}} {line}"
            if color == "blue":
                line = re.sub(r"(?<!\\)&", r"& \\color{blue} ", line)
        elif not line.strip() or _STRUCT_LINE_RE.match(line):
            # structural/blank lines close the row: colour state
            # cannot bleed across them
            blue_open = False
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

    Cancellation is only for old lines from *delete* regions: an old
    line inside a replace region already has new-side partners there,
    and when such lines are duplicated into a new structural context
    (a member moved from one struct into a new band-level struct),
    cancelling hides both the removal and the addition. The reviewer
    must see the delete where it happened  and  the insert where the
    copy now lives (latexdiff does the same there, while genuinely
    re-sited blocks - old side deleted outright - still move quietly)
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
    old_region: dict[int, str] = {
        i: op[0] for op in ops for i in range(op[1], op[2])
    }
    cancels_a: set[int] = set()
    cancels_b: set[int] = set()
    if un_a and un_b:
        sm2 = SequenceMatcher(
            a=[key(old_lines[i]) for i in un_a],
            b=[key(new_lines[j]) for j in un_b],
            autojunk=False,
        )
        for blk in sm2.get_matching_blocks():
            for k in range(blk.size):
                i = un_a[blk.a + k]
                # only old lines with no primary new partner may be
                # cancelled as moved (delete-region lines); replace-
                # region old lines keep their visible strikeout
                if old_region.get(i) == "delete":
                    cancels_a.add(i)
                    cancels_b.add(un_b[blk.b + k])

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
# decoration macros whose argument is pure text; wrapping inside the
# argument keeps the macro arity intact under \DIFdel/\DIFadd braces
_SAFE_DECOR_RE = re.compile(
    r"\\(textbf|textit|emph|texttt|textsl|textrm|textsf|mbox)\s*\{([^{}]*)\}"
)
# same, preceded by a \rowcolor{..} cell colour declaration (common
# in the ADS first column: `\rowcolor{lightcyan} \textbf{lat}`)
_SAFE_ROWCOLOR_DECOR_RE = re.compile(
    r"(\\rowcolor\s*\{[^}]*\}\s*)" + _SAFE_DECOR_RE.pattern
)


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
    rendered = _render_row_region(edit.inner or [], markup)
    # the parse absorbs a longtable tail (``\endfoot\endlastfoot``)
    # into the first data row node's text; when the surrounding
    # prefix already carries the tail, drop the duplicate from the
    # row region output - a repeated \endfoot inside the marked row
    # collapses the first data row into broken borders/empty cells
    if "\\endfoot" in prefix and "\\endfoot" in rendered:
        rendered = re.sub(
            r"((?:\\DIFaddbeginFL|\\DIFdelbeginFL)\s*\n?)?\s*"
            r"\\endfoot\s*\\endlastfoot\s*",
            lambda m: m.group(1) or "",
            rendered,
            count=1,
        )
    return prefix + rendered + suffix


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
    emitted_tails: set[str] = set()  # table-tail blocks already emitted
    edits = _group_keyed_row_pairs(edits)
    while i < len(edits):
        e = edits[i]
        if (
            isinstance(e, Delete)
            and e.old.kind == "row"
            and i + 1 < len(edits)
            and isinstance(edits[i + 1], Insert)
            and edits[i + 1].new.kind == "row"
            and _single_content_line(e.old.text, edits[i + 1].new.text)
            and _keyed_equality(e, edits[i + 1])
            and _keyed_mergeable(e, edits[i + 1])
        ):
            # adjacent same-key pair (possibly made adjacent by the
            # grouping pre-pass) of single-line rows: per-cell
            # merge, changed cells fully struck / waved, keys plain
            merged = _merge_keyed_rows(e, edits[i + 1], markup)
            out.append(_strip_repeated_tail(merged, emitted_tails))
            i += 2
            continue
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
            out.append(_strip_repeated_tail(merged, emitted_tails))
            i += consumed
            continue
        # non-adjacent same-key rows: the aligner interleaves a
        # deleted attribute row (units/degrees_north) with inserted
        # rows of OTHER attributes; when a deleted row and an
        # inserted row further down carry the same variable name
        # (key cell) they are the same logical row - render them as
        # ONE row with per-cell DIFdel/DIFadd marks instead of
        # retiring the whole old row
        if isinstance(e, Delete) and e.old.kind == "row":
            j = _find_key_partner(e, edits, i + 1)
            if j is not None:
                # emit intervening inserts before the merged row, in
                # document order (they belong to other attributes)
                for k in range(i + 1, j):
                    ek = edits[k]
                    if isinstance(ek, Match):
                        _record_tail(ek.node.text, emitted_tails)
                        out.append(ek.node.text)
                    elif isinstance(ek, Insert):
                        out.append(
                            _strip_repeated_tail(
                                _wrap_node(ek.new, markup, added=True),
                                emitted_tails,
                            )
                        )
                    elif isinstance(ek, Delete):
                        out.append(
                            _strip_repeated_tail(
                                _wrap_node(ek.old, markup, added=False),
                                emitted_tails,
                            )
                        )
                    elif isinstance(ek, Modify) and ek.inner is not None:
                        out.append(_render_recursed(ek, markup))
                merged = _merge_keyed_rows(e, edits[j], markup)
                out.append(_strip_repeated_tail(merged, emitted_tails))
                i = j + 1
                continue
        # single edit: normal render path, but recurse for inner lists
        if isinstance(e, Modify) and e.inner is not None:
            out.append(_render_recursed(e, markup))
        elif isinstance(e, Match):
            txt = e.node.text
            _record_tail(txt, emitted_tails)
            out.append(txt)
        elif isinstance(e, Insert):
            rendered = _wrap_node(e.new, markup, added=True)
            out.append(_strip_repeated_tail(rendered, emitted_tails))
            _record_tail(rendered, emitted_tails)
        elif isinstance(e, Delete):
            rendered = _wrap_node(e.old, markup, added=False)
            out.append(_strip_repeated_tail(rendered, emitted_tails))
            _record_tail(rendered, emitted_tails)
        i += 1
    return "".join(out)


def _group_keyed_row_pairs(edits: list[Edit]) -> list[Edit]:
    """Make same-key Delete/Insert row pairs adjacent.

    The aligner can emit ``Delete(units) Delete(valid_min)
    Delete(_FillValue) Insert(units) Insert(valid_range)
    Insert(_FillValue) ...`` - cross products that the pairwise
    merge loop cannot untangle: pairing ``units`` consumes only
    through its Insert, leaving the intervening ``_FillValue``
    Delete wholesale-retired even though its partner sits right
    after. This stable pre-pass reorders the edit list so that each
    Delete sits immediately before its matching (same key cell)
    Insert, in either original order; unmatchable edits keep their
    relative order. Pure movement - no edit is dropped or duplicated.
    """
    used_ins: set[int] = set()

    def partner(d_idx: int, seq: list[Edit]) -> int | None:
        d = seq[d_idx]
        if not isinstance(d, Delete) or d.old.kind != "row":
            return None
        for j, cand in enumerate(seq):
            if j == d_idx or j in used_ins:
                continue
            if not isinstance(cand, Insert) or cand.new.kind != "row":
                continue
            if _keyed_equality(d, cand):
                return j
        return None

    seq: list[Edit] = list(edits)
    result: list[Edit] = []
    while seq:
        e = seq[0]
        if isinstance(e, Delete):
            j = partner(0, seq)
            if j is not None:
                ins = seq[j]
                rest = [x for k, x in enumerate(seq) if k not in (0, j)]
                # adjacency achieved at the delete's position
                result.extend([e, ins])
                used_ins.add(id(ins))
                seq = rest
                continue
        result.append(e)
        seq = seq[1:]
    return result


def _single_content_line(*rows: str) -> bool:
    """True when every row region carries at most one logical row.

    Single logical rows (possibly wrapped across source lines - the
    join in :func:`_split_row_line` merges continuations) take the
    per-cell keyed merge; genuinely multi-row regions
    (``\\makecell`` cells spanning source lines) need the full
    ``_merge_row_pair`` treatment with its per-line FL markers.
    """
    for r in rows:
        if "\\makecell" in r:
            return False
        lead, line, tail = _split_row_line(r)
        # the tail may hold the bare row terminator and longtable
        # foot markers - both fine, the per-cell merge keeps the
        # NEW row's skeleton. Anything else (a further ``\\`` row
        # terminator, ``&`` cells, ``\\hline`` or an environment
        # boundary) means the region holds more than one logical
        # row and needs the _merge_row_pair treatment.
        tail_body = re.sub(r"^\\\\\s*\n?", "", tail)
        if ("\\endfoot" not in tail and "\\endlastfoot" not in tail) and (
            re.search(r"\\\\|&|\\hline|\\end\{|\\begin\{", tail_body)
        ):
            return False
    return True


def _keyed_mergeable(delete: Delete, insert: Insert) -> bool:
    """Cell-count precondition for the per-cell keyed merge.

    Same cell count is required (the merge zips cells). Inline safety
    is no longer required here: :func:`_escape_cell_pair` degrades
    LR-mode-unsafe cells (nested ``itemize`` attribute lists,
    multi-line runs) to plain colour switches, so rows whose
    attribute cells cannot take ``\\DIFdel{..}``/``\\DIFadd{..}``
    still merge per cell.
    """
    _, old_line, _ = _split_row_line(delete.old.text)
    _, new_line, _ = _split_row_line(insert.new.text)
    return len(old_line.split("&")) == len(new_line.split("&"))


def _keyed_equality(delete: Delete, insert: Insert) -> bool:
    """True when a Delete/Insert pair is the same logical attribute row.

    Same cell count, first cells (the name column) matching exactly
    or as typo-level rewrites, and at least 60 % overall row
    similarity - the signature of an attribute row whose *value*
    changed, which belongs in ONE merged row with per-cell marks
    rather than a struck-out retirement above a re-added copy.
    """
    old_cells = _row_key_cells(delete.old.text)
    new_cells = _row_key_cells(insert.new.text)
    if len(old_cells) != len(new_cells) or len(old_cells) < 2:
        return False
    if "\\makecell" in delete.old.text or "\\makecell" in insert.new.text:
        # makecell rows take the _merge_row_pair FL treatment (its
        # per-line markers and skeleton handling are tuned for them)
        return False
    old_key = _cell_key(old_cells[0])
    new_key = _cell_key(new_cells[0])
    if not old_key or not new_key:
        return False
    if old_key == new_key:
        # identical variable name: the same logical row even when the
        # attribute cells were rewritten wholesale (regenerated
        # tables). Per-cell merging shows the real changes; the old
        # similarity gate retired such rows wholesale, which reads as
        # "table completely deleted and re-added"
        if len(old_cells) < 2 and len(new_cells) < 2:
            return False
        return True
    if _last_path_component(old_key) == _last_path_component(new_key):
        # same variable under a renamed grid: the VIIRS products
        # renamed their grid path component (NPP_Grid_IMG_2D ->
        # VIIRS_Grid_IMG_2D) without touching the data fields - the
        # rows are the same logical row
        return True
    if SequenceMatcher(None, old_key, new_key).ratio() < 0.85:
        return False
    old_all = " | ".join(_cell_key(c) for c in old_cells)
    new_all = " | ".join(_cell_key(c) for c in new_cells)
    return SequenceMatcher(None, old_all, new_all).ratio() >= 0.60


def _row_content_lines(row: str) -> list[str]:
    """Content-bearing source lines of a row region (no pure structure).

    A single-line longtable row yields just ``{\\hline, <row>}``
    after normalisation - and ``\\hline`` is shared by EVERY row.
    Pairing must therefore look at *content* lines only, otherwise
    any adjacent Delete+Insert qualifies as "the same logical row"
    (the mis-merge that struck old variable rows out of existence:
    a deleted ``kiso_445`` row merged into an unrelated inserted
    ``coordinates`` row and its content was dropped silently).
    A line starting with ``\\rowcolor`` still carries cell content
    after it - only ``\\rowcolor{..}`` with nothing following is
    structure.
    """
    return [
        l
        for l in row.split("\n")
        if l.strip()
        and not _PURE_STRUCT_LINE_RE.fullmatch(l.strip())
        and not _HEAD_ONLY_RE.fullmatch(l.strip())
    ]


# a bare leading structure token (\hline and friends) with nothing
# after it: it decorates every row region and carries no content
_HEAD_ONLY_RE = re.compile(r"^(?:\\hline|\\hdashline|\\toprule|\\midrule|\\bottomrule)\s*$")


def _row_pair_matches(old_row: str, new_row: str) -> bool:
    """Do a deleted/inserted row pair look like the same logical row?

    Structure tokens (``\\hline`` ...) are shared by every row and
    must not count towards the similarity - otherwise ANY adjacent
    Delete+Insert pair qualifies (a deleted variable row would then
    merge into an unrelated inserted attribute row and its content
    silently disappear from the diff).
    """
    old_lines = {
        oldlines.norm_line(x)
        for x in _row_content_lines(old_row)
        if not _ROW_TOKEN_RE.match(x.strip())
        if (n := oldlines.norm_line(x))
    }
    new_lines = {
        oldlines.norm_line(x)
        for x in _row_content_lines(new_row)
        if not _ROW_TOKEN_RE.match(x.strip())
        if (n := oldlines.norm_line(x))
    }
    if not old_lines or not new_lines:
        return False
    shared = old_lines & new_lines
    if len(shared) >= max(len(old_lines), len(new_lines)) * _ROW_MERGE_SIMILARITY:
        return True
    # typo-level row rewrite with no line fully shared: fall back to
    # whole-region similarity so "Inpu[p]t"-style fixes still merge,
    # while unrelated rows (new variable vs old attribute block)
    # stay separate
    ratio = SequenceMatcher(
        None,
        "".join(sorted(old_lines)),
        "".join(sorted(new_lines)),
    ).ratio()
    return ratio >= 0.60


_ROW_MERGE_SIMILARITY = 0.35  # shared-line fraction below which pairs stay separate

# window for non-adjacent same-key row pairing: how many edits ahead
# of a deleted row to search for its inserted counterpart
_KEY_PARTNER_WINDOW = 6


def _row_key_cells(row: str) -> list[str]:
    """Split a single-line table row into stripped cells."""
    # first *content* line: \endfoot/\endlastfoot-only lines would
    # otherwise be picked when the row text absorbed the table tail
    _lead, line, _tail = _split_row_line(row)
    return [p.strip() for p in line.split("&")]


def _cell_key(cell: str) -> str:
    """Normalised variable name of a row's first cell.

    Strips decorations (``\\rowcolor{..}``, ``\\textbf{..}``,
    ``\\\\-`` soft hyphens) so ``\\\\textbf{kiso\\\\-\\\\_445}`` and
    ``kiso_445`` normalise identically.
    """
    c = re.sub(r"\\rowcolor\s*\{[^}]*\}", "", cell)
    c = re.sub(r"\\textbf\s*\{([^}]*)\}", r"\1", c)
    c = c.replace("\\-", "").replace("\\_", "_").replace("\\", "")
    return re.sub(r"[^A-Za-z0-9_/]", "", c)


def _last_path_component(key: str) -> str:
    """Final ``/``-separated component of a normalised row key.

    HDF-EOS field paths (``HDFEOS/GRIDS/<grid>/Data Fields/<var>``)
    rename their grid freely; the data field name is the stable
    identifier of the logical row.
    """
    return key.rsplit("/", 1)[-1] if "/" in key else key


def _find_key_partner(
    delete: Delete, edits: list[Edit], start: int
) -> int | None:
    """Index of an Insert that is the same logical row as ``delete``.

    The pair qualifies when both rows have the same cell count and
    their first cells (the variable/attribute name column) match -
    exactly, or as a typo-level rewrite with 60% overall row
    similarity. Exact key matches pair regardless of how much the
    attribute cells were rewritten (regenerated tables).
    """
    old_cells = _row_key_cells(delete.old.text)
    if len(old_cells) < 2:
        return None
    for j in range(start, min(start + _KEY_PARTNER_WINDOW, len(edits))):
        cand = edits[j]
        if not isinstance(cand, Insert) or cand.new.kind != "row":
            continue
        if _keyed_equality(delete, cand):
            return j
    return None


def _norm_underscore(cell: str) -> str:
    """Normalise underscore escaping for cell comparison.

    ``\\_`` and a raw ``_`` typeset identically (both produce an
    underscore character), so a cell difference that vanishes under
    this normalisation is spurious markup churn, not a content
    change. Whitespace is melted as generated cells also differ in
    line wrapping.
    """
    c = cell.replace("\\_", "_")
    return re.sub(r"\s+", "", c)


def _escape_cell_pair(old_cell: str, new_cell: str) -> str:
    """Render one changed cell inline: struck old + blue new.

    A control word consumes the space that follows it, so the naive
    ``\\DIFdel{..}\\DIFdelend \\DIFadd{..}`` glues old and new text
    into ONE unbreakable run (``9.96e+36-1.17e-38``) that cannot
    wrap inside a narrow ``W{..}`` column and spills into the
    neighbouring cell. The empty group after ``\\DIFdelend`` keeps
    the space a real, breakable interword space.

    Cells that cannot live inside ``\\DIFdel{..}``/``\\DIFadd{..}``
    (LR mode: a nested ``itemize`` of attribute values, a
    multi-line macro) degrade to the plain colour switches
    ``\\DIFdelbegin/\\DIFaddbegin`` instead - ulem would abort with
    "Not allowed in LR mode" and send the compile into an endless
    error-recovery loop.
    """
    if not old_cell:
        if _is_safe_inline(new_cell):
            return f"\\DIFadd{{{new_cell}}}"
        return f"\\DIFaddbegin{{}} {new_cell} \\DIFaddend{{}}"
    # underscore-escape-only difference: the OLD ADS generator emits
    # raw ``_`` in variable paths while the NEW one escapes it as
    # ``\_`` - both render identically in LaTeX. A cell pair that
    # only differs this way is not a change at all: render the NEW
    # form plain (no red strike + blue duplicate).
    if _norm_underscore(old_cell) == _norm_underscore(new_cell):
        rendered = _add_attr_breaks(new_cell) if _is_safe_inline(new_cell) else new_cell
        return rendered
    # two attribute-list cells (nested itemize): semantics are per
    # \item; diff the item lists so shared attributes (units: 1,
    # standard_name: ...) render ONCE unmarked instead of twice -
    # once red-unstruck and once blue. Only genuinely changed,
    # removed or added attributes take marks.
    if _ITEMIZE_PAIR_RE.fullmatch(old_cell) and _ITEMIZE_PAIR_RE.fullmatch(
        new_cell
    ):
        merged = _merge_itemize_cells(
            _ITEMIZE_PAIR_RE.match(old_cell).group(1),  # type: ignore[union-attr]
            _ITEMIZE_PAIR_RE.match(new_cell).group(1),  # type: ignore[union-attr]
        )
        if merged is not None:
            return merged
    old_ok = _is_safe_inline(old_cell)
    new_ok = _is_safe_inline(new_cell)
    if old_ok and new_ok:
        # edge-punctuation-only change (the old ADS generator's
        # quoting artifacts: "[ value']" vs "value"): the
        # words are identical, retiring + re-adding the whole cell
        # strikes through text that never changed. Word-level marks
        # keep the shared words plain and strike/wave only the
        # brackets and quotes.
        word_marked = _punct_only_wordmarks(old_cell, new_cell)
        if word_marked is not None:
            return word_marked
        # strike the OLD side word-by-word when it contains a long
        # unbreakable run (underscored identifiers, slashed paths):
        # ulem's argument is one unbreakable box and a long variable
        # path inside a narrow W{} column would spill across the
        # neighbouring cells. Cells whose longest whitespace-free
        # token stays short (ordinary words, "degrees\_north")
        # keep the plain \DIFdel{..}.
        tokens = old_cell.split()
        needs_breaks = any(
            len(t) >= _CELL_BREAK_TOKEN_MIN or t.count("\\_") >= 2
            for t in tokens
        )
        marked_old = (
            _strike_item_words(old_cell) if needs_breaks else f"\\DIFdel{{{old_cell}}}"
        )
        marked_new = (
            _add_attr_breaks(new_cell)
            if any(
                len(t) >= _CELL_BREAK_TOKEN_MIN or t.count("\\_") >= 2
                for t in new_cell.split()
            )
            else new_cell
        )
        return (
            f"\\DIFdelbegin {marked_old}\\DIFdelend{{}} "
            f"\\DIFadd{{{marked_new}}}"
        )
    marked_old = f"\\DIFdel{{{old_cell}}}" if old_ok else old_cell
    marked_new = f"\\DIFadd{{{new_cell}}}" if new_ok else new_cell
    return (
        f"\\DIFdelbegin{{}} {marked_old} \\DIFdelend{{}} "
        f"\\DIFaddbegin{{}} {marked_new} \\DIFaddend{{}}"
    )


# a whole-cell nested itemize: begin marker, item body, end marker
_ITEMIZE_PAIR_RE = re.compile(r"\\begin\{itemize\}(.*)\\end\{itemize\}", re.S)

# whitespace melt for hoist probes: generated XML attribute values
# carry raw tabs/newlines that defeat byte-identity comparisons
_MELT_PROBE_RE = re.compile(r"[\t\n\r]+")
# literal two-char backslash escapes of tab/newline/CR as emitted by
# the old ADS generator - in LaTeX these are control words (\t is
# the tie accent) and crash ulem when isolated inside \sout{..}
_LATEX_WS_RE = re.compile(r"""\\(?:[tnr](?=\s)|[\t\n\r])""")


def _merge_itemize_cells(old_body: str, new_body: str) -> str | None:
    """Per-item merge of two attribute itemize bodies.

    Splits on ``\\item`` tokens; items equal after normalisation
    render once, plain. Removed items render red-struck (per word, so
    long attribute values still wrap), added items blue. The
    ``\\DIFdelbegin/\\DIFdelend`` colour switches bracket the removed
    block; each struck word takes its own ``\\sout`` so narrow
    ``W{}`` columns can break between them. Returns None when the
    split yields nothing sensible (no items on either side).
    """
    old_items = _split_items(old_body)
    new_items = _split_items(new_body)
    if not old_items and not new_items:
        return None

    def key(it: str) -> str:
        # normalise away formatting-only differences: the old ADS
        # generator emitted a trailing `'` after every attribute value
        # (a quoting artifact), the new one does not; escaped
        # and raw underscores must compare equal, and literal two-char
        # ``\t``/``\n`` tab/newline artifacts from the same generator
        # must compare equal to real whitespace. Without this,
        # `units: 1'` vs `units: 1` counts as a rewrite and every
        # attribute renders twice - struck red AND blue.
        k = _LATEX_WS_RE.sub(" ", it)
        k = re.sub(r"\s+", " ", k).strip()
        k = k.rstrip("'")
        k = k.replace("\\_", "_")
        return k

    new_keys = {key(i) for i in new_items}
    old_keys = {key(i) for i in old_items}
    out: list[str] = []
    # removed attributes first (red), then kept+added (blue/plain):
    # reading order follows the reference build's del-then-add layout
    removed = [i for i in old_items if key(i) not in new_keys]
    for it in removed:
        out.append(
            "\\DIFdelbegin{} "
            + _strike_item_words(it)
            + " \\DIFdelend{}"
        )
    for it in new_items:
        if key(it) in old_keys:
            out.append(it)  # unchanged attribute: plain
        else:
            out.append("\\DIFaddbegin{} " + it + " \\DIFaddend{}")
    # keep the environment wrapper: the cell is a list, bare \item
    # tokens outside itemize are "Lonely \item" errors
    return "\\begin{itemize}" + "".join(out) + "\\end{itemize}"


def _split_items(body: str) -> list[str]:
    """Split an itemize body into per-item chunks (with \\item kept)."""
    if body is None:
        return []
    parts = re.split(r"(?=\\item\b)", body)
    return [p for p in parts if p.strip()]


def _strike_item_words(item: str) -> str:
    """Strike an attribute item word by word (ulem-safe, wrappable).

    The item's ``\\item`` token stays outside the strike text; each
    following word is wrapped in its own ``\\sout{..}`` so the narrow
    attribute column can break between words. Underscore breaks
    (``\\_\\allowbreak``) keep long identifiers wrappable.
    """
    m = re.match(r"(\\item\s*)", item)
    lead = m.group(1) if m else ""
    rest = item[len(lead) :]
    # literal two-char ``\t``/``\n`` artifacts from the old ADS
    # generator are the LaTeX tie-accent control word there - inside
    # ``\\sout{..}`` it wants an argument and kills the compile
    # ("Missing { inserted"). Melt them to whitespace first.
    rest = _LATEX_WS_RE.sub(" ", _add_attr_breaks(rest))
    # remaining real tab/newline characters also separate words:
    # they make ulem abort inside \sout{..} ("Missing { inserted")
    words = [w for w in re.split(r"\s+", rest) if w]
    if not words:
        return lead
    return lead + " ".join(f"\\sout{{{w}}}" for w in words)


def _add_attr_breaks(text: str) -> str:
    """``\\_`` and long-token breakpoints inside an attribute value.

    Mirrors the retired-table emitter's treatment: identifiers like
    ``land\\_brdf\\_fgeo`` need ``\\allowbreak`` after ``\\_`` to wrap
    inside a ``W{}`` column, and unbreakable 40+ character runs get
    comma/bracket break opportunities.
    """
    text = _BREAK_UNDERSCORE_RE.sub(r"\1\\allowbreak ", text)
    out = []
    for tok in text.split(" "):
        if len(tok) >= _CELL_BREAK_TOKEN_MIN and not re.search(
            r"\\(?:documentclass|usepackage|RequirePackage"
            r"|textattachfile|path|includegraphics|input|include)\s*[\[{]",
            tok,
        ):
            tok = _BREAK_AFTER_RE.sub(r"\\allowbreak ", tok)
        out.append(tok)
    return " ".join(out)


def _merge_keyed_rows(
    delete: Delete, insert: Insert, markup: LatexdiffMarkup
) -> str:
    """One row, per-cell DIFdel/DIFadd, from a same-key row pair.

    Keeps the row skeleton (``\\hline``, trailing ``\\\\``) of the
    NEW row; cells that differ get the inline word marks, equal
    cells stay plain. Rows with different cell counts take the
    ordinary retired + inserted formatting instead.
    """
    _old_lead, old_line, _old_tail = _split_row_line(delete.old.text)
    new_lead, new_line, new_tail = _split_row_line(insert.new.text)
    # a longtable head boundary (\endfirsthead/\endhead) glued onto
    # the first data row of the NEW table must not land mid-body:
    # the table's real head (caption + header row, emitted by the
    # header Modify/Insert region) already terminated first-head
    # material, and a SECOND \endfirsthead inside the body
    # re-classifies everything before it as first-head material.
    # longtable then swallows the caption + already-rendered rows
    # into one giant head - a near-blank page and "Overfull \vbox
    # has occurred while \output is active" worth of vertical
    # overflow. Keep the \hline grid separators, drop the marker.
    new_lead = _drop_head_markers(new_lead)
    new_tail = _drop_head_markers(new_tail)
    old_cells = old_line.split("&")
    new_cells = new_line.split("&")
    if len(old_cells) != len(new_cells):
        return _wrap_node(delete.old, markup, added=False) + _wrap_node(
            insert.new, markup, added=True
        )
    parts: list[str] = []
    for oc, nc in zip(old_cells, new_cells):
        oc_s, nc_s = oc.strip(), nc.strip()
        if oc_s == nc_s or not nc_s:
            parts.append(oc)
        else:
            parts.append(_escape_cell_pair(oc_s, nc_s))
        parts.append("&")
    parts.pop()  # trailing separator
    row_body = "".join(parts)
    return f"{new_lead}{row_body}{new_tail}"


_HEAD_MARKER_TOKEN_RE = re.compile(
    r"\\end(?:firsthead|head)(?![a-zA-Z])"
)

# a line made only of grid separators and head/foot boundary tokens
# (""\hline \endfirsthead \hline"): pure longtable head material
_ROW_STRUCTURE_ONLY_RE = re.compile(
    r"(?:\s|\\hline|\\hdashline"
    r"|\\end(?:firsthead|head|foot|lastfoot))+\s*$"
)


def _drop_head_markers(text: str) -> str:
    """Remove ``\\endfirsthead``/``\\endhead`` tokens from a row lead/tail.

    The head boundary belongs to the table head, which the emit path
    renders before the data rows; when the parser glues the marker
    onto the first data row of an inserted table, re-emitting it
    mid-body re-classifies all preceding rows as first-head material
    (see :func:`_merge_keyed_rows`). Grid separators (``\\hline``)
    and whitespace survive; a line left empty by the removal keeps
    the surrounding newlines so the row layout is unchanged.
    """
    if not text or not _HEAD_MARKER_TOKEN_RE.search(text):
        return text
    out = _HEAD_MARKER_TOKEN_RE.sub("", text)
    # collapse lines that became blank-only after token removal,
    # keeping at most the original line breaks
    return re.sub(r"\n[ \t]*\n+(\s*)", r"\n\n\1", out)


def _split_row_line(row: str) -> tuple[str, str, str]:
    """Split a row region into (lead, content line, tail).

    ``lead`` is everything up to the content line (structure
    commands like ``\\\\hline``), ``tail`` the row-end ``\\\\\\\\``
    and anything after. The content line is the first line that is
    neither blank nor a pure structure command.
    """
    lines = row.split("\n")
    k = 0
    for k, l in enumerate(lines):
        s = l.strip()
        if s and not _ROW_TOKEN_RE.match(s) and not _PURE_STRUCT_LINE_RE.fullmatch(
            s
        ):
            break
    lead = "\n".join(lines[:k]) + ("\n" if k else "")
    content = lines[k] if k < len(lines) else ""
    # a row may wrap across source lines (h5py ``array([...])``
    # dumps inside an attribute cell). A continuation line is a
    # content line that is not pure structure and does not itself
    # start a new row (``\\hline`` between two data rows does) -
    # join them so cell splitting sees the WHOLE logical row.
    # Rows that already end with the row terminator ``\\`` are
    # complete on this line: anything after them is a new row.
    j = k + 1
    if not re.search(r"\s*\\\\\s*$", content):
        while j < len(lines):
            s = lines[j].strip()
            if (
                s
                and not _ROW_TOKEN_RE.fullmatch(s)
                and not _PURE_STRUCT_LINE_RE.fullmatch(s)
                and not _HEAD_ONLY_RE.fullmatch(s)
                and not _ENV_LINE_RE.match(s)
                and "\\makecell" not in s
            ):
                content += " " + s
                j += 1
            else:
                break
    # the row-end \\ belongs to the skeleton, not to the last cell
    term = ""
    m = re.search(r"\s*(\\\\)\s*$", content)
    if m:
        term = m.group(1)
        content = content[: m.start()]
    tail = "\n".join(lines[j:])
    if term:
        tail = term + ("\n" + tail if tail else "")
    return lead, content, (tail if tail else "")


def _record_tail(text: str, tails: set[str]) -> None:
    """Remember endfoot markers emitted by matched skeleton rows."""
    if "\\endfoot" in text:
        tails.add("\\endfoot")


def _strip_repeated_tail(text: str, tails: set[str]) -> str:
    """Drop a skeleton tail block already emitted in this region.

    The parse absorbs a longtable tail (``\\endfoot\\endlastfoot``)
    into the first data row node's text; when an earlier matched
    row of this region already emitted the ``\\endfoot``, the
    marked-up row must not repeat the tail - a duplicated
    ``\\endfoot``/``\\endlastfoot`` pair inside the marked row
    collapses the first data row into broken borders / empty
    cells.
    """
    if "\\endfoot" not in tails:
        return text
    # remove the whole block: a repeated \endfoot does not merely
    # duplicate the foot, it re-classifies the rows between the two
    # declarations as foot material - invisible in a table that
    # fits on one page (longtable only typesets foots at page
    # breaks)
    return re.sub(
        r"((?:\\DIFaddbeginFL|\\DIFdelbeginFL)\s*\n?)?\s*"
        r"\\endfoot\s+\\endlastfoot\s*\n?",
        lambda m: (m.group(1) or "") + "\n",
        text,
        count=1,
    )


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
    # leading structure lines of the old row stay visible: the lead
    # covers \hline and, when the parse absorbed a longtable tail
    # into the row node, the \endfoot marker (the tail's \endlastfoot
    # counts as the first "content" line so the lead stops there).
    # Marked-up structure between \endfoot and the row would yield
    # "Misplaced \noalign" - skeleton lines must sit outside the
    # \DIF...begin/end spans
    first_content = next(
        (
            l
            for l in old_lines
            if l.strip()
            and (
                not _STRUCT_LINE_RE.match(l)
                # a caption line that the new row REPLACED is
                # content, not lead structure: emitting it as lead
                # renders a live (un-struck) caption AND the
                # DIFDELCMD copy in del_only - a duplicated
                # \caption{..} that breaks longtable ("Misplaced
                # \noalign"). Lead captions are the ones the new
                # row kept verbatim.
                or (
                    l.strip().startswith("\\caption")
                    and oldlines.norm_line(l) not in new_norms
                )
            )
        ),
        None,
    )
    lead = old_row[: old_row.find(first_content)] if first_content else ""
    out.append(lead)
    # skeleton lines already emitted verbatim by the lead: re-emitting
    # them from the marked-up new row would duplicate \endfoot and
    # break the table grid
    lead_structs = {
        l.strip()
        for l in lead.split("\n")
        if l.strip() and _PURE_STRUCT_LINE_RE.fullmatch(l.strip())
    }
    # tail markers of the NEW row still ahead of its content but not
    # covered by the lead (e.g. \endlastfoot when the lead stopped at
    # \endfoot) are hoisted out too: they must precede
    # \DIFaddbeginFL, so no colour command can sit between \endfoot
    # and \endlastfoot (that yields "Misplaced \noalign")
    for l in new_lines:
        s = l.strip()
        if not s:
            continue  # leading blank lines before the tail markers
        if not _PURE_STRUCT_LINE_RE.fullmatch(s):
            break
        if s not in lead_structs:
            out.append(l + "\n")
            lead_structs.add(s)

    # longtable HEAD block hoisted out of the FL region: when the
    # parse glued the new table's head material (\hline
    # \endfirsthead \hline) onto the FIRST inserted data row. A
    # second \endfirsthead inside the table body re-classifies
    # everything from the table start as first-head material -
    # longtable then discards the whole head (caption AND header
    # row vanish from the PDF, the first body page opens with a
    # blank row). The OLD table's head (rendered by the deleted
    # row region) already carries the \endfirsthead that
    # terminates first-head material, so the duplicated head
    # block is DROPPED: \hline separators stay (grid continuity),
    # the marker itself goes.
    k = 0
    while k < len(new_lines):
        s = new_lines[k].strip()
        if not s or _PURE_STRUCT_LINE_RE.fullmatch(s) or s == "\\hline":
            k += 1
            continue
        # mixed-token lead line: "\hline \endfirsthead \hline" is
        # pure head material too - the parser glued the markers
        # onto one line (see _drop_head_markers)
        if _HEAD_MARKER_TOKEN_RE.search(s) and _ROW_STRUCTURE_ONLY_RE.fullmatch(
            s
        ):
            k += 1
            continue
        break
    if (
        k
        and any(re.search(r"\\end(?:firsthead|head)\b", l) for l in new_lines[:k])
    ):
        for l in new_lines[:k]:
            if l.strip() == "\\hline":
                out.append(l + "\n")
        new_lines = new_lines[k:]
        new_row = "\n" + new_row.lstrip("\n")

    # old-only content lines: commented out inside \DIFdelbegin...\DIFdelend;
    # lines also present in the new row are re-emitted (colour-marked) below.
    # A similar old/new line *pair* (one-char fix, small rewording) is
    # instead emitted once with word-level DIFdel/DIFadd marks - the
    # reference treatment of the "Inpu[p]t product files" typo fix.
    del_only = [
        l
        for l in old_lines
        if l.strip()
        and not _PURE_STRUCT_LINE_RE.fullmatch(l.strip())
        and oldlines.norm_line(l) not in new_norms
    ]
    add_only = [
        l
        for l in new_lines
        if l.strip()
        and not _PURE_STRUCT_LINE_RE.fullmatch(l.strip())
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
                out.append(new_pair_map[is_add_only])
            elif oldlines.in_old(line):
                out.append(_color_line(line, "black"))
            else:
                out.append(_color_line(line, "blue"))
            ai += 1
        elif _PURE_STRUCT_LINE_RE.fullmatch(s):
            if s in lead_structs:
                continue  # already emitted by the lead - no duplicate
            out.append(" " + s if not line[:1].isspace() else line)
        elif oldlines.in_old(line):
            out.append(_color_line(line, "black"))
        else:
            out.append(_color_line(line, "blue"))
    out.append("\\DIFaddendFL\n")
    return "".join(out), 2


_ROW_LEAD_STRUCT_RE = re.compile(
    r"^(\s*)((?:\\hline|\\hdashline|\\toprule|\\midrule|\\bottomrule"
    r"|\\rowcolor\s*\{[^}]*\})\s*)+"
)


def _color_line(line: str, color: str) -> str:
    """Prepend ``\\color{..}``, after any noalign-leading structure tokens.

    A row line can start with ``\\hline``/``\\rowcolor{..}`` (the parse
    glues them onto the content): ``\\color`` before the ``\\hline``
    lands between ``\\cr`` and ``\\noalign`` and errors with
    "Misplaced \\noalign". Structure tokens keep their position at
    the line start, the colour prefix moves behind them. A line that
    is ONLY structure keeps no prefix at all.

    A ``\\caption{..}`` line is an exception in the other direction:
    the caption macro itself expands to ``\\noalign`` material, so a
    colour declaration ahead of it is just as illegal as ahead of an
    ``\\hline`` - the colour wraps the caption TEXT instead.
    """
    if not line.strip() or _PURE_STRUCT_LINE_RE.fullmatch(line.strip()):
        return line
    if re.fullmatch(r"\\\\\s*", line.strip()):
        # the bare row terminator: a \color between the caption's
        # \noalign material and \\ is illegal ("Misplaced \noalign")
        return line
    m = _CAPTION_LINE_RE.search(line)
    if m and not _ROW_LEAD_STRUCT_RE.match(line.strip()):
        open_, close = (
            ("\\color{blue}", "\\color{black}")
            if color == "blue"
            else ("\\color{red}", "\\color{black}")
        )
        inner = m.group(1)
        if inner:
            body = f"{open_}{inner}{close}"
            caption_new = m.group(0).replace(inner, body, 1)
            # everything after the caption is row terminator / tail
            # structure: a colour declaration between the caption's
            # \noalign material and \\ is illegal ("Misplaced
            # \noalign") - drop any earlier prefix colour too
            rest = line[m.end() :]
            prefix = line[: m.start()]
            prefix = re.sub(r"\\color\{(?:blue|red|black)\}\s*", "", prefix)
            return f"{prefix}{caption_new}{rest}"
    m = _ROW_LEAD_STRUCT_RE.match(line)
    if m:
        rest = line[m.end() :]
        if not rest.strip():
            return line
        return f"{m.group(0)}{_reset_after_endmarks(rest, color)}"
    return re.sub(r"^(\s*)(\S.*)$", rf"\1\\color{{{color}}} \2", line)


def _reset_after_endmarks(text: str, color: str) -> str:
    """Blue colour after an inline end-marker is reset to black.

    ``\\endfirsthead``-style tokens often sit mid-line between
    ``\\hline``s; without a reset, the page-header rules that follow
    draw in the row's blue - a lone blue line at the top of the next
    page. Content after the LAST inline end-marker renders black;
    text without such markers keeps the row's declared colour.
    """
    if color != "blue":
        return f"\\color{{{color}}} {text}"
    parts = _ENDMARK_INLINE_RE.split(text)
    if len(parts) == 1:
        return f"\\color{{blue}} {text}"
    pre = "".join(parts[:-1])  # up to and including the markers
    return f"\\color{{blue}} {pre}\\color{{black}} {parts[-1]}"


# end-marker tokens sitting inline in a row line (between \hline's)
_ENDMARK_INLINE_RE = re.compile(r"\\end(?:firsthead|head|foot|lastfoot)\b")

# a caption line: colour must wrap the caption TEXT (the macro
# expands to \noalign material that a preceding \color would break)
_CAPTION_LINE_RE = re.compile(r"\\caption\{([^{}]*)\}")


def _norm_core(s: str) -> str:
    """Minimal normalisation for contained-in checks of marked lines."""
    return re.sub(r"\\DIF(?:add|del)?(?:begin|end)?(?:FL)?|\\color\{(?:black|blue)\}|\s+", "", s)


# edge punctuation that the old generator's value quoting added
# around attribute values ("[ value']", "value'"): never word content
_EDGE_PUNCT = "[]{}()'\"`.,:;"


def _strip_edge_punct(s: str) -> tuple[str, str, str]:
    """Split a token into (leading punct, core, trailing punct)."""
    i, j = 0, len(s)
    while i < j and s[i] in _EDGE_PUNCT:
        i += 1
    while j > i and s[j - 1] in _EDGE_PUNCT:
        j -= 1
    return s[:i], s[i:j], s[j:]


def _punct_surplus(old_punct: str, new_punct: str) -> str:
    """Old-side punctuation not matched (subsequence) in the new side.

    ``"kernel)'" `` vs ``"kernel)"`` share the closing paren; the
    surplus ``'`` is what the old quoting style added and the
    only thing that should strike. Matches greedily left-to-right
    (punctuation runs are 1-3 characters, order is cosmetic).
    """
    new_chars = list(new_punct)
    surplus = []
    for ch in old_punct:
        if ch in new_chars:
            new_chars.remove(ch)
        else:
            surplus.append(ch)
    return "".join(surplus)


def _punct_only_wordmarks(old_cell: str, new_cell: str) -> str | None:
    """Word-marked rendering when only edge punctuation changed.

    The old document generator quoted attribute values tool-style
    (``[ value']``): the word content is unchanged, yet the whole
    cell would be retired and re-added - the shared words struck in
    red and duplicated in blue. When every delete/insert region of
    the word diff carries the same word content with only edge
    ``[ ] ' "`` punctuation differing, the shared core renders
    plain and only the punctuation takes marks.

    Returns the marked cell, or ``None`` when the word diff finds
    real content changes (caller falls back to whole-cell marks).
    """
    from .textdiff import DELETE, INSERT, word_diff

    chunks = word_diff(old_cell, new_cell)
    changed = [c for c in chunks if c.op != "equal"]
    if not changed:
        return new_cell
    if not any(c.op == "equal" and c.text.strip() for c in chunks) and not (
        # a whole-cell replace of identical words with different
        # edge punctuation is still quoting noise ("Latitude'" ->
        # "Latitude"): the delete/insert pairing below guards
        # against genuine rewrites by comparing cores
        [c.op for c in chunks] == [DELETE, INSERT]
    ):
        return None  # nothing shared: a genuine rewrite

    # pair up changes: a pure deletion or insertion keeps the other
    # side "empty core". Every replacement pair must have equal
    # punctuation-stripped word content.
    dels: list[str] = []
    parts: list[str] = []
    pending_del = ""
    n_ins_pending = False
    for c in chunks:
        if c.op == "equal":
            if pending_del and pending_del.strip():
                # unmatched deleted region: only quoting noise
                # may be struck alone; real deleted words mean the
                # cell genuinely changed - fall back to whole-cell
                # marks
                _l, core, _t = _strip_edge_punct(pending_del)
                if core.strip():
                    return None
                parts.append(
                    f"\\DIFdelbegin \\DIFdel{{{pending_del.strip()}}}"
                    f"\\DIFdelend{{}} "
                )
            pending_del = ""
            n_ins_pending = False
            parts.append(c.text)
        elif c.op == DELETE:
            pending_del += c.text
        else:  # INSERT
            old_pending, pending_del = pending_del, ""
            if n_ins_pending:
                return None  # two inserts against one deletion
            n_ins_pending = True
            o_lead, o_core, o_trail = _strip_edge_punct(old_pending)
            _lead, core, _trail = _strip_edge_punct(c.text)
            if core != o_core:
                return None  # word content differs: real change
            # identical word content: plain text, edge punct marked.
            # Pair the punctuation character-wise: edge punct the
            # two sides SHARE renders plain ("kernel)'" vs "kernel)"
            # shares the closing paren); only the old side's surplus
            # characters strike
            n_lead, n_core, n_trail = _strip_edge_punct(c.text)
            surplus_lead = _punct_surplus(o_lead, n_lead)
            surplus_trail = _punct_surplus(o_trail, n_trail)
            if surplus_lead:
                parts.append(
                    f"\\DIFdelbegin \\DIFdel{{{surplus_lead}}}"
                    f"\\DIFdelend{{}} "
                )
            if n_lead and not o_lead:
                parts.append(f"\\DIFadd{{{n_lead.strip()}}}")
            elif n_lead:
                parts.append(n_lead)
            if core:
                parts.append(core)
            if n_trail and not o_trail:
                parts.append(f"\\DIFadd{{{n_trail.strip()}}}")
            elif n_trail:
                parts.append(n_trail)
            if surplus_trail:
                parts.append(
                    f"\\DIFdelbegin \\DIFdel{{{surplus_trail}}}"
                    f"\\DIFdelend{{}} "
                )
            # the punctuation of the NEW side that the old lacked:
            extra = ""
            if n_lead and not o_lead:
                extra += n_lead
            if n_trail and not o_trail and n_trail != o_trail:
                extra += n_trail
            if extra.strip():
                parts.append(f"\\DIFadd{{{extra.strip()}}}")
    if pending_del and pending_del.strip():
        _l, core, _t = _strip_edge_punct(pending_del)
        if core.strip():
            return None
        parts.append(
            f"\\DIFdelbegin \\DIFdel{{{pending_del.strip()}}}\\DIFdelend{{}} "
        )
    return "".join(parts)


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
            # {} before the space: \DIFdelend alone would swallow
            # it and glue old/new text into an unbreakable run
            parts.append(f"\\DIFdelbegin \\DIFdel{{{c.text}}}\\DIFdelend{{}} ")
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

    Very long tokens (an NCML ``spatial_ref`` WKT string runs 400+
    characters with no space) get ``\\allowbreak`` breakpoints at
    punctuation corners: ulem arguments are unbreakable boxes, so
    without them the cell runs past the right page border.
    Underscore-escaped identifiers (``land\\_brdf\\_fgeo``) also get
    a break after each ``\\_`` - without it the marked first-column
    cell cannot wrap inside a narrow ``W{}`` column.
    """
    stripped = text.strip()
    if not stripped:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    fired = _long_token_breaks(stripped)
    fired = re.sub(r"(\\_)(?!\s*\\allowbreak)", r"\1\\allowbreak ", fired)
    return f"{lead}{open_}{fired}{close}{trail}"


# tokens longer than this many characters get break opportunities
# injected at their internal punctuation; shorter ones never do (the
# breakpoints cost horizontal space and shift the typeset text)
# minimum length of a whitespace-free token that needs \allowbreak
# breakpoints inserted, so long identifiers wrap inside narrow W{}
# columns (see _add_attr_breaks / _escape_cell_pair)
_BREAK_TOKEN_MIN = 40
# threshold for inline cell pairs (\DIFdel{..}\DIFadd{..}): a
# variable-path cell of an ADS file-description table already
# overflows the 25%-wide first column at this length
_CELL_BREAK_TOKEN_MIN = 25

# punctuation corners used as break opportunities: after a comma, a
# closing bracket, a colon, a slash or an equals sign (all common in
# attribute values like WKT strings, GeoTransform tuples and file
# paths such as Fields/BRDF_Albedo_Band_Mandatory_Quality_M1)
_BREAK_AFTER_RE = re.compile(r"(?<=[,:;/=)\]])")
# underscore runs get an \allowbreak too: both the escaped ``\_``
# form and the literal ``_`` identifiers emitted by the old ADS
# generator
_BREAK_UNDERSCORE_RE = re.compile(r"(?:(\\_)|_)(?!\s*\\allowbreak)")


def _long_token_breaks(text: str) -> str:
    """Inject ``\\allowbreak`` after punctuation inside very long tokens.

    ulem's ``\\uwave``/``\\sout`` make their whole argument one
    unbreakable box - a single 400-character WKT attribute then
    overflows the page even though it contains commas and brackets
    a line could break at. Only tokens longer than
    :data:`_CELL_BREAK_TOKEN_MIN` are touched, and file-reference
    arguments are skipped (their corruption would break the build).
    """
    out = []
    for tok in text.split(" "):
        if len(tok) >= _BREAK_TOKEN_MIN:
            if not re.search(
                r"\\(?:documentclass|usepackage|RequirePackage"
                r"|textattachfile|path|includegraphics|input|include)\s*[\[{]",
                tok,
            ):
                tok = _BREAK_AFTER_RE.sub(r"\\allowbreak ", tok)
        out.append(tok)
    return " ".join(out)


PREAMBLE_TEMPLATE = """\
%DIF PREAMBLE EXTENSION ADDED BY texdiff
\\RequirePackage[normalem]{ulem} %DIF PREAMBLE
\\RequirePackage{color} %DIF PREAMBLE
%DIF single braces around the marked text: double braces make the
%DIF \\uwave/\\sout argument one unbreakable box and long retired
%DIF sentences run past the right page border
\\providecommand{\\DIFadd}[1]{{\\protect\\color{blue}\\uwave{#1}}} %DIF PREAMBLE
\\providecommand{\\DIFdel}[1]{{\\protect\\color{red}\\sout{#1}}} %DIF PREAMBLE
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
