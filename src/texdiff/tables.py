"""Wholesale rendering of heavily restructured tables.

Ports the behaviour of the reference ``sanitize-tables.pl`` toolkit to
generic texdiff: when one logical table's content changed so much that
row-granular markup is noise (80 %+ row churn), the change reads best
as *two whole tables* - the old revision's table struck through in
red, the new revision's in blue. latexdiff's own row markup for such
configurations produces pathological glue; the reference build
solves that by locating each clean source table and rendering it
wholesale.

Components:

* :func:`is_restructured` - classify an old/new table-text pair by
  data-row churn (the reference thresholds: >= 80 % relative
  difference AND >= 3 rows apart);
* :func:`is_pathological` - inline row markup of a pair would be
  unreadable interleaving (word similarity below
  :data:`PATHOLOGICAL_WORD_SIMILARITY`); such pairs take the
  wholesale or row-merged rendering instead;
* :func:`merge_tables` - the row-merged rendering: one table, common
  rows black, removed rows red-struck, added rows blue (the reference
  ``merged_rows``, LCS over normalized rows with the 0.3 / 0.5 / 0.5
  thresholds);
* :func:`render_restructured` - the wholesale renderer: the old side
  as :func:`strike_through` (red, per-word so wide W{} cells can
  still wrap), the new side unmarked inside a blue colour group.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# reference thresholds (sanitize-tables.pl): a table pair is
# "restructured" when the data-row counts differ by >= 80 % relative
# AND >= 3 absolute rows
RETIRE_DEL_FRAC = 0.80
RETIRE_MIN_ROWS = 3

# when the word-level similarity of the data rows falls below this,
# per-cell inline markup degenerates into an unreadable red/blue
# interleaving - the reference build calls such blocks "pathological"
# and replaces them wholesale (calibrated so well-matching tables
# like hand-edited ICD longtables, similarity ~ 0.97, keep their
# inline markup)
PATHOLOGICAL_WORD_SIMILARITY = 0.50

# row-merge acceptance (reference): the LCS must pair >= 30 % of the
# smaller row set, <= 50 % of the old rows may stay unpaired, and a
# merge is rejected when >= 50 % of the unmatched old rows reappear
# verbatim among the unmatched new rows
MIN_MATCH_FRAC = 0.30
MAX_UNMATCHED_FRAC = 0.50
REAPPEAR_FRAC = 0.50

_MAX_SINGLE_PAGE_ROWS = 25  # more rows -> multi-page rendering path
_MAX_SINGLE_PAGE_LINES = 120

# marker comment the emit-side retired-table detection keys off: a
# restructured pair renders within ONE render call, so only the emit
# side (a lone Delete before a lone Insert) needs marker scanning
RESTRUCTURED_MARKER = "% texdiff: restructured table - new version from source (blue)\n"

_ROW_LINE_RE = re.compile(r"(^|[^\\])&(?!\\)|\\\\")
_STRUCT_BEGIN_RE = re.compile(
    r"^\s*\\(?:hline|begin\{longtable|end\{longtable|begin\{tabular|end\{tabular)"
)
_HEADFOOT_RE = re.compile(r"\\end(first|last)?(head|foot)")


def data_rows(text: str) -> int:
    """Count data rows of a (clean source) table text.

    Non-structure, non-comment lines holding a cell separator or a
    row terminator; mirrors ``data_rows_source`` of the reference.
    """
    n = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        if _STRUCT_BEGIN_RE.match(stripped):
            continue
        if _HEADFOOT_RE.search(line):
            continue
        if _ROW_LINE_RE.search(line):
            n += 1
    return n


def is_restructured(old_text: str, new_text: str) -> bool:
    """True when a table pair is too far apart for row-granular markup.

    Either side missing counts: an old table without a new counterpart
    is retired, a new without an old is new - both take the wholesale
    rendering too. For two present tables the reference thresholds
    decide (>= 80 % relative data-row difference, >= 3 rows apart).
    """
    n_old = data_rows(old_text) if old_text else 0
    n_new = data_rows(new_text) if new_text else 0
    if n_old == 0 or n_new == 0:
        return True
    diff = abs(n_old - n_new)
    larger = max(n_old, n_new)
    return diff >= RETIRE_MIN_ROWS and diff / larger >= RETIRE_DEL_FRAC


# --- old-side renderer: strike every word, keep structure ------------------

_LIST_TOKEN_RE = re.compile(r"(\\begin\{itemize\}|\\end\{itemize\}|\\item\b|\\t\b)")
_BREAKPOINT_RE = re.compile(r"(\\-\\_?\\allowbreak|\\allowbreak|\\-\\_|\\-|\\_|(?<!\\)_)")


def _strike_word(word: str) -> str:
    """Strike one word, inserting breakpoints between struck segments.

    ``\\sout`` makes its argument unbreakable, so a whole-cell strike
    in a narrow ``W{}`` column can never wrap - the reference strikes
    per word and re-opens the strike after each breakpoint token
    (``\\_``, ``\\-``, ``\\allowbreak`` ...). List-environment tokens
    (``\\item`` ...) stay outside: ulem's LR mode rejects them.
    """
    if word.startswith("\\\\") or word == "&":
        return word
    if re.match(r"^\\(?:rowcolor|hline|cline|multicolumn|multirow)", word):
        return word
    if _LIST_TOKEN_RE.search(word):
        parts = _LIST_TOKEN_RE.split(word)
        out = []
        for seg in parts:
            if not seg:
                continue
            if _LIST_TOKEN_RE.fullmatch(seg):
                out.append("  " if seg == "\\t" else seg)
            else:
                out.append(_strike_word(seg))
        return "".join(out)
    pieces = _BREAKPOINT_RE.split(word)
    out = []
    depth = 0
    for piece in pieces:
        if not piece:
            continue
        if _BREAKPOINT_RE.fullmatch(piece):
            if depth:
                out.append("}")
                depth -= 1
            # a literal (unescaped) ``_`` arrives here as a
            # breakpoint piece; emitted bare it typesets as a math
            # subscript OUTSIDE the strike - unbreakable, and it
            # pushes the row into the neighbouring cell. Emit the
            # escaped form, which is both breakable and safe text.
            bp = "\\_" if piece == "_" else piece
            out.append(bp + "\\allowbreak ")
            out.append("\\sout{")
            depth += 1
        else:
            if not depth:
                out.append("\\sout{")
                depth += 1
            out.append(piece)
    if depth:
        out.append("}")
    return "".join(out)


def _add_breakpoints(s: str) -> str:
    """Let long tokens wrap: ``\\_`` gets ``\\allowbreak`` after it.

    Skips comments and package/filename arguments, whose corruption
    would break file references; comma breaking of the reference is
    not ported (texdiff emits generated tables whose cells are short).
    """
    if s.lstrip().startswith("%"):
        return s
    if re.search(r"\\(?:documentclass|usepackage|RequirePackage|textattachfile|path|includegraphics|input|include)\s*[\[{]", s):
        return s
    return re.sub(r"(\\_)(?!\s*\\allowbreak)", r"\1\\allowbreak ", s)


# a full \caption{...} call; the body must be brace-balanced on ONE
# line (captions in generated spec tables always are)
_CAPTION_RE = re.compile(r"(\\caption)(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})")


def strike_through(text: str) -> str:
    """Render a clean old table text red-struck, per word.

    Data-row lines get every word struck (terminator ``\\\\`` stays
    outside the strikes); structure lines (``\\hline``, environment
    boundaries), blank lines and header/footer markers pass through
    untouched. Wrapped-row continuation lines are struck as well so
    long attribute tails do not render as live text.
    """
    out = []
    in_row = False
    lines = _fix_cell_counts(_add_breakpoints(text)).split("\n")
    for line in lines:
        # caption line: a retired table's caption typesets struck like
        # its rows but must stay transparent to numbering - it neither
        # steps the table counter nor writes a list-of-tables entry,
        # so the blue replacement keeps the number the retired table
        # would have taken (captions *of and only of* retired tables
        # pass through strike_through; live ones never do)
        mcap = _CAPTION_RE.search(line)
        if mcap:
            begin, body = mcap.groups()
            out.append(
                line[: mcap.start()]
                # empty optional argument: the caption typesets (with
                # its Table N: label, struck like the table body) but
                # writes NO list-of-tables entry; the immediate
                # \addtocounter{table}{-1} then gives the number back,
                # so the blue replacement caption takes the very
                # number the retired table carried - they read as one
                # logical "Table N (old) -> Table N (new)" pair
                + begin
                + "[]{\\texorpdfstring{"
                + _strike_word(body[1:-1])
                + "}{}"
                + "}"
                + "\\addtocounter{table}{-1}% texdiff: "
                "retired caption - struck, unnumbered, counter-neutral\n"
                + line[mcap.end() :]
            )
            continue
        if _STRUCT_BEGIN_RE.match(line) or not line.strip() or _HEADFOOT_RE.search(line):
            out.append(line)
            continue
        if _ROW_LINE_RE.search(line):
            m = re.search(r"\s*(\\\\)\s*$", line)
            term = ""
            row = line
            if m:
                term = m.group(1) + "\n"
                row = line[: m.start()]
            struck = re.sub(r"\S+", lambda m2: _strike_word(m2.group(0)), row)
            out.append(struck)
            out.append(term)
            in_row = True
            continue
        if in_row and line.strip() and not line.lstrip().startswith("\\"):
            out.append(re.sub(r"\S+", lambda m2: _strike_word(m2.group(0)), line))
            continue
        out.append(line)
    return "".join(x if x.endswith("\n") else x + "\n" for x in out)


# plane implementation of the reference's cell-count repair: the old
# hand-authored tables occasionally hold rows with one cell too few;
# the restructured rendering has no latexdiff glue to fix, so a plain
# pass keeps the column count of the spec
def _fix_cell_counts(text: str) -> str:
    """No-op placeholder keeping the reference call graph shape.

    The reference repairs latexdiff's glue rows; texdiff renders the
    clean source directly, whose rows already match the spec (round
    trip verified on parse). Returns the input unchanged.
    """
    return text


def render_restructured(old_text: str, new_text: str) -> str:
    """Render a restructured pair: old red-struck, then new in blue.

    Both sides are the clean revision sources (no markup); the old
    side is per-word struck through inside a red colour group, the
    new side emitted verbatim inside a blue one - the reference build's
    "restructured (multi-page)" and "restructured new" rendering.
    """
    parts = ["{\\color{red}\n", strike_through(old_text), "}\n"]
    if new_text:
        parts += [
            RESTRUCTURED_MARKER,
            "{\\color{blue}\n",
            new_text,
            "}\n",
        ]
    return "".join(parts)


# --- pathology and row merging ---------------------------------------------


def _data_row_lines(text: str) -> list[str]:
    """Data-row physical lines of a clean table text."""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        if _STRUCT_BEGIN_RE.match(s):
            continue
        if _HEADFOOT_RE.search(line):
            continue
        if _ROW_LINE_RE.search(line):
            out.append(line)
    return out


def is_pathological(old_text: str, new_text: str) -> bool:
    """True when inline row markup of the pair would be noise.

    A word-level similarity of the data-row content below
    :data:`PATHOLOGICAL_WORD_SIMILARITY` means almost every cell is
    rewritten; per-cell markup then interleaves red and blue fragments
    line upon line. Such pairs render better as wholesale tables (or
    a row-merged single table when enough rows still match).
    """
    wo = " ".join(_data_row_lines(old_text)).split()
    wn = " ".join(_data_row_lines(new_text)).split()
    if not wo or not wn:
        return False
    ratio = SequenceMatcher(a=wo, b=wn, autojunk=False).ratio()
    return ratio < PATHOLOGICAL_WORD_SIMILARITY


# first data cell of a row, bracket/brace aware, macro lead stripped
def _first_cell(row: str) -> str:
    """Normalized first cell (the row's key) of a table row line."""
    # drop the row terminator and any rowcolor/hline lead
    r = re.sub(r"\\\\\s*$", "", row)
    r = re.sub(r"^\\(?:rowcolor\s*\{[^{}]*\}|hline|cline\s*\{[^{}]*\})\s*", "", r.strip())
    cell = []
    depth = 0
    i = 0
    while i < len(r):
        c = r[i]
        if c == "\\" and i + 1 < len(r):
            cell.append(r[i : i + 2])
            i += 2
            continue
        if c == "&" and depth == 0:
            break
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        cell.append(c)
        i += 1
    key = "".join(cell)
    # unwrap \textbf{...}/\emph{...}/\texttt{...} wrappers so a
    # labelled row equals its plain form
    m = re.fullmatch(r"\\(?:textbf|emph|texttt|textit)\{(.*)\}", key, re.S)
    if m:
        key = m.group(1)
    return re.sub(r"\s+", " ", key.replace("\\_", "_")).strip()


def rows_pair_by_key(old_text: str, new_text: str, min_frac: float = 0.5) -> bool:
    """True when the data rows of two tables pair by first-cell key.

    Regenerated spec tables rewrite whole attribute cells (a new NCML
    generation changes type names, shapes and every attribute), so
    word similarity collapses and the pair looks mutually
    unrecognizable - while the logical row structure (one row per
    variable: ``lat``, ``lon``, ``crs``, ...) is intact. When at least
    ``min_frac`` of the smaller side's rows have a first cell that
    also appears as a first cell on the other side, the table is the
    SAME table with edited cells: inline row markup (red struck cell,
    blue replacement right after) reads far better than retiring the
    whole table and reintroducing it blue.
    """
    lo = [
        _first_cell(r)
        for r in _data_row_lines(old_text)
        if _first_cell(r)
    ]
    ln = [
        _first_cell(r)
        for r in _data_row_lines(new_text)
        if _first_cell(r)
    ]
    if not lo or not ln:
        return False
    # structural repeats (header rows repeated via \endhead) must not
    # count twice per side
    lo, ln = set(lo), set(ln)
    # grid renames (NPP_Grid_IMG_2D -> VIIRS_Grid_IMG_2D) change the
    # path prefix of every HDF-EOS field row without touching the
    # data field itself: rows also pair when their last path
    # component matches
    def _last(k: str) -> str:
        return k.rsplit("/", 1)[-1] if "/" in k else k
    lo_last, ln_last = {_last(k) for k in lo}, {_last(k) for k in ln}
    shared = len(lo_last & ln_last)
    return shared >= min_frac * min(len(lo_last), len(ln_last)) and shared >= 2


_SPEC_RE = re.compile(r"\\begin\{longtable\*?\}\s*\{")

_STRUCT_TAIL_RE = re.compile(
    r"\n[ \t]*(\\(?:hline|cline\s*\{[^{}]*\})(?:[ \t]+\\(?:hline|cline\s*\{[^{}]*\}))*)[ \t]*(?=\n)"
)
_ROW_TERM_RE = re.compile(r"(?:^|\n)([^\n]*?\\\\)", re.M)


def _table_spec(text: str) -> str | None:
    """Column spec of a longtable (brace-balanced, one nesting level)."""
    m = _SPEC_RE.search(text)
    if m is None:
        return None
    start = m.end() - 1
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _split_table_rows(text: str) -> tuple[str, list[str], str]:
    """Split a table into (pre, rows, post); rows end at their ``\\\\``.

    A row runs from its line start to the terminating ``\\\\``,
    absorbing the standalone structure lines (``\\hline`` /
    ``\\cline``) that directly follow it plus one blank line - that
    keeps the horizontal borders attached to the right row.
    """
    rows: list[str] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    while True:
        m = _ROW_TERM_RE.search(text, pos)
        if m is None:
            break
        start = m.start(1)
        end = m.end(1)
        # absorb following standalone \hline / \cline lines + one blank
        while True:
            t = _STRUCT_TAIL_RE.match(text, end)
            if t is None:
                break
            end = t.end()
        if text.startswith("\n", end):
            end += 1
        rows.append(text[start:end])
        spans.append((start, end))
        pos = end
    if not spans:
        return text, [], ""
    pre = text[: spans[0][0]]
    post = text[spans[-1][1] :]
    return pre, rows, post


def _row_is_data(row: str) -> bool:
    """False for structure-only rows (lone \\hline etc.)."""
    t = re.sub(r"\\\\\s*$", "", row)
    t = t.replace("\\hline", "")
    t = re.sub(r"\s+", "", t)
    return len(t) > 0


def _norm_row(row: str) -> str:
    """Normalization for row matching (breakpoints/underscores/ws)."""
    r = row.replace("\\-", "")
    r = r.replace("\\_", "_")
    r = re.sub(r"\\allowbreak\s*", "", r)
    r = re.sub(r"\s+", " ", r)
    return r.strip()


def _lcs_pairs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Longest common subsequence pairs of two key lists (DP)."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = (
                dp[i + 1][j + 1] + 1
                if a[i] == b[j]
                else max(dp[i + 1][j], dp[i][j + 1])
            )
    pairs = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _color_row(row: str, color: str) -> str:
    """Wrap one source row's cells in a colour group, red struck.

    Lead (``\\rowcolor`` etc.), the ``\\\\`` terminator and trailing
    structure lines stay outside the colour groups: a brace group may
    not span the alignment separator ``&``, and row colours must be
    given right after ``\\\\``. Each cell is coloured (and, for red,
    struck word-wise) individually.
    """
    tail = ""
    while True:
        t = re.search(r"\n[ \t]*(\\(?:hline|cline\s*\{[^{}]*\}|rowcolor\s*\{[^{}]*\})[^\n]*)\s*$", row)
        if t is None:
            break
        row = row[: t.start()]
        tail = "\n" + t.group(1) + tail
    m = re.search(r"\s*(\\\\)\s*$", row)
    term = ""
    if m:
        term = m.group(1)
        row = row[: m.start()]
    lead = ""
    while True:
        m = re.match(r"\s*(\\rowcolor\s*\{[^{}]*\}|\\hline|\\cline\s*\{[^{}]*\})\s*(.*)$", row, re.S)
        if m is None:
            break
        lead += m.group(1) + " "
        row = m.group(2)
    cells: list[str] = []
    depth = 0
    cur = []
    i = 0
    while i < len(row):
        c = row[i]
        if c == "\\" and i + 1 < len(row):
            cur.append(row[i : i + 2])
            i += 2
            continue
        if c == "&" and depth == 0:
            cells.append("".join(cur))
            cur = []
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        cur.append(c)
        i += 1
    cells.append("".join(cur))
    out = []
    for cell in cells:
        if color == "red":
            cell = re.sub(r"\S+", lambda m2: _strike_word(m2.group(0)), cell)
        out.append("{\\color{" + color + "}" + cell + "}")
    return lead + " & ".join(out) + term + tail + "\n"


def merge_tables(old_text: str, new_text: str) -> str | None:
    """Row-merged rendering of a matched pair, or None when impossible.

    One longtable: common rows black (new variant), removed rows
    red-struck, added rows blue - reading like the classic diff while
    keeping the layout. Returns None when the merge would be mostly
    noise (reference acceptance thresholds).
    """
    if _table_spec(old_text) != _table_spec(new_text):
        return None
    pre_old, rows_old, post_old = _split_table_rows(old_text)
    pre_new, rows_new, post_new = _split_table_rows(new_text)

    idx_old = [i for i, r in enumerate(rows_old) if _row_is_data(r)]
    idx_new = [j for j, r in enumerate(rows_new) if _row_is_data(r)]
    if not idx_old or not idx_new:
        return None
    data_old = [_norm_row(rows_old[i]) for i in idx_old]
    data_new = [_norm_row(rows_new[j]) for j in idx_new]
    pairs = _lcs_pairs(data_old, data_new)
    if len(pairs) < MIN_MATCH_FRAC * min(len(data_old), len(data_new)):
        return None
    pair_of_new = {j: i for i, j in pairs}
    matched_old = {i for i, _ in pairs}
    unmatched_old = len(data_old) - len(pairs)
    if unmatched_old >= MAX_UNMATCHED_FRAC * len(data_old):
        return None
    matched_new = {j for _, j in pairs}
    unmatched_new_norms: dict[str, int] = {}
    for j in range(len(data_new)):
        if j not in matched_new:
            unmatched_new_norms[data_new[j]] = (
                unmatched_new_norms.get(data_new[j], 0) + 1
            )
    identical = 0
    for i in range(len(data_old)):
        if i in matched_old:
            continue
        if unmatched_new_norms.get(data_old[i], 0):
            identical += 1
            unmatched_new_norms[data_old[i]] -= 1
    if unmatched_old > 0 and identical / unmatched_old >= REAPPEAR_FRAC:
        return None

    out_rows: list[str] = []
    old_cursor = 0
    for j in range(len(data_new)):
        if j in pair_of_new:
            while old_cursor < pair_of_new[j]:
                out_rows.append(
                    _color_row(rows_old[idx_old[old_cursor]], "red")
                )
                old_cursor += 1
            old_cursor += 1  # skip the common old row
            out_rows.append(rows_new[idx_new[j]].rstrip("\n") + "\n")
        else:
            out_rows.append(_color_row(rows_new[idx_new[j]], "blue"))
    while old_cursor < len(data_old):
        out_rows.append(_color_row(rows_old[idx_old[old_cursor]], "red"))
        old_cursor += 1

    return (
        "% texdiff: row-merged table (common rows black, removed red, added blue)\n"
        + pre_new
        + "".join(out_rows)
        + post_new
    )
