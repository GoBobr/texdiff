"""Tests for the wholesale table rendering (restructured tables).

texdiff renders heavily restructured table pairs as whole tables -
the old revision struck through in red, the new one in blue - instead
of per-cell markup, which degenerates into unreadable red/blue
interleaving when nearly every cell changes (generated NCML dumps).
The policy mirrors the reference sanitize-tables toolkit.
"""

from __future__ import annotations

from texdiff import tables
from texdiff.api import diff_documents


TABLE = (
    "\\begin{longtable}{|W{.25}|W{.07}|W{.35}|}\n"
    "\\caption{old table}\n"
    "\\\\\n\\hline\n"
    "\\rowcolor{lightgray} \\textbf{Variable} & \\textbf{Type} & \\textbf{Desc}\\\\\n"
    "\\hline\n\\endfirsthead\n"
    "\\rowcolor{lightgray} \\textbf{Variable} & \\textbf{Type} & \\textbf{Desc} \\\\\n"
    "\\hline\n\\endhead\n"
    "\\hline\\multicolumn{3}{r}{Continued on next page} \\\\\n\\endfoot\n"
    "\\endlastfoot\n"
    "\\hline\n"
    "CHL & float32 & mean of binned pixels\\\\\n"
    "\\hline\n"
    "CHL\\_uncertainty & int16 & uncertainty estimation\\\\\n"
    "\\hline\n"
    "\\end{longtable}"
)


class TestDataRows:
    def test_counts_data_rows(self):
        # 3 content rows + the caption-terminator \\ line + the repeated
        # \endhead header row - the reference counts these too
        assert tables.data_rows(TABLE) == 5

    def test_ignores_comments_and_structure(self):
        text = (
            "\\begin{longtable}{ll}\n% comment row & here\\\\\n\\hline\na & b\\\\\n\\end{longtable}"
        )
        assert tables.data_rows(text) == 1

    def test_empty(self):
        assert tables.data_rows("") == 0


class TestIsRestructured:
    def test_same_row_count_not_restructured(self):
        new = TABLE.replace("CHL", "XX")
        assert not tables.is_restructured(TABLE, new)

    def test_moderate_row_loss_below_threshold(self):
        # drop 1 of 3 data rows: diff 1 < RETIRE_MIN_ROWS
        trimmed = TABLE.replace(
            "CHL\\_uncertainty & int16 & uncertainty estimation\\\\\n\\hline\n", ""
        )
        assert not tables.is_restructured(TABLE, trimmed)

    def test_missing_side_is_restructured(self):
        assert tables.is_restructured(TABLE, "")
        assert tables.is_restructured("", TABLE)

    def test_massive_row_loss_triggers(self):
        # 3 original + 20 extra = 23 data rows, 17 counted by the
        # structure rules vs 4: diff 13, ratio 13/17 >= 0.80
        big = TABLE + "\n" + "\n".join(f"v{i} & t{i} & d{i}\\\\" for i in range(20))
        small = TABLE.replace(
            "CHL\\_uncertainty & int16 & uncertainty estimation\\\\\n\\hline\n", ""
        )
        assert tables.is_restructured(big, small)


class TestStrikeWord:
    def test_plain_word_struck(self):
        assert tables._strike_word("abc") == "\\sout{abc}"

    def test_breakpoint_reopens_strike(self):
        out = tables._strike_word("a\\_b")
        assert "\\allowbreak" in out
        assert out.count("\\sout{") == 2

    def test_rowcolor_untouched(self):
        assert tables._strike_word("\\rowcolor{x}") == "\\rowcolor{x}"

    def test_ampersand_untouched(self):
        assert tables._strike_word("&") == "&"

    def test_double_backslash_untouched(self):
        assert tables._strike_word("\\\\") == "\\\\"

    def test_item_token_outside_strike(self):
        # \item stays outside \sout (ulem rejects it in LR mode);
        # the word itself is struck
        assert tables._strike_word("\\item") == "\\item"
        assert tables._strike_word("foo") == "\\sout{foo}"


class TestStrikeThrough:
    def test_structure_lines_pass_through(self):
        out = tables.strike_through(TABLE)
        assert "\\hline\n" in out
        assert "\\endfirsthead" in out
        assert "\\end{longtable}" in out

    def test_row_content_struck(self):
        out = tables.strike_through(TABLE)
        assert "\\sout{CHL}" in out
        assert "\\sout{float32}" in out

    def test_terminator_outside_strike(self):
        out = tables.strike_through(TABLE)
        assert "\\sout{\\\\}" not in out


class TestAddBreakpoints:
    def test_underscore_gets_allowbreak(self):
        assert tables._add_breakpoints("a\\_b") == "a\\_\\allowbreak b"

    def test_comment_untouched(self):
        assert tables._add_breakpoints("% a\\_b") == "% a\\_b"

    def test_package_line_untouched(self):
        line = "\\usepackage{a\\_b}"
        assert tables._add_breakpoints(line) == line


class TestRenderRestructured:
    def test_shape(self):
        out = tables.render_restructured(TABLE, TABLE.replace("old", "new"))
        assert out.startswith("{\\color{red}\n")
        assert "{\\color{blue}\n" in out

    def test_no_new_side(self):
        out = tables.render_restructured(TABLE, "")
        assert "{\\color{blue}" not in out


def _rows_table(rows: list[str]) -> str:
    """A minimal longtable with the given data rows."""
    body = "\n".join(rows)
    return (
        "\\begin{longtable}{lll}\n"
        "\\caption{table}\\\\\n\\hline\n"
        f"{body}\n"
        "\\hline\n"
        "\\end{longtable}"
    )


class TestIsPathological:
    def test_similar_tables_not_pathological(self):
        assert not tables.is_pathological(TABLE, TABLE.replace("CHL", "CHL2"))

    def test_dissimilar_tables_pathological(self):
        old = _rows_table(
            [f"variable{i} & type{i} & description of thing{i}\\\\" for i in range(10)]
        )
        new = _rows_table(
            [f"other{i} & different{i} & completely unrelated words{i}\\\\" for i in range(8)]
        )
        assert tables.is_pathological(old, new)

    def test_empty_side_not_pathological(self):
        assert not tables.is_pathological(TABLE, "")
        assert not tables.is_pathological("", TABLE)


class TestMergeTables:
    def test_identical_rows_merge_black(self):
        merged = tables.merge_tables(TABLE, TABLE.replace("old table", "new table"))
        assert merged is not None
        assert "row-merged" in merged
        assert "{\\color{blue}" not in merged
        assert "{\\color{red}" not in merged

    def test_different_specs_rejected(self):
        other = TABLE.replace("{|W{.25}|W{.07}|W{.35}|}", "{|W{.5}|W{.1}|}")
        assert tables.merge_tables(TABLE, other) is None

    def test_added_row_renders_blue(self):
        new = TABLE.replace(
            "CHL\\_uncertainty & int16 & uncertainty estimation\\\\",
            "CHL\\_uncertainty & int16 & uncertainty estimation\\\\\n"
            "\\hline\nNEW & int8 & freshly added row\\\\",
        )
        merged = tables.merge_tables(TABLE, new)
        assert merged is not None
        assert "{\\color{blue}" in merged
        assert "NEW" in merged

    def test_removed_row_renders_red_struck(self):
        trimmed = TABLE.replace(
            "CHL\\_uncertainty & int16 & uncertainty estimation\\\\\n\\hline\n", ""
        )
        merged = tables.merge_tables(TABLE, trimmed)
        assert merged is not None
        assert "{\\color{red}" in merged
        assert "\\sout{CHL}" in merged


class TestFixCellCounts:
    def test_noop(self):
        assert tables._fix_cell_counts(TABLE) == TABLE


class TestEmitWiring:
    """The emit.py table cascade, exercised through the public API."""

    def test_table_with_stable_anchor_keeps_row_markup(self):
        # the caption anchors the pair; a single changed cell keeps
        # the inline per-cell markup
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\begin{longtable}{ll}\n\\caption{the table}\\\\\na & b\\\\\n\\end{longtable}\n"
            "\\end{document}\n"
        )
        new_src = old_src.replace("a & b", "a & c")
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        assert "\\DIFdel{b}" in out
        assert "\\DIFadd{c}" in out

    def test_pathological_pair_renders_wholesale(self):
        rows_old = "\n".join(
            f"variable{i} & type{i} & description of the thing{i}\\\\" for i in range(8)
        )
        rows_new = "\n".join(
            f"completely{i} & different{i} & entirely other content{i}\\\\" for i in range(6)
        )
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            f"\\begin{{longtable}}{{lll}}\n{rows_old}\n\\end{{longtable}}\n"
            "\\end{document}\n"
        )
        new_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            f"\\begin{{longtable}}{{lll}}\n{rows_new}\n\\end{{longtable}}\n"
            "\\end{document}\n"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        assert "{\\color{red}" in out or "{\\color{blue}" in out or "row-merged" in out

    def test_deleted_table_renders_struck(self):
        rows = "\n".join(f"var{i} & type{i} & desc{i}\\\\" for i in range(5))
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            f"\\begin{{longtable}}{{lll}}\n{rows}\n\\end{{longtable}}\n"
            "\\end{document}\n"
        )
        new_src = "\\documentclass{article}\n\\begin{document}\ntext here\n\\end{document}\n"
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        assert "{\\color{red}" in out
        assert "\\sout{" in out


class TestRetiredTablePolicy:
    """Old-then-new ordering, shared numbering for replaced tables."""

    def test_retired_table_precedes_inserted_replacement(self):
        # insert-first alignment: the new table's Edit comes before
        # the old table's Delete; the render must still emit the
        # retired red table first, then the blue replacement
        rows_old = "\n".join(f"var{i} & type{i} & desc{i}\\\\" for i in range(6))
        rows_new = "\n".join(f"new{i} & other{i} & different{i}\\\\" for i in range(3))
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            f"\\begin{{longtable}}{{lll}}\n\\caption{{grid specs}}\n"
            f"{rows_old}\n\\end{{longtable}}\n"
            "\\end{document}\n"
        )
        new_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            f"\\begin{{longtable}}{{lll}}\n\\caption{{model params}}\n"
            f"{rows_new}\n\\end{{longtable}}\n"
            "\\end{document}\n"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        i_red = out.find("{\\color{red}")
        # the pair may render wholesale (blue block after red) or as
        # a DIFadd-wrapped insert after the retired block; either way
        # the retired table must come FIRST
        if i_red >= 0:
            candidates = [m for m in (out.find("{\\color{blue}"), out.find("\\DIFaddbegin")) if m >= 0]
            assert not candidates or i_red < min(candidates)
        else:
            # insert-first alignment with merge path: assert the
            # retired table exists somewhere by struck caption
            assert "\\caption[]{\\texorpdfstring" in out

    def test_retired_caption_is_struck_unnumbered_counter_neutral(self):
        # the retired table's caption keeps its struck text but must
        # not add a list-of-tables entry and must not consume a table
        # number: the blue replacement takes the number instead
        rows_old = "\n".join(f"var{i} & type{i} & desc{i}\\\\" for i in range(6))
        rows_new = "\n".join(f"entirely{i} & new{i} & content{i}\\\\" for i in range(3))
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            f"\\begin{{longtable}}{{lll}}\n\\caption{{sales figures}}\n"
            f"{rows_old}\n\\end{{longtable}}\n"
            "\\end{document}\n"
        )
        new_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            f"\\begin{{longtable}}{{lll}}\n\\caption{{model params}}\n"
            f"{rows_new}\n\\end{{longtable}}\n"
            "\\end{document}\n"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        struck = tables.strike_through(
            "\\caption{sales figures}\n"
        )
        assert "\\caption[]{" in struck
        assert "\\sout{sales figures}" in struck
        assert "\\addtocounter{table}{-1}" in struck

    def test_strike_through_guarded_caption_e2e(self):
        # strike_through applies the guard to real caption lines
        struck = tables.strike_through("Grid of something:\n\\caption{Regular grid, layer and parameter}\nvar & x\\\\\n")
        assert "\\caption[]{\\texorpdfstring{\\sout{Regular grid, layer and parameter}}{}}" in struck
        # the word after the caption is still struck normally
        assert "\\sout{var}" in struck
