"""CROSS-REVISION CONTEXT: ORDER IN THE NEW REVISION.

The aligner emits edits in old-anchored order, so an inserted table
can precede the edit that modifies/replaces a table that comes
BEFORE it in the new document. Rendering back the aligner's order
reorders the output relative to the new revision. This module gives
the emission side access to the flattened NEW source as one blob,
so order-sensitive pre-passes (the retired-table hoist) can check
which of two table regions comes first in the new document and only
swap when the new revision actually places the retired table first.
"""

from __future__ import annotations

# module-level mutable state (populated from api.diff_documents):
new_blob: str = ""


def mark_new_lines(source: str) -> None:
    """Record the flattened NEW revision as one contiguous blob."""
    global new_blob
    new_blob = source


def new_first(a: str, b: str) -> bool | None:
    """True when region ``a`` appears before region ``b`` in the new source.

    Legacy TEXT-SEARCH variant. Byte-identical regions (every
    "Global dimensions" longtable in the document) collapse to the
    FIRST occurrence, so later duplicates compare wrong - prefer
    :func:`node_first` whenever the actual new-side node is in hand.

    Returns ``None`` when either region is not locatable (identical
    duplicates collapse to the first occurrence - the caller then
    falls back to the legacy behaviour).
    """
    if not new_blob or not a or not b:
        return None
    ia = new_blob.find(a)
    ib = new_blob.find(b)
    if ia < 0 or ib < 0:
        return None
    return ia < ib


def node_first(a, b) -> bool | None:
    """True when new-revision node ``a`` precedes node ``b``.

    Compares the parser-recorded source offsets (``Node.pos``) of the
    two NEW-side nodes. Synthetically built nodes (``pos == -1``) are
    not locatable and yield ``None`` - the caller falls back.
    """
    pa = getattr(a, "pos", -1)
    pb = getattr(b, "pos", -1)
    if pa < 0 or pb < 0:
        return None
    return pa < pb
