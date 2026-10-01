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

    1. ``SequenceMatcher`` over node *signatures* yields runs of
       same-signature positions deemed "matching";
    2. within a run, nodes are paired positionally: identical source
       text becomes :class:`Match`, differing text :class:`Modify`;
    3. gap regions become ``Delete`` (old-only) then ``Insert``
       (new-only) sequences, preserving document order.
    """
    sm = SequenceMatcher(
        a=[n.signature() for n in old],
        b=[n.signature() for n in new],
        autojunk=False,
    )

    edits: list[Edit] = []
    prev_a = prev_b = 0
    for block in sm.get_matching_blocks():
        # gap before this matching block: deletions then insertions
        edits.extend(Delete(old=n) for n in old[prev_a : block.a])
        edits.extend(Insert(new=n) for n in new[prev_b : block.b])
        # the matching run: pair positionally, decide Match vs Modify
        for i in range(block.size):
            o, n = old[block.a + i], new[block.b + i]
            if o.text == n.text:
                edits.append(Match(node=o))
            elif o.kind == "row" and n.kind == "row" and o.name == n.name:
                # equal row signature (content key) with differing text
                # means the difference is purely structural: \hline on
                # the other side of the row, whitespace, comments.
                # Duplicating such a pair (del + add) would emit the
                # table header material (\endfirsthead, \endhead ...)
                # twice inside one longtable, which can throw longtable
                # into an infinite loop. Keep the old row verbatim.
                edits.append(Match(node=o))
            else:
                edits.append(Modify(old=o, new=n))
        prev_a, prev_b = block.a + block.size, block.b + block.size
    return edits
