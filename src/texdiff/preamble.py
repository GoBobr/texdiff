"""Preamble handling: split, compare, and choose which revision wins.

Policy (v1):

* the marked-up output uses the **new** revision's preamble. The diff
  has to render with the current document styling; marking up
  preamble macro definitions with ``\\DIFadd``/``\\DIFdel`` would
  define/de-define packages mid-document and break compilation;
* preamble differences are counted (``DiffStats.preamble_changes``)
  rather than marked up, so callers can report "preamble changed"
  without risking a broken diff;
* fragments (no ``\\begin{document}``) have an empty preamble and are
  never touched.
"""

from __future__ import annotations

import difflib


def split_preamble(source: str) -> tuple[str, str, str]:
    """Split a document into (preamble, body, postscript).

    ``preamble`` includes everything up to and including the
    ``\\begin{document}`` line; ``postscript`` starts at
    ``\\end{document}`` and covers the rest; ``body`` is the
    in-between and parses standalone. A document without
    ``\\begin{document}`` is a fragment: empty preamble and post.
    """
    idx = source.find("\\begin{document}")
    if idx < 0:
        return "", source, ""
    end = idx + len("\\begin{document}")
    # include trailing spaces and ONE newline only when nothing but
    # whitespace follows on that line - glued documents
    # (\begin{document}body...) must keep the body as body
    rest_of_line = source[end : source.find("\n", end) if "\n" in source[end:] else len(source)]
    stripped = rest_of_line.strip()
    if not stripped or stripped.startswith("\\end{document}"):
        nl = source.find("\n", end)
        if nl >= 0:
            end = nl + 1
    pre = source[:end]

    endidx = source.find("\\end{document}", end)
    if endidx < 0:
        return pre, source[end:], ""
    return pre, source[end:endidx], source[endidx:]


def preamble_tuple(source: str) -> tuple[str, str]:
    """Two-tuple view for callers that ignore the body split."""
    pre, _body, post = split_preamble(source)
    return pre, post


def new_preamble(source: str) -> str:
    """The preamble used for the marked-up output (the new revision's)."""
    pre, _ = preamble_tuple(source)
    return pre


def count_preamble_changes(old_source: str, new_source: str) -> int:
    """Count changed preamble *lines* between the two revisions.

    Uses line-level ratio so reordering or reformatting counts as a
    change, but identical preambles count zero. Comments and blank
    lines are ignored (they carry no rendering semantics).
    """
    old_lines = _meaningful(old_source)
    new_lines = _meaningful(new_source)
    if not old_lines and not new_lines:
        return 0
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            changed += max(i2 - i1, j2 - j1)
    return changed


def _meaningful(source: str) -> list[str]:
    pre, _body, _post = split_preamble(source)
    out = []
    for line in pre.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        out.append(stripped)
    return out
