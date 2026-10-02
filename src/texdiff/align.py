"""Tree alignment: match two node lists into aligned pairs and edits.

The aligner is where latexdiff's heuristics become data. It produces,
for a pair of node lists, an edit script of three shapes:

* ``match(old, new)`` - same signature, identical text → keep verbatim
* ``modify(old, new)`` - same signature, different text → recurse if
  both sides have children (block diff), else whole-block replacement
* ``insert(new)`` / ``delete(old)`` - no counterpart

Uses :class:`difflib.SequenceMatcher` over node signatures; runs of
equal signature with different text become ``modify`` pairs aligned
positionally within the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from .nodes import Node


@dataclass(frozen=True)
class Match:
    """A node kept verbatim (identical on both sides)."""

    node: Node


@dataclass(frozen=True)
class Modify:
    """A node pair to be diffed further (recursed) - or replaced.

    Attributes:
        old: the node from the old revision.
        new: the node from the new revision.
        inner: the recursive edit script of the children, when both
            sides are recursable; ``None`` means whole-block replace.
    """

    old: Node
    new: Node
    inner: "list[Edit] | None" = None


@dataclass(frozen=True)
class Insert:
    """A node present only in the new version."""

    new: Node


@dataclass(frozen=True)
class Delete:
    """A node present only in the old version."""

    old: Node


Edit = Match | Modify | Insert | Delete


def align(old: list[Node], new: list[Node]) -> list[Edit]:
    """Align two node lists into an edit script.

    The returned script is in document order, interleaving old and new
    positions like a unified diff.

    Algorithm:

    1. an equal *prefix* and *suffix* of signatures anchors the
       alignment first: ``SequenceMatcher`` finds the globally
       longest matching block, which can let a later identical run
       steal the match from the earliest identical nodes - an
       inserted chapter between two near-identical passages then
       drags unchanged leading lines into the insertion (they
       render as added);
    2. ``SequenceMatcher`` over the remaining node *signatures*
       yields runs of same-signature positions deemed "matching";
    3. within a run, nodes are paired positionally: identical source
       text becomes :class:`Match`, differing text :class:`Modify`;
    4. gap regions become ``Delete`` (old-only) then ``Insert``
       (new-only) sequences, preserving document order.
    """

    def _pair(o: Node, n: Node) -> Edit:
        if o.text == n.text:
            return Match(node=o)
        if o.kind == "row" and n.kind == "row" and o.name == n.name:
            # equal row signature (content key) with differing text
            # means the difference is purely structural: \hline on
            # the other side of the row, whitespace, comments.
            # Duplicating such a pair (del + add) would emit the
            # table header material (\endfirsthead, \endhead ...)
            # twice inside one longtable, which can throw longtable
            # into an infinite loop. Keep the old row verbatim.
            return Match(node=o)
        return Modify(old=o, new=n)

    # 1. common prefix, paired positionally (identical to what a
    # matching-block run does - only the anchoring is stronger)
    pre = 0
    while (
        pre < len(old)
        and pre < len(new)
        and old[pre].signature() == new[pre].signature()
    ):
        pre += 1
    # common suffix (may not overlap the prefix)
    suf = 0
    while (
        suf < len(old) - pre
        and suf < len(new) - pre
        and old[len(old) - 1 - suf].signature() == new[len(new) - 1 - suf].signature()
    ):
        suf += 1
    mid_old = old[pre : len(old) - suf]
    mid_new = new[pre : len(new) - suf]

    sm = SequenceMatcher(
        a=[n.signature() for n in mid_old],
        b=[n.signature() for n in mid_new],
        autojunk=False,
    )

    edits: list[Edit] = [_pair(o, n) for o, n in zip(old[:pre], new[:pre])]
    prev_a = prev_b = 0
    for block in sm.get_matching_blocks():
        # gap before this matching block: deletions then insertions.
        # Block-head refinement: ``SequenceMatcher`` extends a matching
        # block as far as equal *signatures* reach - with the wildcard
        # ``text`` signature, a run may begin by pairing two text nodes
        # whose contents share nothing, while the node that should pair
        # with the old head sits at the START of the insertion gap just
        # before the block. When that pairing is clearly better, consume
        # the gap head into a Modify pair, emit the rest of the gap as
        # inserts, and let the block start one position later on both
        # sides (its first new-side node then lands at the gap end).
        new_gap = mid_new[prev_b : block.b]
        head_a, head_b = block.a, block.b
        if (
            block.size
            and new_gap
            and mid_old[block.a].kind == "text"
            and new_gap[0].kind == "text"
            and mid_new[block.b].kind == "text"
        ):
            o_head = mid_old[block.a]
            best = max(
                range(min(len(new_gap), 4)),
                key=lambda k: _text_similarity(o_head.text, new_gap[k].text),
            )
            n_head = new_gap[best]
            if _text_similarity(o_head.text, n_head.text) > (
                _text_similarity(o_head.text, mid_new[block.b].text) + 0.2
            ):
                edits.extend(Delete(old=n) for n in mid_old[prev_a : block.a])
                edits.extend(Insert(new=n) for n in new_gap[:best])
                edits.append(_pair(o_head, n_head))
                edits.extend(Insert(new=n) for n in new_gap[best + 1 :])
                edits.append(Insert(new=mid_new[block.b]))
                head_a, head_b = block.a + 1, block.b + 1
                for i in range(1, block.size):
                    edits.append(
                        _pair(mid_old[block.a + i], mid_new[block.b + i])
                    )
                prev_a, prev_b = block.a + block.size, block.b + block.size
                continue
        edits.extend(Delete(old=n) for n in mid_old[prev_a : block.a])
        edits.extend(Insert(new=n) for n in new_gap)
        # the matching run: pair positionally, decide Match vs Modify
        for i in range(head_a, head_a + block.size):
            edits.append(
                _pair(mid_old[i], mid_new[head_b + (i - head_a)])
            )
        prev_a, prev_b = head_a + block.size, head_b + block.size
    edits.extend(Delete(old=n) for n in mid_old[prev_a:])
    edits.extend(Insert(new=n) for n in mid_new[prev_b:])
    # common suffix, in document order
    edits.extend(
        _pair(o, n)
        for o, n in zip(
            old[len(old) - suf :], new[len(new) - suf :]
        )
    )
    return edits


def _text_similarity(a: str, b: str) -> float:
    """Quick content similarity of two text runs, in ``[0, 1]``.

    Used only to compare pairing candidates at matching-block
    boundaries, so a cheap ``SequenceMatcher.ratio`` over word
    tokens (not raw characters) is enough - and stays stable for
    long runs where character noise would dominate.
    """
    wa = [t for t in a.split() if t]
    wb = [t for t in b.split() if t]
    if not wa or not wb:
        return 0.0
    return SequenceMatcher(a=wa, b=wb, autojunk=False).ratio()
