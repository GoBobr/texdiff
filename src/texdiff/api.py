"""Public API of texdiff.

Three functions make up the pipeline::

    from texdiff import parse, align, render
    from texdiff import diff_documents

    result = diff_documents(old_source, new_source)
    print(result.marked_up)   # the diff.tex source
    print(result.stats)       # counts of edits
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .align import Delete, Edit, Insert, Match, Modify, align

# word-similarity floor below which a paired text run is retired and
# re-added wholesale instead of word-marked: two sentences sharing
# less than half their words are a rewrite, not an edit
_REPLACE_WORD_SIMILARITY = 0.5
from .emit import LatexdiffMarkup, render
from .flatten import Flattener, flatten_file, flatten_source
from .nodes import Node, text_node
from .parse import parse
from .preamble import (
    count_preamble_changes,
    mark_preamble_macro_changes,
    split_preamble,
)
from .textdiff import DELETE, EQUAL, Chunk, word_diff


@dataclass(frozen=True)
class DiffStats:
    """Aggregated change counts of one diff."""

    matches: int = 0
    modifications: int = 0
    insertions: int = 0
    deletions: int = 0
    preamble_changes: int = 0

    @property
    def changed(self) -> int:
        """Number of changed blocks (modify+insert+delete)."""
        return self.modifications + self.insertions + self.deletions

    def __add__(self, other: "DiffStats") -> "DiffStats":
        """Sum two stats (used to layer preamble counts onto body counts)."""
        return DiffStats(
            matches=self.matches + other.matches,
            modifications=self.modifications + other.modifications,
            insertions=self.insertions + other.insertions,
            deletions=self.deletions + other.deletions,
            preamble_changes=self.preamble_changes + other.preamble_changes,
        )

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        pre = (
            f", {self.preamble_changes} preamble lines changed"
            if self.preamble_changes
            else ""
        )
        return (
            f"{self.matches} unchanged, {self.modifications} modified, "
            f"{self.insertions} added, {self.deletions} deleted{pre}"
        )


@dataclass(frozen=True)
class DiffResult:
    """Outcome of diffing two documents.

    Attributes:
        marked_up: the LaTeX source with \\DIFadd/\\DIFdel markup.
        stats: change counts.
        edits: the full edit script (for tools/UIs on top).
    """

    marked_up: str
    stats: DiffStats
    edits: list[Edit]


def diff_documents(
    old_source: str,
    new_source: str,
    markup: LatexdiffMarkup = LatexdiffMarkup(),
    inject_preamble: bool = True,
) -> DiffResult:
    """Diff two LaTeX documents (flattened sources).

    Args:
        old_source: old revision LaTeX source.
        new_source: new revision LaTeX source.
        markup: markup style for additions/deletions.
        inject_preamble: insert the markup macro definitions
            (``\\DIFadd`` etc.) before ``\\begin{document}`` so the
            output compiles standalone; skipped when the document
            defines them already or has no document body.

    Returns:
        :class:`DiffResult` with the marked-up document.
    """
    # preamble policy: the output uses the NEW preamble; preamble
    # differences are counted, never marked up (see texdiff.preamble)
    pre_old, body_old, post_old = split_preamble(old_source)
    pre_new, body_new, post_new = split_preamble(new_source)
    preamble_changes = count_preamble_changes(old_source, new_source)

    # cross-revision context: added lines that already existed in the
    # old revision render black, not blue (refine-diff semantics)
    from . import oldlines

    oldlines.mark_old_lines(old_source)

    # cross-revision context: the flattened NEW source, for
    # order-sensitive pre-passes (retired-table hoisting must only
    # swap when the NEW revision places the retirement first)
    from . import newlines

    newlines.mark_new_lines(new_source)

    edits = _diff_nodes(parse(body_old), parse(body_new))
    body_markup = render(edits, markup)
    stats = _count(edits) + DiffStats(preamble_changes=preamble_changes)
    marked_up = pre_new + body_markup + post_new if pre_new else body_markup
    marked_up = _fix_listing_languages(marked_up)
    if pre_new:
        # change-record and other body-invoked preamble macros:
        # their typeset content would otherwise silently swallow
        # additions (preamble policy keeps the new revision as-is)
        marked_up = mark_preamble_macro_changes(marked_up, old_source, new_source)
    if inject_preamble and stats.changed:
        marked_up = _inject_preamble(marked_up, new_source)
    return DiffResult(marked_up=marked_up, stats=stats, edits=edits)


def _fix_listing_languages(marked_up: str) -> str:
    """Drop ``language=<lang>,`` from document-level ``\\lstset`` calls.

    The marked-up listings carry ``[alsolanguage=DIFcode]`` so the
    ``%DIF`` line markers render as strikeout/blue instead of literal
    text. But a primary language with C-style *block comments*
    (``/** ... */``) swallows the markers inside comment regions -
    listings stops processing delimiters once a block comment opens.
    The reference pipeline strips ``language=C++`` for exactly this
    reason; keyword colouring is a visual nicety, marker visibility
    is the point of a diff document.
    """
    import re

    return re.sub(r"(\\lstset\{)language=[A-Za-z0-9+#]*,", r"\1", marked_up)


def _inject_preamble(marked_up: str, source: str) -> str:
    """Insert PREAMBLE_TEMPLATE before ``\\begin{document}``.

    Follows latexdiff's convention; skips injection when the source
    already defines ``\\DIFadd`` or has no ``\\begin{document}```
    (a fragment - the caller provides definitions).
    """
    if "\\DIFadd" in source:
        return marked_up
    idx = marked_up.find("\\begin{document}")
    if idx < 0:
        return marked_up
    from .emit import PREAMBLE_TEMPLATE

    return marked_up[:idx] + PREAMBLE_TEMPLATE + marked_up[idx:]


def diff_files(
    old_path: str,
    new_path: str,
    flatten: bool = True,
    **kwargs,
) -> DiffResult:
    """Diff two LaTeX files (UTF-8).

    By default the sources are flattened first (``\\input``/``\\include``
    expanded), like ``latexdiff --flatten``; pass ``flatten=False`` to
    compare already-flattened sources.
    """
    from pathlib import Path

    def read(p):
        return Path(p).read_text(encoding="utf-8")

    old_source = flatten_file(old_path) if flatten else read(old_path)
    new_source = flatten_file(new_path) if flatten else read(new_path)
    return diff_documents(old_source, new_source, **kwargs)


def _diff_nodes(old: list[Node], new: list[Node]) -> list[Edit]:
    """Align, then recurse into modified pairable nodes.

    A ``Modify`` pair whose both sides have children is re-aligned at
    the next level down (e.g. a changed paragraph lives inside the
    ``document`` environment); the recursion result is carried on the
    ``Modify`` (``inner``) and rendered in place of the whole-block
    del/add replacement.

    A ``Modify`` pair of two *text* nodes is refined to a word-level
    diff so individual words - not whole paragraphs - get marked up.
    """
    edits = align(old, new)
    result: list[Edit] = []
    for edit in edits:
        if (
            isinstance(edit, Modify)
            and edit.old.children
            and edit.new.children
        ):
            inner = _diff_nodes(edit.old.children, edit.new.children)
            result.append(Modify(old=edit.old, new=edit.new, inner=inner))
        elif (
            isinstance(edit, Modify)
            and edit.old.kind == "text"
            and edit.new.kind == "text"
        ):
            # heavily rewritten paragraphs: interleaving word marks
            # between two sentences sharing less than half their words
            # reads as word salad. Such paragraphs retire wholesale -
            # delete + re-add, the track-changes convention - while
            # well-matched paragraphs of the same run keep their
            # word-level treatment.
            para_edits = _paragraph_edits(edit.old.text, edit.new.text)
            if para_edits is None:
                result.append(edit)
            else:
                result.extend(para_edits)
        else:
            result.append(edit)
    return result


def _run_similarity(a: str, b: str) -> float:
    """Word-level similarity of two text runs, in ``[0, 1]``.

    Tokens are compared with edge punctuation stripped, so
    ``text.`` matches ``text`` - the word differ itself works at
    that granularity and the retire/re-add decision must agree
    with it, or trivially reworded sentences (``Body text.`` to
    ``Body text changed.``) would look like rewrites.
    """
    from difflib import SequenceMatcher

    wa = [w for w in (t.strip(".,;:!?()\"'`") for t in a.split()) if w]
    wb = [w for w in (t.strip(".,;:!?()\"'`") for t in b.split()) if w]
    if not wa or not wb:
        return 0.0
    return SequenceMatcher(a=wa, b=wb, autojunk=False).ratio()


def _extends_by_words(short: str, long: str) -> bool:
    """Whether one run is the other with words appended (or removed).

    Text appended to a sentence (``... compression.`` growing into
    ``... compression: the file structure ...``) drives the word
    overlap ratio far below the replace threshold - the shared
    prefix is drowned by the additions - yet the change IS an edit
    of the same sentence: the word differ renders exactly the right
    thing (struck final period, inserted tail). Prefix/suffix word
    containment therefore overrides the ratio before a paragraph is
    judged a wholesale rewrite.
    """
    wa = [w for w in (t.strip(".,;:!?()\"'`") for t in short.split()) if w]
    wb = [w for w in (t.strip(".,;:!?()\"'`") for t in long.split()) if w]
    if len(wa) >= len(wb):
        wa, wb = wb, wa
    if not wa or len(wb) - len(wa) < 3:
        return False
    return wb[: len(wa)] == wa or wb[len(wb) - len(wa):] == wa


def _paragraph_edits(old: str, new: str) -> list[Edit] | None:
    """Word-refine a text run paragraph by paragraph.

    A text run spans everything up to the next macro/environment -
    often several paragraphs. Blank-line-separated paragraphs pair
    positionally; each pair either takes the word-level diff or, when
    the two paragraphs share less than half their words, the
    whole-paragraph delete + re-add. Whitespace around and between
    paragraphs keeps the NEW bytes - separator differences are
    invisible and must not produce marks.

    Returns ``None`` when paragraph counts do not match or the run
    is a wholesale rewrite: the caller then keeps the block-level
    ``Modify`` (whole-run retire + re-add).
    """

    def split_run(s: str) -> tuple[str, list[str], str]:
        lead = s[: len(s) - len(s.lstrip())]
        trail = s[len(s.rstrip()) :]
        core = s[len(lead) : len(s) - len(trail)]
        return lead, re.split(r"\n\s*\n", core), trail

    lead_a, paras_a, trail_a = split_run(old)
    lead_b, paras_b, trail_b = split_run(new)
    if len(paras_a) != len(paras_b) or any(
        not p.strip() for p in paras_a + paras_b
    ):
        # mismatched paragraph structure: whole-run judgement.
        # (A word-prefix run is still an edit of the same sentence,
        # never a rewrite - keep the word-level treatment.)
        return (
            None
            if _run_similarity(old, new) < _REPLACE_WORD_SIMILARITY
            and not _extends_by_words(old, new)
            else _chunks_to_edits(word_diff(old, new))
        )

    def sep(text: str) -> Edit:
        # paragraph separator, kept from the NEW side when it exists
        return Match(node=text_node(text if text.strip() else "\n\n"))

    # the lead is leading whitespace of the run (often empty): an
    # EMPTY lead must not fabricate a "\n\n" paragraph break that
    # neither revision had - it would typeset a blank line at the
    # start of the marked run (e.g. right after a list item's bold
    # label, splitting the item into "label." + "rest" lines)
    out: list[Edit] = [Match(node=text_node(lead_b or lead_a))]
    for k, (ca, cb) in enumerate(zip(paras_a, paras_b)):
        if (
            _run_similarity(ca, cb) < _REPLACE_WORD_SIMILARITY
            and not _extends_by_words(ca, cb)
        ):
            out.append(Delete(old=text_node(ca)))
            out.append(Insert(new=text_node(cb)))
        else:
            out.extend(_chunks_to_edits(word_diff(ca, cb)))
        if k + 1 < len(paras_a):
            out.append(sep("\n\n"))
    out.extend(_chunks_to_edits(word_diff(trail_a, trail_b)))
    return out


def _chunks_to_edits(chunks: list[Chunk]) -> list[Edit]:
    """Convert word-diff chunks to a flat list of atomic edits."""
    out: list[Edit] = []
    for chunk in chunks:
        if chunk.op == EQUAL:
            out.append(Match(node=text_node(chunk.text)))
        elif chunk.op == DELETE:
            out.append(Delete(old=text_node(chunk.text)))
        else:  # insert
            out.append(Insert(new=text_node(chunk.text)))
    return out

def _count(edits: list[Edit]) -> DiffStats:
    m = i = d = n = 0
    for e in edits:
        if isinstance(e, Match):
            m += 1
        elif isinstance(e, Modify):
            n += 1
        elif isinstance(e, Insert):
            i += 1
        elif isinstance(e, Delete):
            d += 1
    return DiffStats(matches=m, modifications=n, insertions=i, deletions=d)
