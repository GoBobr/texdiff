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

from dataclasses import dataclass, replace as _dc_replace
from difflib import SequenceMatcher

from .nodes import Node


@dataclass(frozen=True)
class Match:
    """A node kept verbatim (identical on both sides)."""

    node: Node
    group: int | None = None


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
    group: int | None = None


@dataclass(frozen=True)
class Insert:
    """A node present only in the new version."""

    new: Node
    group: int | None = None


@dataclass(frozen=True)
class Delete:
    """A node present only in the old version."""

    old: Node
    group: int | None = None


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

    A pure row list whose rows form anchored groups (unique-key
    variable rows each followed by repeated-key attribute rows)
    takes the group-block alignment instead
    (:func:`_align_grouped_rows`): sequence alignment cannot
    express "moved", and grouped tables whose groups are REORDERED
    between revisions would render each moved group's old rows
    strung across other groups' new rows.
    """
    if _is_grouped_row_list(old) and _is_grouped_row_list(new):
        grouped = _align_grouped_rows(old, new)
        if grouped is not None:
            return grouped
    return _align_plain(old, new)


def _align_plain(old: list[Node], new: list[Node]) -> list[Edit]:

    def _pair(o: Node, n: Node) -> Edit:
        if o.text == n.text:
            return Match(node=o)
        if o.kind == "row" and n.kind == "row" and o.name == n.name:
            # equal row signature (content key) with differing text
            # means the difference is purely structural: \hline on
            # the other side of the row, whitespace, comments.
            # Keep ONE row verbatim - the NEW one: unchanged rows
            # keep the new revision's table markup (border style,
            # indentation) so an inline-diffed table does not mix
            # the old border pattern (e.g. no \hline between rows)
            # into the new table's row grid.
            return Match(node=n)
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
                rescued = _gap_rescues(
                    prev_a, block.a, prev_b, block.b, mid_old, mid_new, _pair
                )
                edits.extend(rescued)
                rescued_new = {
                    id(getattr(e, "new", None)) for e in rescued
                }
                edits.extend(
                    Insert(new=n)
                    for n in new_gap[:best]
                    if id(n) not in rescued_new
                )
                edits.append(_pair(o_head, n_head))
                rescued2 = _gap_rescues(
                    prev_a,
                    block.a,
                    block.b + best + 1,
                    block.b + len(new_gap),
                    mid_old,
                    mid_new,
                    _pair,
                )
                edits.extend(rescued2)
                rescued_new |= {
                    id(getattr(e, "new", None)) for e in rescued2
                }
                edits.extend(
                    Insert(new=n)
                    for n in new_gap[best + 1 :]
                    if id(n) not in rescued_new
                )
                edits.append(Insert(new=mid_new[block.b]))
                head_a, head_b = block.a + 1, block.b + 1
                for i in range(1, block.size):
                    edits.append(
                        _pair(mid_old[block.a + i], mid_new[block.b + i])
                    )
                prev_a, prev_b = block.a + block.size, block.b + block.size
                continue
        edits.extend(Delete(old=n) for n in mid_old[prev_a : block.a])
        rescued = _gap_rescues(
            prev_a, head_a, prev_b, block.b, mid_old, mid_new, _pair
        )
        edits.extend(rescued)
        rescued_new = {
            id(getattr(e, "new", None)) for e in rescued
        }
        edits.extend(
            Insert(new=n)
            for n in new_gap
            if id(n) not in rescued_new
        )
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
    edits = _post_pass_rescue(edits, _pair)
    edits = _sibling_swap_rescue(edits, _pair)
    edits = _table_keyed_pair_rescue(edits, _pair)
    edits = _hoist_table_head_inserts(edits)
    return edits


_GROUP_MIN_ROWS = 6  # a grouped table needs anchors AND attributes


def _anchor_key(node: Node) -> str:
    """First-cell key of a row node, ignoring markup and structure.

    The first ``&``-bearing line of the row's text holds the cells;
    leading lines are pure ``\\hline`` structure that the parser
    glues onto the row. Falls back to the whole-row content key when
    the row has no cells (e.g. a caption or multicolumn row).
    """
    import re as _re

    first = ""
    for line in (node.text or "").splitlines():
        if "&" in line:
            first = line
            break
    if not first:
        return node.name or node.signature()
    cell = first.split("&", 1)[0]
    cell = _re.sub(r"\\rowcolor\s*\{[^}]*\}", "", cell)
    cell = _re.sub(r"\\textbf\s*\{([^}]*)\}", r"\1", cell)
    cell = cell.replace("\\-", "").replace("\\_", "_").replace("\\", "")
    return _re.sub(r"[^A-Za-z0-9_/]", "", cell)


def _is_grouped_row_list(nodes: list[Node]) -> bool:
    """True when a node list is a table body of anchored row groups.

    Grouped variable/attribute tables: a unique-key variable row
    (the anchor) followed by repeated-key attribute rows (``units``,
    ``\\_FillValue``, ...). Requires at least two anchors and at least
    one repeated key so plain tables and one-row-per-variable tables
    keep the ordinary sequence alignment. The first-cell key is used,
    not the whole-row content key, so a ``units Pa`` row counts as
    ``units`` - repeated like every group's units row - not as an
    anchor.
    """
    from collections import Counter

    rows = [n for n in nodes if n.kind == "row"]
    if len(rows) < _GROUP_MIN_ROWS or len(rows) != len(nodes):
        return False
    counts = Counter(k for k in (_anchor_key(n) for n in rows) if k)
    anchors = sum(1 for c in counts.values() if c == 1)
    repeated = sum(1 for c in counts.values() if c > 1)
    return anchors >= 2 and repeated >= 1


def _align_grouped_rows(old: list[Node], new: list[Node]) -> list[Edit] | None:
    """Block-align an anchored-group row table (optionally reordered).

    Splits both bodies into group blocks at unique-key anchor rows
    (``variable & ... \\`` followed by its ``attr & ... \\\\`` rows),
    matches blocks by anchor signature with ``SequenceMatcher``, and
    aligns the row lists INSIDE each matched block pair with the
    ordinary row alignment. Old-only blocks retire wholesale, new-only
    blocks insert wholesale - a group never straddles another group's
    rows, whatever the reordering between revisions.

    Returns None (caller falls back to plain sequence alignment) when
    the block structure is degenerate: fewer than two matched blocks
    or a block whose rows exceed a sane group size (the anchor
    detection would be splitting an ordinary table, not groups).
    """
    from collections import Counter

    def blocks(nodes: list[Node]) -> list[list[Node]]:
        # Split right before every row whose first-cell key is unique
        # within the table - such rows are group anchors (variable
        # name rows). Attribute rows repeat their key (``units``,
        # ``flag_values`` ...) and never split. Using the first cell,
        # NOT the whole-row content key, matters: a ``units Pa`` row
        # is content-unique but keyed ``units`` like every other
        # group's units row.
        #
        # A unique key alone is not proof of an anchor: a variable
        # may carry an attribute that no OTHER variable repeats (a
        # one-off ``scale_factor`` row) - splitting there fragments
        # the group and the fragments then pair against the wrong
        # counterpart rows. Generated spec tables mark their
        # variable rows with a row color + bold leading cell and
        # leave attribute rows plain; the decoration is the primary
        # anchor signal, and the count/next-key heuristics below are
        # the fallback for undecorated tables.
        def _is_decorated_anchor(n: Node) -> bool:
            first = ""
            for line in (n.text or "").splitlines():
                if "&" in line:
                    first = line
                    break
            return (
                "\\rowcolor" in first
                and "\\textbf" in first.split("&", 1)[0]
            )

        keys = [_anchor_key(n) for n in nodes if n.kind == "row"]
        counts = Counter(k for k in keys if k)
        row_idx = {id(n): p for p, n in enumerate(nodes) if n.kind == "row"}
        pos = list(row_idx.values())
        n_rows = len(keys)
        next_key: list[str | None] = [None] * (n_rows + 1)
        seq = 0
        for n in nodes:
            if n.kind == "row":
                for m in nodes[row_idx[id(n)] + 1 :]:
                    if m.kind == "row":
                        next_key[seq] = _anchor_key(m)
                        break
                seq += 1
        out: list[list[Node]] = []
        cur: list[Node] = []
        has_decorated = any(
            _is_decorated_anchor(n) for n in nodes if n.kind == "row"
        )
        seq = 0
        for n in nodes:
            if n.kind != "row":
                cur.append(n)
                continue
            k = _anchor_key(n)
            nxt = next_key[seq]
            if has_decorated:
                is_anchor = bool(cur) and _is_decorated_anchor(n)
            else:
                is_anchor = (
                    bool(cur)
                    and counts.get(k) == 1
                    and (
                        seq + 1 < n_rows
                        and nxt is not None
                        and counts.get(nxt, 0) > 1
                    )
                )
            if is_anchor:
                out.append(cur)
                cur = []
            cur.append(n)
            seq += 1
        if cur:
            out.append(cur)
        return out

    old_blocks = blocks(old)
    new_blocks = blocks(new)
    if len(old_blocks) < 2 or len(new_blocks) < 2:
        return None
    if max(map(len, old_blocks + new_blocks)) > 12:
        # implausibly large "group": the anchors are not grouping
        return None

    sm = SequenceMatcher(
        a=[_anchor_key(b[0]) for b in old_blocks],
        b=[_anchor_key(b[0]) for b in new_blocks],
        autojunk=False,
    )

    def block_matches(
        sm_blocks: list,
    ) -> list[tuple[int, int]]:
        """Pairs of (old_index, new_index) of matched groups.

        SequenceMatcher pairs only the longest IN-ORDER runs: a group
        moved to the other end of the table stays unpaired and would
        retire + re-add wholesale - the emitter's keyed row pairing
        then merges attribute rows of unrelated groups (the moved
        group's ``units`` into another group's anchor block). Groups
        that exist uniquely on each side with the same anchor key
        are the SAME group however far it moved: pair the leftovers
        too, keyed by anchor, using the first unused same-key block
        on the other side.
        """
        from collections import Counter

        old_counts = Counter(_anchor_key(b[0]) for b in old_blocks)
        new_counts = Counter(_anchor_key(b[0]) for b in new_blocks)

        # in-order longest-run pairs SequenceMatcher found; the SM
        # pairing is a function i -> j and each j pairs at most one
        # i, so a dict is safe here
        pairs: dict[int, int] = {}
        for mb in sm_blocks[:-1]:  # last block is the (0,0,0) sentinel
            for i in range(mb.size):
                # guard: an OLD block can sit in several SM blocks
                # when its key repeats (e.g. duplicate group anchors
                # across joined sub-tables) - keep the FIRST pairing
                if mb.a + i not in pairs:
                    pairs[mb.a + i] = mb.b + i

        # leftover move-pairing, keyed by unique anchor on both
        # sides, in new-document order
        new_matched = set(pairs.values())
        for j, nb in enumerate(new_blocks):
            if j in new_matched:
                continue
            k = _anchor_key(nb[0])
            if new_counts[k] != 1 or old_counts[k] != 1:
                # ambiguous or unmatched key: leave it for gap
                # retirement / insertion
                continue
            for i, ob in enumerate(old_blocks):
                if i not in pairs and _anchor_key(ob[0]) == k:
                    pairs[i] = j
                    break
        return sorted(pairs.items())

    edits: list[Edit] = []
    matched_pairs = block_matches(sm.get_matching_blocks())
    matched_old = {i for i, _ in matched_pairs}

    # emit in NEW document order so the rendered table follows the
    # new revision's grouping. Movement makes the matched pairs'
    # new indices non-monotonic (they are sorted by OLD index), so
    # a naive ``range(prev_new + 1, j)`` gap insert would re-emit
    # blocks from ranges already covered by an earlier, larger j -
    # track the high-water mark and never go back.
    def _tag(edits_: list[Edit], group: int) -> list[Edit]:
        # stamp the group id on every edit: the emitter uses it to
        # scope same-key row pairing to one variable's rows instead
        # of inferring group boundaries from edit order (fragile -
        # a group's deletes and inserts interleave variably)
        return [
            _dc_replace(e, group=group) if e.group is None else e
            for e in edits_
        ]

    remaining_old = [i for i in range(len(old_blocks)) if i not in matched_old]
    paired_new = {j for _, j in matched_pairs}
    prev_new = -1
    for i, j in matched_pairs:
        # retire old-only groups positioned before this matched
        # group's old index
        for r in list(remaining_old):
            if r < i:
                edits.extend(
                    _tag([Delete(old=n) for n in old_blocks[r]], ("old", r))
                )
                remaining_old.remove(r)
        # insert new-only groups that precede this matched block in
        # new-document order and were never paired
        for k in range(prev_new + 1, j):
            if k not in paired_new:
                edits.extend(
                    _tag([Insert(new=n) for n in new_blocks[k]], ("new", k))
                )
        prev_new = max(prev_new, j)
        edits.extend(
            _tag(
                _align_rows_within_group(old_blocks[i], new_blocks[j]),
                ("old", i),
            )
        )
    for k in range(prev_new + 1, len(new_blocks)):
        if k not in paired_new:
            edits.extend(
                _tag([Insert(new=n) for n in new_blocks[k]], ("new", k))
            )
    for r in remaining_old:
        edits.extend(_tag([Delete(old=n) for n in old_blocks[r]], ("old", r)))
    if len(matched_pairs) < 2:
        return None  # nothing group-like matched: keep plain alignment
    return edits


def _align_rows_within_group(old_rows: list[Node], new_rows: list[Node]) -> list[Edit]:
    """Ordinary row alignment confined to one matched group block.

    Rows within a group keep their relative order between revisions,
    so the SequenceMatcher-based :func:`align` body (without the
    grouped-row dispatch, which would recurse) aligns them well:
    shared rows Match, changed attribute rows pair for the per-cell
    keyed merge, added attributes Insert, removed ones Delete.
    """
    # plain align(), bypassing the grouped dispatch on purpose
    return _align_plain(old_rows, new_rows)


def _hoist_table_head_inserts(edits: list[Edit]) -> list[Edit]:
    """Move an inserted longtable head block to the head of the delete run.

    A fully restructured table diffes as a long run of ``Delete(old
    rows)`` followed by ``Insert(new rows)``. SequenceMatcher anchors
    the insert gap at whichever old rows share incidental content
    with the new head (e.g. trailing ``HDFEOS INFORMATION`` rows),
    so the NEW table head - the blue header row plus the
    ``\\endfirsthead`` boundary - renders deep inside the table body
    instead of at the top where the old header was retired (the
    repeated header then only shows near the table end).

    When an insert gap starts with the new header row and reaches an
    insert carrying the ``\\endfirsthead`` marker (a new head
    block), hoist that whole head block ahead of the contiguous
    run of row ``Delete``s before it, keeping the visual order
    old-header-retired -> new-header-added -> body rows.
    """
    i = 0
    while i < len(edits):
        e = edits[i]
        if (
            isinstance(e, Insert)
            and "\\begin{longtable" not in e.new.text
            and "\\rowcolor{" in e.new.text
        ):
            # extend the head block through the insert that carries
            # the \endfirsthead marker
            j = i
            end = i
            while j < len(edits) and isinstance(edits[j], Insert):
                if "\\endfirsthead" in edits[j].new.text:
                    end = j
                    break
                j += 1
            if end == i and "\\endfirsthead" not in edits[i].new.text:
                i += 1
                continue
            # contiguous Delete run immediately before i
            k = i
            while k > 0 and isinstance(edits[k - 1], Delete):
                k -= 1
            if k < i:
                # the first Delete of the run retires the OLD header
                # row: keep it first (struck old header), then the
                # new blue head block, then the rest
                head = edits[i : end + 1]
                del_pos = k + 1 if i - k > 1 else k
                return (
                    edits[:del_pos]
                    + head
                    + edits[del_pos:i]
                    + edits[end + 1 :]
                )
        i += 1
    return edits


_ROW_KEY_RE = None


def _row_keys(text: str) -> set[str]:
    """Content keys of table row lines (first cell of each `a & b`)."""
    import re

    global _ROW_KEY_RE
    if _ROW_KEY_RE is None:
        _ROW_KEY_RE = re.compile(r"^\s*([^&%\n]+?)\s*&", re.M)
    return {m.group(1).strip() for m in _ROW_KEY_RE.finditer(text or "")}


def _content_similarity(a: "Node", b: "Node") -> float:
    """How much two same-signature nodes share, in ``[0, 1]``.

    Row-set Jaccard for tables (a 3-row fragment cannot beat a
    30-row near-copy), plain text ratio otherwise.
    """
    ka, kb = _row_keys(a.text), _row_keys(b.text)
    if len(ka) >= 3 and len(kb) >= 3:
        return len(ka & kb) / len(ka | kb)
    return _text_similarity(a.text or "", b.text or "")


def _sibling_swap_rescue(edits: list[Edit], _pair) -> list[Edit]:
    """Re-pair a Modify whose new side is the WRONG same-sig sibling.

    A duplicate signature on the new side (a table split into two
    same-captioned tables) makes SequenceMatcher pair the old node
    with whichever sibling sits at the aligned offset - often the
    small positional fragment while the real counterpart (a near
    copy carrying most of the old rows) lands as a pure Insert.
    For each Insert carrying an anchored signature that some Modify
    already used, move the pairing to the sibling the old side
    actually resembles; the previously (wrongly) chosen sibling
    becomes the Insert.
    """
    anchored = lambda n: (
        s := n.signature()
    ) and ":" in s and s not in _RESCUE_PLAIN_SIGS and not (
        s.startswith("macro:") and s.count(":") < 2
    )
    modifies = {
        i: e for i, e in enumerate(edits) if type(e).__name__ == "Modify"
    }
    swaps: dict[int, int] = {}
    taken_ins: set[int] = set()
    for i, e in modifies.items():
        if not anchored(e.new):
            continue
        sig = e.new.signature()
        for j, ie in enumerate(edits):
            if j in taken_ins or type(ie).__name__ != "Insert":
                continue
            if ie.new.signature() != sig or not anchored(ie.new):
                continue
            better = _content_similarity(e.old, ie.new)
            current = _content_similarity(e.old, e.new)
            if better > max(current + 0.25, 0.5):
                swaps[i] = j
                taken_ins.add(j)
                break
    if not swaps:
        return edits
    # detach the (better) new siblings from their Insert slots, park
    # the previously-chosen siblings there instead
    swap_new: dict[int, object] = {}
    for i, j in swaps.items():
        swap_new[i] = edits[j].new
        edits[j] = Insert(new=modifies[i].new)
    return [
        Modify(old=modifies[idx].old, new=swap_new[idx])
        if idx in swaps
        else e
        for idx, e in enumerate(edits)
    ]


# signatures that must NOT take part in cross-gap rescue pairing:
# they carry no content anchor, so "unique" is meaningless and any
# forced pairing is a guess. Exact-match only: ``group:..anchor``
# and ``macro:sectioning:title`` signatures DO carry anchors.
_RESCUE_PLAIN_SIGS = ("text", "group", "env", "macro:label")


def _post_pass_rescue(edits: list[Edit], _pair) -> list[Edit]:
    """Pair leftover Delete+Insert nodes with unique equal signatures.

    ``SequenceMatcher`` is greedy over whole-signature sequences; a
    LOCAL reorganisation (one added table or a duplicated partner
    table nearby) can steal the alignment so a *globally unique*
    signature pair - a table group anchored by its caption - ends up
    split across two different gaps: the old node deleted in one,
    the new node inserted in another. Content-wise identical tables
    then read as "retired and reintroduced".

    After the gap loop, any signature that occurs exactly once among
    the deletes AND exactly once among the inserts, and that carries
    a content anchor, unambiguously identifies the same logical
    block: pair it (Match when verbatim-equal, else Modify) and drop
    the separate insert.
    """
    from collections import Counter

    def node_of(e):
        return getattr(e, "node", None) or getattr(
            e, "old", None
        ) or getattr(e, "new", None)

    def sig_of(e):
        s = node_of(e).signature()
        if s in _RESCUE_PLAIN_SIGS:
            return None
        if s.startswith("macro:") and s.count(":") < 2:
            return None  # anchorless macro, e.g. plain \renewcommand
        return s

    dels = [i for i, e in enumerate(edits) if type(e).__name__ == "Delete"]
    ins = [i for i, e in enumerate(edits) if type(e).__name__ == "Insert"]
    if not dels or not ins:
        return edits
    dcount = Counter(
        s
        for s in (sig_of(edits[i]) for i in dels)
        if s
    )
    icount = Counter(
        s
        for s in (sig_of(edits[i]) for i in ins)
        if s
    )
    rescue = {
        s for s in dcount if dcount[s] == 1 and icount.get(s) == 1
    }
    if not rescue:
        return edits
    # rebuild: on a rescued Delete, splice its Insert partner in
    # place (replacing the Delete); the partner Insert itself is
    # dropped. Pairings are precomputed so an Insert that PRECEDES
    # its rescued Delete is also suppressed.
    pairs = {}
    used_ins = set()
    for i in dels:
        s = sig_of(edits[i])
        if s not in rescue:
            continue
        for j in ins:
            if j in used_ins or sig_of(edits[j]) != s:
                continue
            used_ins.add(j)
            pairs[i] = j
            break
    if not pairs:
        return edits
    out = []
    for idx, e in enumerate(edits):
        if type(e).__name__ == "Delete":
            if idx in pairs:
                j = pairs[idx]
                out.append(_pair(e.old, edits[j].new))
            else:
                out.append(e)
        elif type(e).__name__ == "Insert":
            if idx not in used_ins:
                out.append(e)
        else:
            out.append(e)
    return out


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


def _gap_rescues(a_lo, a_hi, b_lo, b_hi, mid_old, mid_new, _pair):
    """Intersectional pairing of overlooked signature-equal nodes.

    ``SequenceMatcher`` over signatures is greedy: a long
    equal-signature run that starts at a shifted offset can eat the
    alignment budget so a *unique* signature pair - e.g. a table
    group anchored by its caption - lands inside a gap and is
    emitted as wholesale Delete + Insert, even though only
    formatting inside changed (that reads as "table retired and
    reintroduced unchanged").

    For each signature that appears EXACTLY ONCE on both sides of
    the gap, pair the two nodes before the plain delete/insert
    emission. Unique-per-gap means there is no ambiguity about
    which nodes belong together. Paired nodes leave the gap; the
    remaining gap nodes keep the plain delete+insert treatment.
    """
    from collections import Counter

    olds = mid_old[a_lo:a_hi]
    news = mid_new[b_lo:b_hi]
    if not olds or not news:
        return []
    oc = Counter(n.signature() for n in olds)
    nc = Counter(n.signature() for n in news)
    shared_once = [
        s for s in set(oc) & set(nc) if oc[s] == 1 and nc[s] == 1
    ]
    if not shared_once:
        return []
    # only structural candidates; plain text/blank nodes pair
    # positionally already and rescuing them would fight the
    # block-head refinement above
    keep = {
        s
        for s in shared_once
        if not s.startswith(("text", "macro:label"))
    }
    if not keep:
        return []
    by_sig_new = {n.signature(): n for n in news if n.signature() in keep}
    out = []
    for n in olds:
        s = n.signature()
        if s in keep and s in by_sig_new:
            out.append(_pair(n, by_sig_new.pop(s)))
    return out


def _table_keyed_pair_rescue(edits: "list[Edit]", _pair) -> "list[Edit]":
    """Pair a deleted longtable with an inserted one whose rows match.

    A wholesale-regenerated table (new generator, column-wise output)
    can be textually so different that no signature run pairs the two
    versions: the old table land as a Delete, the new one as an
    Insert, and the diff retires + reintroduces the whole table even
    when row by row it is the same table (every variable: same first
    cell, edited attribute cells). When the delete's table rows pair
    by key with a LATER insert's table rows, convert the pair into a
    Modify so the emit layer's inline row markup applies.

    Row keys compare by the FULL first cell: an HDF-EOS grid
    rename (NPP_Grid_IMG_2D -> VIIRS_Grid_IMG_2D) changes every
    row's path prefix, and the resulting Modify would render as two
    wholesale blocks (all rows struck, then all re-added) inside
    one table - worse than leaving the pair as a clean whole-table
    retire + reintroduction. Regenerated tables keep their row
    keys; only wholesale renames are left alone.
    """
    import re

    def table_row_keys(node) -> set[str]:
        if "longtable" not in (node.text or ""):
            return set()
        keys = set()
        for m in re.finditer(r"^\s*([^&%\n]+?)\s*&", node.text or "", re.M):
            keys.add(m.group(1).strip().replace("\\_", "_"))
        return keys

    dels = [
        (i, e)
        for i, e in enumerate(edits)
        if type(e).__name__ == "Delete"
        and "longtable" in (e.old.text or "")
    ]
    if not dels:
        return edits
    ins = [
        (j, e)
        for j, e in enumerate(edits)
        if type(e).__name__ == "Insert"
        and "longtable" in (e.new.text or "")
    ]
    used: set[int] = set()
    out = list(edits)
    for i, d in dels:
        if i in used:
            continue
        ka = table_row_keys(d.old)
        if len(ka) < 2:
            continue
        best_j, best_shared = None, 0
        for j, ie in ins:
            if j in used or j <= i:
                continue
            kb = table_row_keys(ie.new)
            if len(kb) < 2:
                continue
            shared = len(ka & kb)
            if shared >= 2 and shared >= 0.5 * min(len(ka), len(kb)):
                if shared > best_shared:
                    best_j, best_shared = j, shared
        if best_j is None:
            continue
        out[i] = _pair(d.old, edits[best_j].new)
        out[best_j] = None  # type: ignore[assignment]
        used.add(best_j)
        used.add(i)
    return [e for e in out if e is not None]
