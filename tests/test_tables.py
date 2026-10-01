"""Contract tests for table row alignment (v1 core feature).

Tables are the construct that breaks latexdiff hardest: it glues old
and new cells together. texdiff splits table bodies into *row nodes*
with content-based signatures, so the aligner handles rows like list
items - equal rows match verbatim, changed rows pair as block
del/add, insertions and reorders fall out of sequence matching.
"""

from __future__ import annotations

from texdiff import diff_documents
from texdiff.parse import parse

LONGTABLE = """\
\\begin{longtable}{|l|l|}
\\hline
name & type \\\\
\\hline
longitude & float32 \\\\
latitude & float32 \\\\
\\hline
\\end{longtable}
"""


def _table_env(source: str):
    return next(n for n in parse(source) if n.kind == "env" and n.name == "longtable")


class TestRowSplitting:
    def test_longtable_body_splits_into_rows(self):
        env = _table_env(LONGTABLE)
        assert env.atom is False
        assert len(env.children) >= 3  # header rows + 2 data rows
        assert all(c.kind == "row" for c in env.children)

    def test_row_children_reconstruct_body(self):
        # INvariant: children concatenated + wrapper == env text
        env = _table_env(LONGTABLE)
        body = "".join(c.text for c in env.children)
        assert env.text.startswith("\\begin{longtable}")
        assert env.text.endswith("\\end{longtable}")
        assert body in env.text

    def test_row_signature_is_content_based(self):
        # rows with different content must have different signatures
        # (positional pairing would misalign inserted rows)
        env = _table_env(LONGTABLE)
        sigs = [c.signature() for c in env.children if "&" in c.text]
        assert len(set(sigs)) == len(sigs)

    def test_identical_rows_share_signature(self):
        # the same row content (possibly reindented) pairs up
        env = _table_env(LONGTABLE)
        row = next(c for c in env.children if "longitude" in c.text)
        indented = row.text.replace("longitude", "  longitude", 1)
        from texdiff.nodes import Node

        clone = Node(kind="row", text=indented, name=row.name, atom=True)
        assert clone.signature() == row.signature()

    def test_makecell_double_backslash_does_not_split(self):
        # \makecell{a\\b} contains \\ INSIDE braces: one row, not two
        src = (
            "\\begin{tabular}{|l|l|}\n"
            "a & \\makecell{up\\\\down} \\\\\n"
            "b & plain \\\\\n"
            "\\end{tabular}\n"
        )
        env = next(n for n in parse(src) if n.kind == "env" and n.name == "tabular")
        rows = [c for c in env.children if "&" in c.text]
        assert len(rows) == 2
        assert "makecell" in rows[0].text

    def test_endhead_boundaries_are_segments(self):
        src = (
            "\\begin{longtable}{|l|l|}\n"
            "h1 & h2 \\\\\n"
            "\\endfirsthead\n"
            "h1 & h2 \\\\\n"
            "\\endhead\n"
            "d1 & d2 \\\\\n"
            "\\end{longtable}\n"
        )
        env = _table_env(src)
        kinds = [c.kind for c in env.children]
        assert kinds.count("row") >= 3

    def test_verbatim_stays_atomic(self):
        # regression guard: verbatim must never gain children
        env = next(
            n
            for n in parse("\\begin{verbatim}a\\\\b\\end{verbatim}")
            if n.kind == "env"
        )
        assert env.atom is True
        assert env.children == []


class TestRowDiff:
    def test_row_insertion_marks_only_the_row(self):
        old = LONGTABLE
        new = LONGTABLE.replace(
            "latitude & float32 \\\\\n",
            "latitude & float32 \\\\\npolarised & float \\\\\n",
        )
        r = diff_documents(old, new)
        assert "polarised" in r.marked_up
        # the inserted row is marked...
        assert "\\DIFaddbegin" in r.marked_up
        # ...and must NOT be glued to the neighbouring row
        marked = r.marked_up
        add_start = marked.index("\\DIFaddbegin")
        add_end = marked.index("\\DIFaddend")
        added = marked[add_start:add_end]
        assert "latitude" not in added

    def test_changed_cell_marks_its_row_only(self):
        old = LONGTABLE
        new = LONGTABLE.replace("float32", "float64", 1)
        r = diff_documents(old, new)
        # changed row carries both markers...
        assert "\\DIFdelbegin" in r.marked_up
        assert "\\DIFaddbegin" in r.marked_up
        # ...the untouched row does not appear inside any del region
        del_start = r.marked_up.index("\\DIFdelbegin")
        del_end = r.marked_up.index("\\DIFdelend")
        assert "latitude" not in r.marked_up[del_start:del_end]

    def test_row_reorder_keeps_table_valid(self):
        old = LONGTABLE
        new = old.replace(
            "longitude & float32 \\\\\nlatitude & float32 \\\\\n",
            "latitude & float32 \\\\\nlongitude & float32 \\\\\n",
        )
        r = diff_documents(old, new)
        m = r.marked_up
        assert "\\begin{longtable}" in m
        assert "\\end{longtable}" in m
        # equal counts of begin/end markers (balanced markup)
        assert m.count("\\DIFaddbegin") == m.count("\\DIFaddend")
        assert m.count("\\DIFdelbegin") == m.count("\\DIFdelend")

    def test_identical_tables_round_trip_verbatim(self):
        r = diff_documents(LONGTABLE, LONGTABLE)
        assert r.marked_up == LONGTABLE
        assert r.stats.changed == 0

    def test_row_insertion_compilable_shape(self):
        # each marked row keeps its \\ terminator inside its own region
        old = LONGTABLE
        new = LONGTABLE.replace(
            "latitude & float32 \\\\\n",
            "latitude & float32 \\\\\npolarised & float \\\\\n",
        )
        m = diff_documents(old, new).marked_up
        add = m[m.index("\\DIFaddbegin") : m.index("\\DIFaddend")]
        assert add.count("\\\\") >= 1  # row terminator inside the region
        assert "\\end{longtable}" not in add
