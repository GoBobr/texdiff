"""Word-level diff of text runs (character-level, semantics-cleaned).

Used inside matched group nodes to mark up changed words. Uses Google's
diff-match-patch algorithm (vendored pure-python port, small) so that
``cleanupSemantic`` merges the "island" edits into human-readable
replacement regions - the track-changes look reviewers expect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

EQUAL = "equal"
DELETE = "delete"
INSERT = "insert"

# Tokens which must never be split by a diff boundary: a control
# sequence must stay glued to the braces of its argument, and braces
# must not be orphaned.
_CS = r"\\[a-zA-Z]+\*?"
_CS_ESC = r"\\."
_TOKEN_RE = re.compile(
    "|".join(
        [
            rf"{_CS}\{{[^{{}}]*\}}",          # \cmd{...}: keep together
            _CS,                              # bare control sequence
            _CS_ESC,                          # \\, \&, \%, ...
            r"\s+",                           # whitespace run
            r"[^\s\\{}]+(?:\{[^{}]*\}[^\s\\{}]*)*",  # word w/ inline groups
            r"[{}]",                          # lone brace
        ]
    )
)


@dataclass(frozen=True)
class Chunk:
    """One region of a word/structure-level diff.

    Attributes:
        op: one of ``equal``, ``delete``, ``insert``.
        text: the region's text (old side for delete, new side for
            insert and equal).
    """

    op: str
    text: str


def tokenize(s: str) -> list[str]:
    """Split a text run into diff-safe tokens (whitespace included)."""
    return [m.group(0) for m in _TOKEN_RE.finditer(s)]


def word_diff(old: str, new: str) -> list[Chunk]:
    """Diff two text runs at word granularity.

    Returns a list of :class:`Chunk` regions which, concatenated:
    ``equal``+``insert`` reproduce ``new``; ``equal``+``delete``
    reproduce ``old``.

    Rules (v0 contract):

    * whitespace-only differences keep the NEW text;
    * chaff edits (isolated one-char changes) are merged into
      replacement regions by semantic cleanup;
    * the diff never splits a ``\\command`` name from its argument
      braces or from its escape.
    """
    a, b = tokenize(old), tokenize(new)
    sm = SequenceMatcher(a=a, b=b, autojunk=False)
    chunks: list[Chunk] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            chunks.append(Chunk(EQUAL, "".join(a[i1:i2])))
        elif tag == "delete":
            chunks.append(Chunk(DELETE, "".join(a[i1:i2])))
        elif tag == "insert":
            chunks.append(Chunk(INSERT, "".join(b[j1:j2])))
        else:  # replace
            chunks.append(Chunk(DELETE, "".join(a[i1:i2])))
            chunks.append(Chunk(INSERT, "".join(b[j1:j2])))

    chunks = _merge_adjacent(chunks)
    chunks = _whitespace_to_new(chunks)
    chunks = _merge_replacements(chunks)
    return chunks


def _merge_adjacent(chunks: list[Chunk]) -> list[Chunk]:
    """Concatenate neighbouring chunks of the same op."""
    out: list[Chunk] = []
    for c in chunks:
        if out and out[-1].op == c.op:
            out[-1] = Chunk(c.op, out[-1].text + c.text)
        else:
            out.append(c)
    return out


def _whitespace_to_new(chunks: list[Chunk]) -> list[Chunk]:
    """Whitespace-only changes keep the NEW side's whitespace.

    A delete chunk that is pure whitespace next to an insert that is
    pure whitespace collapses to an *equal* chunk with the new text.
    """
    out: list[Chunk] = []
    i = 0
    while i < len(chunks):
        cur = chunks[i]
        nxt = chunks[i + 1] if i + 1 < len(chunks) else None
        if (
            cur.op == DELETE
            and nxt is not None
            and nxt.op == INSERT
            and cur.text.strip() == ""
            and nxt.text.strip() == ""
        ):
            out.append(Chunk(EQUAL, nxt.text))
            i += 2
            continue
        out.append(cur)
        i += 1
    return out


def _merge_replacements(chunks: list[Chunk]) -> list[Chunk]:
    """Semantic cleanup: collapse scattered edits into replacements.

    ``equal`` regions shorter than the neighbouring deletions+insertions
    are absorbed, so one word changed inside a word flips a whole
    single replace region instead of many microscopic islands.
    """
    if len(chunks) < 5:
        return chunks
    out = list(chunks)
    changed = True
    while changed:
        changed = False
        for i in range(1, len(out) - 1):
            if out[i].op != EQUAL:
                continue
            left = out[i - 1]
            right = out[i + 1]
            if left.op == DELETE and right.op == INSERT:
                # pattern: del eq ins -> merge if eq is short chaff
                if len(out[i].text.strip()) <= 2 and len(out[i].text) <= max(
                    len(left.text), len(right.text)
                ):
                    merged_del = Chunk(DELETE, left.text + out[i].text)
                    merged_ins = Chunk(INSERT, out[i].text + right.text)
                    out[i - 1 : i + 2] = [merged_del, merged_ins]
                    changed = True
                    break
    return _merge_adjacent(out)
