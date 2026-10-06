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
import re


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


# \def\name{...} / \newcommand{\name}{...} definitions in the preamble
_DEF_RE = re.compile(r"\\(?:def|gdef|edef|xdef)\s*\\([a-zA-Z]+)\s*(?=\{)")
_NEWCOMMAND_RE = re.compile(r"\\(?:re)?newcommand\*?\s*\{\s*\\([a-zA-Z]+)\s*\}")
# a reference to the macro later in the document (used, not defined)
_USE_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _invoked_preamble_macros(pre: str, body: str) -> set[str]:
    """Preamble-defined macros that are used (invoked) in the body.

    Only definitions followed by a body invocation qualify: their
    content is typeset at the use site, so colouring inside their
    bodies is visible (and safe - the colour is scoped by whatever
    group the body wraps it in, e.g. each table cell).
    """
    defined = {m.group(1) for m in _DEF_RE.finditer(pre)}
    defined |= {m.group(1) for m in _NEWCOMMAND_RE.finditer(pre)}
    return {n for n in defined if re.search(rf"\\{n}\s*[^a-zA-Z]", body) or f"\\{n}" == body.strip()}


def _norm_line(line: str) -> str:
    """Normalised content of a macro-body line for comparison.

    Mirrors refine-diff.pl's ``norm``: latexdiff markers, control
    sequences, special characters and whitespace are dropped, so a
    line that merely absorbed a moved ``\\hline`` or closing brace
    still compares equal to its old counterpart. Only the *words*
    carry identity.
    """
    s = re.sub(r"%DIF.*$", "", line)
    s = re.sub(r"\\[a-zA-Z]+\s*", "", s)
    s = re.sub(r"[%\\{}\[\]&]", "", s)
    return re.sub(r"\s+", "", s)


def _changed_macro_lines(old_pre: str, new_pre: str, names: set[str]) -> list[str]:
    """Content lines present only in the new preamble macro bodies.

    A *changed macro line* is a line inside a ``\\name{...}``
    definition whose normalised content has no counterpart in the
    old revision's bodies of the same macros. Closures (``}`` alone)
    and pure structure do not count: they merely move when rows are
    appended.
    """
    old_norms = {_norm_line(l) for l in _macro_body_lines(old_pre, names)}
    changed: list[str] = []
    for line in _macro_body_lines(new_pre, names):
        if not line.strip() or _is_closing_line(line):
            continue
        if len(_norm_line(line)) < 4:
            continue
        if _norm_line(line) not in old_norms:
            changed.append(line)
    return changed


def _macro_body_lines(pre: str, names: set[str]) -> list[str]:
    """Lines belonging to the tracked macro definitions' bodies."""
    out: list[str] = []
    active = False
    depth = 0
    for line in pre.splitlines():
        started = False
        for name in names:
            if re.search(rf"\\(?:def\s*|gdef\s*|edef\s*|xdef\s*)\\{name}\s*\{{", line) or re.search(
                rf"\\(?:re)?newcommand\*?\s*\{{\s*\\{name}\s*\}}", line
            ):
                active = True
                started = True
        if active:
            out.append(line)
            depth += line.count("{") - line.count("}")
            if not started and depth <= 0:
                active = False
    return out


def _is_closing_line(line: str) -> bool:
    """A line that only closes a group / carries no content (``}``)."""
    return not re.sub(r"[{}\s%]", "", line)


def mark_preamble_macro_changes(
    marked_up: str, old_source: str, new_source: str
) -> str:
    """Colour added lines of body-invoked preamble macros (blue).

    Preamble macro definitions whose content is *typeset* via a use
    in the document body (a change-record table held in
    ``\\def\\name{...}`` and expanded with ``\\name``) would
    otherwise swallow their additions silently: the preamble policy
    emits the new preamble verbatim, so new rows of the change record
    render indistinguishable from the kept ones.

    For each such macro, lines of its body that are new (absent from
    the old revision's bodies of the same macros) get the same
    per-cell colour treatment the emitter uses for unsafe added runs
    - ``\\color{blue}`` re-started after each ``&``. Deleted lines are
    commented out (``%DIF <``, latexdiff preamble convention) so the
    old rows stay visible in the source without typesetting.

    The operation is textual and strictly contained in the preamble;
    it cannot affect compilation because ``\\color`` inside a macro
    body is scoped by the table cells of the typeset body.
    """
    pre_old, _body_old, _post_old = split_preamble(old_source)
    pre_new, body_new, _post_new = split_preamble(new_source)
    if not pre_new:
        return marked_up
    names = _invoked_preamble_macros(pre_new, body_new)
    # only macro bodies that are typeset as TABLES may carry the
    # per-cell colour markup: a \color is only meaningful - and only
    # safe - inside table cells. Plain macros (\newcommand{\issue})
    # change value between revisions; they keep the new value
    # silently, exactly like refine-diff.pl leaves them.
    names = {n for n in names if _body_is_table(pre_new, n)}
    if not names:
        return marked_up
    changed = _changed_macro_lines(pre_old, pre_new, names)
    # deleted lines live only in the OLD preamble: re-insert them as
    # comments at their old position next to the surviving lines
    deleted = _changed_macro_lines(pre_new, pre_old, names)
    if not changed and not deleted:
        return marked_up
    return (
        _apply_macro_markup(pre_old, pre_new, changed, deleted, names)
        + marked_up[len(pre_new) :]
    )


def _apply_macro_markup(
    old_pre: str,
    new_pre: str,
    changed: list[str],
    deleted: list[str],
    names: set[str],
) -> str:
    """Merge the old and new tracked macro bodies, marked per line.

    Emits the NEW preamble with, inside each tracked macro body:

    * genuinely new lines prefixed ``\\color{blue}`` per cell;
    * lines that existed only in the old body re-inserted at their
      original position as ``%DIF <`` comments (latexdiff preamble
      convention: invisible in the typeset output, reviewable in
      source).

    The merge aligns the old and new body lines on their normalized
    content (SequenceMatcher over normalized lines): equal lines are
    kept verbatim, old-only lines become comments, new-only lines
    are coloured blue.
    """
def _apply_macro_markup(
    old_pre: str,
    new_pre: str,
    changed: list[str],
    deleted: list[str],
    names: set[str],
) -> str:
    """Merge the old and new tracked macro bodies, marked per line.

    Emits the NEW preamble with, inside EACH tracked macro body:

    * genuinely new lines prefixed ``\\color{blue}`` per cell;
    * lines that existed only in the old body re-inserted at their
      original position as ``%DIF <`` comments (latexdiff preamble
      convention: invisible in the typeset output, reviewable in
      source).

    Each macro's old and new body lines are aligned on their
    normalized content (SequenceMatcher): equal lines kept verbatim,
    old-only lines commented, new-only lines coloured blue. Bodies
    are spliced back in reverse document order so earlier line
    indices stay valid.
    """
    pre_out = new_pre.splitlines()
    spliced = False
    for name, rng in _body_ranges(new_pre, names):
        new_body = _macro_body(new_pre, name)
        old_body = _macro_body(old_pre, name)
        sm = difflib.SequenceMatcher(
            a=[_norm_line(l) for l in old_body],
            b=[_norm_line(l) for l in new_body],
            autojunk=False,
        )
        merged: list[str] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                # equal ranges are index-aligned to the OLD body
                # (i1/i2) and the NEW body (j1/j2) separately; the
                # kept bytes must come from the NEW revision, so the
                # slice runs over the NEW indices. Using i1/i2 here
                # re-emits earlier new-side lines (a duplicated
                # table row) and silently drops the tail of the
                # body - including the macro's closing brace.
                merged.extend(new_body[j1:j2])
            elif tag == "delete":
                merged.extend(f"%DIF < {l}" for l in old_body[i1:i2])
            elif tag == "insert":
                merged.extend(_color_line(l) for l in new_body[j1:j2])
            else:  # replace
                merged.extend(f"%DIF < {l}" for l in old_body[i1:i2])
                merged.extend(_color_line(l) for l in new_body[j1:j2])
        if merged != new_body or len(merged) != len(new_body):
            pre_out[rng[0] : rng[1]] = merged
            spliced = True
        elif merged == new_body and _body_changed(old_body, new_body):
            # identical render but changes exist (colours dropped):
            # still splice to carry them
            pre_out[rng[0] : rng[1]] = merged
            spliced = True
    if not spliced:
        return new_pre
    result = "\n".join(pre_out)
    return result + ("\n" if new_pre.endswith("\n") else "")


def _body_changed(old_body: list[str], new_body: list[str]) -> bool:
    """True when the bodies differ on normalized content."""
    return [_norm_line(l) for l in old_body] != [_norm_line(l) for l in new_body]


def _body_is_table(pre: str, name: str) -> bool:
    """True when a macro's body contains table rows (cells + row ends).

    The criterion is structural: at least one unescaped ``&`` cell
    separator AND at least one ``\\\\`` row terminator anywhere in
    the body. Change-record tables (``v3 & date & id & text \\\\``)
    satisfy it; scalar macros like ``\\newcommand{\\issue}{v2}`` do
    not.
    """
    body = "\n".join(_macro_body(pre, name))
    stripped = _strip_comment_line_wise(body)
    has_cells = bool(re.search(r"(?<!\\)&", stripped))
    has_rows = "\\\\" in stripped
    return has_cells and has_rows


def _strip_comment_line_wise(text: str) -> str:
    """Remove ``%`` comments from each line (but keep escaped ``\\%``)."""
    return "\n".join(_strip_comment(l) for l in text.splitlines())


def _body_ranges(pre: str, names: set[str]) -> list[tuple[str, tuple[int, int]]]:
    """(name, (start, end)) line ranges of tracked macro bodies.

    Returned in document order. Bodies are brace-delimited and thus
    disjoint; a definition not found yields no range.
    """
    lines = pre.splitlines()
    out: list[tuple[str, tuple[int, int]]] = []
    start = None
    depth = 0
    for idx, line in enumerate(lines):
        if start is None:
            for name in names:
                if re.search(
                    rf"\\(?:def\s*|gdef\s*|edef\s*|xdef\s*)\\{name}\s*\{{", line
                ) or re.search(
                    rf"\\(?:re)?newcommand\*?\s*\{{\s*\\{name}\s*\}}", line
                ):
                    start = (name, idx)
                    depth = _brace_delta(_strip_comment(line))
                    if depth <= 0:
                        out.append((name, (idx, idx + 1)))
                        start = None
                    break
        else:
            depth += _brace_delta(_strip_comment(line))
            if depth <= 0:
                out.append((start[0], (start[1], idx + 1)))
                start = None
    if start is not None:
        out.append((start[0], (start[1], len(lines))))
    return out


def _macro_body(pre: str, name: str) -> list[str]:
    """Lines of ONE tracked macro's definition body."""
    for _name, rng in _body_ranges(pre, {name}):
        if _name == name:
            return pre.splitlines()[rng[0] : rng[1]]
    return []

def _brace_delta(line: str) -> int:
    """Net brace depth change of a line (comments removed)."""
    return line.count("{") - line.count("}")


def _strip_comment(line: str) -> str:
    """Drop a trailing LaTeX comment (``%`` to end of line)."""
    return re.sub(r"(?<!\\)%.*$", "", line)


def _color_line(line: str) -> str:
    """Prepend \\color{blue} per table cell (after every &)."""
    line = f"\\color{{blue}} {line}"
    return re.sub(r"(?<!\\)&", r"& \\color{blue} ", line)
