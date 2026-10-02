"""CROSS-REVISION CONTEXT FOR MARKUP EMISSION.

refine-diff semantics: an added line is only "genuinely new" - and
rendered blue - when its normalised content did not already exist in
the OLD revision of the document. Re-emitted table cells (makecell
bodies that moved between rows, reformatted config listings) carry
lines that existed verbatim on the old side; marking those blue makes
the diff noisy and wrong. This module provides global access to a set
of all old-revision line norms for consult during rendering, sparing
us a large refactor to thread it through the render call chain.
"""

from __future__ import annotations

import re

# module-level mutable state (populated from api.diff_documents):
# the old revision as one blob of concatenated line norms - refine-diff
# matches by SUBSTRING containment, so a re-emitted row line whose old
# counterpart carries extra trailing structure (``... \\ \\hline``)
# still counts as existing
old_blob: str = ""
old_lines: set[str] = set()


def norm_line(line: str) -> str:
    """Normalise a source line the way the reference build does."""
    clean = re.sub(r"%DIF.*$", "", line)
    clean = re.sub(r"[%\\{}\[\]&]", "", clean)
    return re.sub(r"\s+", "", clean)


def mark_old_lines(source: str) -> None:
    """Record all content line norms of the OLD revision."""
    global old_blob, old_lines
    norms = [norm_line(line) for line in source.split("\n")]
    old_lines = {n for n in norms if len(n) >= 10}
    old_blob = "".join(n + "\n" for n in norms if len(n) >= 10)


def in_old(line: str) -> bool:
    """True when a normalised version of `line` existed in the old revision."""
    norm = norm_line(line)
    return len(norm) >= 10 and norm in old_blob


def is_struct_line(line: str) -> bool:
    """Bare structure lines never take a colour declaration."""
    return bool(re.match(r"^\s*\\(?:rowcolor|hline|begin|end|caption)\b", line))
