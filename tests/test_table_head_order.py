"""Regression tests for table head/dims-block ordering in diffs.

Covers three renderer misplacements found on a generated
specification-document diff build:

* the inserted (blue) header row of a fully restructured table
  landing deep inside the table body because SequenceMatcher
  anchors the insert gap at incidental content near the old
  table's tail;
* an inserted "Global dimensions" metadata longtable rendering
  AFTER the modified sibling data table although the new revision
  places it BEFORE;
* punctuation-only cell differences (``[ ... ']`` edge brackets,
  a dangling ``'``) rendering as struck-and-reintroduced words.
"""

from texdiff.api import diff_documents


def doc(body: str) -> str:
    return (
        "\\documentclass{article}\n\\usepackage{longtable}\n"
        "\\begin{document}\n" + body + "\\end{document}\n"
    )


class TestInsertedHeadRowHoist:
    def test_header_row_insert_hoists_to_table_head(self):
        # unit-level: the aligner emits a long Delete run (restructured
        # old rows) followed by the Insert gap; when the gap's leading
        # inserts carry the new header row and the \endfirsthead
        # marker, the head block hoists to the front of the delete
        # run (right after the old header-row Delete)
        from texdiff.align import (
            Delete,
            Insert,
            _hoist_table_head_inserts,
        )
        from texdiff.nodes import Node

        n = lambda t: Node(kind="row", text=t)  # noqa: E731
        old_hdr = Delete(old=n("\n\\hline\n\\rowcolor{lightgray}  extbf{Variable} &  extbf{Type} \\\\\n\\hline\n"))
        old_rows = [Delete(old=n(f"\n\\hline\na{i} & t{i} \\\\\n\\hline\n")) for i in range(6)]
        new_hdr = Insert(new=n("\n\\hline\n\\rowcolor{lightgray} \\textbf{Variable} & \\textbf{Type} \\\\\n"))
        new_headblk = Insert(new=n("\n\\hline\n\\endfirsthead\n\n\\hline\nb0 & u0 \\\\\n"))
        new_rows = [Insert(new=n(f"\n\\hline\nb{i+1} & u{i+1} \\\\\n")) for i in range(5)]

        edits = [old_hdr] + old_rows + [new_hdr, new_headblk] + new_rows
        out = _hoist_table_head_inserts(edits)
        order = [type(e).__name__ for e in out]
        texts = [(e.old if isinstance(e, Delete) else e.new).text for e in out]
        # nothing dropped or duplicated
        assert len(out) == len(edits)
        assert order.count("Insert") == 7 and order.count("Delete") == 7
        # the blue head block (new header row through \endfirsthead)
        # renders after the retired old header but BEFORE any other
        # old-row Delete
        hdr_pos = next(i for i, t in enumerate(texts) if "extbf{Variable}" in t)
        endfirst_pos = next(i for i, t in enumerate(texts) if "\\endfirsthead" in t)
        assert hdr_pos == 0
        assert endfirst_pos <= 2  # old hdr, new hdr, headblk
        # no old body row retired before the new head block (a1-a5 all after)
        body_del_pos = min(
            texts.index(t) for t in texts if "a1 & t1" in t
        )
        assert endfirst_pos < body_del_pos

    def test_no_hoist_without_head_marker(self):
        # inserts without an \endfirsthead marker in the gap stay put:
        # plain added rows in mid-table must render at the gap
        from texdiff.align import (
            Delete,
            Insert,
            _hoist_table_head_inserts,
        )
        from texdiff.nodes import Node

        n = lambda t: Node(kind="row", text=t)  # noqa: E731
        edits = (
            [Delete(old=n("\n\\hline\nold & row \\\\\n"))]
            + [Delete(old=n(f"\n\\hline\na{i} & t{i} \\\\\n")) for i in range(4)]
            + [Insert(new=n(f"\n\\hline\\rowcolor{{lightgray}} x{i} & y{i} \\\\\n")) for i in range(3)]
        )
        out = _hoist_table_head_inserts(edits)
        assert [type(e).__name__ for e in out] == [
            "Delete",
        ] * 5 + ["Insert"] * 3


class TestInsertedDimsTableOrder:
    def test_dims_table_precedes_modified_sibling(self):
        # old revision: data table only; new revision: dims table
        # BEFORE the data table. The aligner pairs the data tables
        # (Modify) and emits the dims Insert after it - the render
        # order must follow the NEW revision instead.
        data_old = (
            "{\\scriptsize\n\\begin{longtable}{ll}\n"
            "\\caption{data cap}\\\\\n\\hline\n"
            "\\rowcolor{lightgray} \\textbf{Variable} & \\textbf{Type} \\\\\n"
            "\\hline\n\\endfirsthead\n\\hline\n"
            "foo & float32 \\\\\n\\hline\n"
            "\\end{longtable}\n}"
        )
        dims_new = (
            "{\\scriptsize\n\\begin{longtable}{ll}\n"
            "\\caption{Global dimensions (path: \\path{/})}\\\\\n\\hline\n"
            "\\rowcolor{lightgray} \\textbf{Dimension name} & \\textbf{Length}\\\\\n"
            "\\hline\n\\endfirsthead\n\\hline\n"
            "\\textbf{lat} & 10 \\\\\n\\hline\n"
            "\\end{longtable}\n}"
        )
        data_new = (
            "{\\scriptsize\n\\begin{longtable}{ll}\n"
            "\\caption{data cap}\\\\\n\\hline\n"
            "\\rowcolor{lightgray} \\textbf{Variable} & \\textbf{Type} \\\\\n"
            "\\hline\n\\endfirsthead\n\\hline\n"
            "foo & float \\\\\n\\hline\n"
            "\\end{longtable}\n}"
        )
        old_src = doc(data_old)
        new_src = doc(dims_new + "\n" + data_new)
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        dims_pos = out.find("Global dimensions")
        data_pos = out.find("data cap")
        assert dims_pos >= 0 and data_pos >= 0
        assert dims_pos < data_pos


class TestPunctOnlyCellMatch:
    def test_edge_brackets_stay_only_marks(self):
        # "[ 500 m 16 days SWIR3 reflectance']" vs the same words
        # without the bracket-punctuation: words render plain, only
        # the brackets strike
        head = (
            "\\caption{vars}\\\\\n\\hline\n"
            "\\rowcolor{lightgray} \\textbf{Variable} & \\textbf{Description} \\\\\n"
            "\\hline\n\\endfirsthead\n\\hline\n"
        )
        old_src = doc(
            "{\\scriptsize\n\\begin{longtable}{ll}\n"
            + head
            + "swir3 & [ 500 m 16 days SWIR3 reflectance'] \\\\\n\\hline\n"
            "\\end{longtable}\n}"
        )
        new_src = doc(
            "{\\scriptsize\n\\begin{longtable}{ll}\n"
            + head
            + "swir3 & 500 m 16 days SWIR3 reflectance \\\\\n\\hline\n"
            "\\end{longtable}\n}"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        # only the bracket characters are struck
        assert "\\DIFdel{[}" in out
        assert "\\DIFdel{']}" in out or "\\DIFdel{]}" in out
        # the shared words are NOT struck as whole units
        assert "\\sout{reflectance}" not in out
        assert "\\sout{SWIR3}" not in out

    def test_stray_quote_strikes_alone(self):
        # "Algorithm bit flags QA'" vs "Algorithm bit flags QA"
        head = (
            "\\caption{vars}\\\\\n\\hline\n"
            "\\rowcolor{lightgray} \\textbf{Variable} & \\textbf{Description} \\\\\n"
            "\\hline\n\\endfirsthead\n\\hline\n"
        )
        old_src = doc(
            "{\\scriptsize\n\\begin{longtable}{ll}\n"
            + head
            + "qa & Algorithm bit flags QA' \\\\\n\\hline\n"
            "\\end{longtable}\n}"
        )
        new_src = doc(
            "{\\scriptsize\n\\begin{longtable}{ll}\n"
            + head
            + "qa & Algorithm bit flags QA \\\\\n\\hline\n"
            "\\end{longtable}\n}"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        assert "\\DIFdel{'}" in out
        assert "\\sout{QA}" not in out
        assert "\\sout{Algorithm}" not in out

    def test_shared_closing_paren_not_struck(self):
        # "(LiSparse kernel)'" vs "(LiSparse kernel)": the closing
        # paren exists on BOTH sides - only the trailing quote may
        # strike, the paren renders plain (word-diff pairs the last
        # token, so the matcher must surplus-match the punctuation)
        from texdiff.emit import _punct_only_wordmarks

        out = _punct_only_wordmarks(
            "Geometric BRDF kernel coefficient (LiSparse kernel)'",
            "Geometric BRDF kernel coefficient (LiSparse kernel)",
        )
        assert out is not None
        assert "\\DIFdel{'}" in out
        assert "\\DIFdel{)'}" not in out
        # the shared closing paren must still render (plain)
        assert "(LiSparse kernel)" in out

    def test_whole_cell_quoted_value_strikes_quote_only(self):
        # "Latitude'" vs "Latitude": the word diff pairs the ENTIRE
        # cell (no equal run) - core equality still identifies pure
        # quoting noise, so only the quote strikes
        from texdiff.emit import _punct_only_wordmarks

        out = _punct_only_wordmarks("Latitude'", "Latitude")
        assert out is not None
        assert "\\DIFdel{'}" in out
        assert "Latitude" in out
        # genuine rewrite still falls back to whole-cell marks
        assert _punct_only_wordmarks("Latitude", "Longitude") is None
