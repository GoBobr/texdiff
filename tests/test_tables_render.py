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

    def test_punctuation_delete_leads_inserted_block(self):
        """A stranded sentence-final period must lead the add region."""
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "Some sentence referencing \\hyperlink{doc:ADS}{[ADS]}.\n"
            "\\end{document}\n"
        )
        new_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "Some sentence referencing \\hyperlink{doc:SPEC}{[SPEC]}.\n"
            "Note: schemas are generated in the format definition\n"
            "repository (\\texttt{xml-definitions})\n"
            ") and shipped with the processor.\n"
            "\\end{document}\n"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        if r"\DIFdel{.}" in out:
            # if present, the struck dot must come BEFORE the blue
            # replacement text - at the start of the block, not after it
            del_pos = out.index(r"\DIFdel{.}")
            add_pos = out.index(r"\DIFaddbegin")
            assert del_pos < add_pos

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

    def test_retired_table_precedes_inserted_metadata_and_data_tables(self):
        # GitHub #1: a region where the new revision gained a
        # metadata ("Global dimensions") table BEFORE its (slightly
        # changed) data table: the Insert queues up before the
        # table-replacement Modify and the retiring Modify must hoist
        # above the WHOLE queue so the struck old data table
        # precedes the blue Global-dimensions table
        dims = (
            "\\begin{longtable}{ll}\n\\caption{Global dimensions}\n"
            "dim & 1\\\\\n\\end{longtable}\n"
        )
        rows_old = "\n".join(f"old{i} & v{i}\\\\" for i in range(6))
        rows_new = "\n".join(f"old{i} & w{i}\\\\" for i in range(6))
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            f"\\begin{{longtable}}{{ll}}\n\\caption{{data table}}\n"
            f"{rows_old}\n\\end{{longtable}}\n"
            "\\end{document}\n"
        )
        new_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            + dims
            + f"\\begin{{longtable}}{{ll}}\n\\caption{{data table}}\n"
            f"{rows_new}\n\\end{{longtable}}\n"
            "\\end{document}\n"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        i_dims = out.find("Global dimensions")
        assert i_dims >= 0, "inserted dimensions table lost"
        # the old data table content must render (struck, or inline
        # per-cell marks when the delete/insert pair up by row key)
        # BEFORE the inserted metadata table
        i_stuck = out.find("\\sout")
        i_inline = out.find("old0")
        first_old = min(x for x in (i_stuck, i_inline) if x >= 0)
        assert first_old >= 0 and first_old < i_dims, (
            "retired table must precede the inserted metadata table"
        )

    def test_unrelated_adjacent_delete_insert_rows_stay_separate(self):
        # GitHub #1 follow-up (scene ADS LSA table): a deleted
        # variable row followed by an unrelated inserted attribute
        # row must NOT merge into one logical row. Rows are single
        # lines sharing only \hline after normalisation, which used
        # to satisfy the 35%-shared-line test and silently drop the
        # old row's content (kiso_445 "appeared out of the blue")
        old_row = (
            "\\rowcolor{lightcyan} \\textbf{kiso\\-\\_445} & "
            "BRDF parameter kiso at 445 nm. & short & "
            "\\emph{valid\\_min} to \\emph{valid\\_max} & dim1, dim1\\\\"
        )
        new_row = (
            "\\rowcolor{lightcyan} \\textbf{kiso\\-\\_445} & "
            "VIIRS BRDF albedo parameter kiso_445 & short & "
            "\\emph{valid\\_min} to \\emph{valid\\_max} & lat, lon\\\\"
        )
        other_row = "coordinates & Coordinate variables (CF) & string & lat lon & \\\\"
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\begin{longtable}{lllll}\n"
            "name & desc & type & range & dims\\\\\n"
            + old_row + "\n\\end{longtable}\n\\end{document}\n"
        )
        new_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\begin{longtable}{lllll}\n"
            "name & desc & type & range & dims\\\\\n"
            + other_row + "\n" + new_row
            + "\n\\end{longtable}\n\\end{document}\n"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        # the old row content must survive visibly (struck), not be
        # commented away into nothing
        assert "BRDF parameter kiso at 445 nm" in out
        assert "\\DIFdel{BRDF parameter kiso at 445 nm.}" in out
        # new content present too
        assert "VIIRS BRDF albedo parameter kiso_445" in out

    def test_typo_level_row_rewrite_still_merges(self):
        # genuinely similar rows (same logical row, small fix) keep
        # the single merged-row treatment
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\begin{longtable}{ll}\n\\caption{vars}\\\\\n"
            "a & Inpu variable\\\\\n\\end{longtable}\n\\end{document}\n"
        )
        new_src = old_src.replace("Inpu", "Input")
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        # one row carrying both marks (single-line rows merge at
        # cell level), not a deleted + re-added pair
        assert "\\DIFdel{Inpu variable}" in out
        assert "\\DIFadd{Input variable}" in out
        assert out.count("Inpu variable") == 1

    def test_keyed_row_pair_merges_across_intervening_rows(self):
        # a deleted attribute row (units) whose inserted counterpart
        # sits several edits away - the reordered variable row
        # changed in between - renders as ONE row with per-cell
        # marks.  Realistic shape: the first data row absorbs the
        # \endfoot\endlastfoot table tail, everything before it is
        # matched skeleton.
        head = (
            "\\caption{Global variables}\\\\\n"
            "\\hline\n"
            "\\rowcolor{lightgray} \\textbf{Variable} & \\textbf{Description} & "
            "\\textbf{Type} & \\textbf{Range} & \\textbf{Dimensions} \\\\\n"
            "\\hline\n\\endhead\n"
            "\\hline\\multicolumn{5}{r}{Continued on next page} \\\\\n"
            "\\endfoot\n\\endlastfoot\n"
        )
        old_row = (
            "\\hline\\rowcolor{lightcyan} \\textbf{lat} & Latitude & float & "
            "\\emph{valid\\_min} to \\emph{valid\\_max} & dim1, dim1\\\\\n"
        )
        new_row = old_row.replace("dim1, dim1", "lat, lon")
        tail_rows = "\\hline\nunits & Physical units & string & degrees\\_north & \\\\\n"
        tail_rows_new = tail_rows.replace("degrees\\_north", "degrees")
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "{\\scriptsize\n\\begin{longtable}{lllll}\n" + head + old_row + tail_rows +
            "\\hline\n\\end{longtable}}\n\\end{document}\n"
        )
        new_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "{\\scriptsize\n\\begin{longtable}{lllll}\n" + head + new_row + tail_rows_new +
            "\\hline\n\\end{longtable}}\n\\end{document}\n"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        # the units value cell merge: old struck, new waved, in one row
        assert "\\DIFdel{degrees\\_north}" in out
        assert "\\DIFadd{degrees}" in out
        # the row keys themselves are NOT struck or waved
        assert "\\DIFdel{units}" not in out

    def test_no_duplicated_endfoot_in_marked_row(self):
        # the parse absorbs \endfoot\endlastfoot into the first data
        # row node; when that row pair merges, the skeleton foot
        # must be emitted exactly once and no \color may leak
        # between \endfoot and \endlastfoot
        head = (
            "\\caption{Global dimensions}\\\\\n"
            "\\hline\n"
            "\\rowcolor{lightgray} \\textbf{Name} & \\textbf{Size} \\\\\n"
            "\\hline\n\\endhead\n"
            "\\hline\\multicolumn{2}{r}{Continued on next page} \\\\\n"
            "\\endfoot\n\\endlastfoot\n"
        )
        old_row = "\\hline\\rowcolor{lightcyan} \\textbf{lat} & 2400\\\\\n"
        tail_rows = "\\hline\nunits & string & degrees\\\\_north & \\\\\n"
        new_src_base = (
            "\\documentclass{article}\n\\begin{document}\n"
            "{\\scriptsize\n\\begin{longtable}{ll}\n" + head + old_row + tail_rows +
            "\\hline\n\\end{longtable}}\n\\end{document}\n"
        )
        old_src = new_src_base
        new_src = new_src_base.replace(
            "2400", "dynamic"
        ).replace("degrees\\\\_north", "degrees")
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        assert out.count("\\endfoot") <= 1
        # no \color between the skeleton markers
        import re as _re

        assert not _re.search(r"\\endfoot\s*\\color", out)
        assert not _re.search(r"\\endlastfoot\s*\\color", out)

    def test_retired_row_first_column_stays_visible(self):
        # \textbf{name} in the first cell of a retired row must be
        # struck (wrap inside the argument), not commented out
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\begin{longtable}{ll}\n\\caption{vars}\\\\\n"
            "\\hline\n\\rowcolor{lightcyan} \\textbf{kiso\\_445} & 0.3\\\\\n"
            "\\hline\nunits & string & degrees & \\\\\n"
            "\\end{longtable}\n\\end{document}\n"
        )
        new_src = old_src.replace("\\rowcolor{lightcyan} \\textbf{kiso\\_445} & 0.3\\\\\n", "")
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        assert out.count("kiso") == 1
        assert "\\textbf{\\DIFdel{kiso\\_\\allowbreak 445}}" in out or (
            "\\textbf{\\DIFdel{kiso\\_445}}" in out
        )  # breakpoint after \_ inserted for column wrapping

    def test_strike_through_guarded_caption_e2e(self):
        # strike_through applies the guard to real caption lines
        struck = tables.strike_through("Grid of something:\n\\caption{Regular grid, layer and parameter}\nvar & x\\\\\n")
        assert "\\caption[]{\\texorpdfstring{\\sout{Regular grid, layer and parameter}}{}}" in struck
        # the word after the caption is still struck normally
        assert "\\sout{var}" in struck


class TestHeadMarkerInMergedRow:
    def test_endfirsthead_dropped_from_mid_body_merge(self):
        # the NEW table's first data row can glue the longtable head
        # material onto its lead ("\hline \endfirsthead \hline").
        # When that row merges inline with an old data row, the glued
        # \endfirsthead must not land mid-body: a second end-marker
        # re-classifies the already-rendered rows as first-head
        # material and longtable swallows them into one giant head
        # (near-blank page, "Overfull \vbox ... while \output is
        # active"). The \hline grid separators survive.
        head = (
            "\\caption{vars}\\\\\n"
            "\\hline\n"
            "\\rowcolor{lightgray} \\textbf{Variable} & \\textbf{Type} \\\\\n"
            "\\hline\n\\endfirsthead\n"
            "\\hline\n"
        )
        old_rows = (
            "\\hline\nfoo & float32 \\\\\n"
            "\\hline\nbar & float64 \\\\\n"
            "\\hline\n\\end{longtable}\n"
        )
        # the new first data row carries the head markers in its lead
        # (parser-glued); the rows are keyed-merged inline
        new_rows = (
            "\\hline \\endfirsthead \\hline\nfoo & float \\\\\n"
            "\\hline\nbar & float \\\\\n"
            "\\hline\n\\end{longtable}\n"
        )
        old_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "{\\scriptsize\n\\begin{longtable}{ll}\n" + head + old_rows + "}\n\\end{document}\n"
        )
        new_src = (
            "\\documentclass{article}\n\\begin{document}\n"
            "{\\scriptsize\n\\begin{longtable}{ll}\n" + head + new_rows + "}\n\\end{document}\n"
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        # the head marker appears exactly once: the genuine head
        assert out.count("\\endfirsthead") == 1
        # the grid separator of the glued lead survives
        assert "\\DIFdel{float32}" in out
        assert "\\DIFadd{float}" in out


class TestGroupedRowPairing:
    """Group-scoped same-key row pairing (variable/attribute tables).

    A grouped table repeats its attribute keys (``units``,
    ``\_FillValue``, ...) in every variable group; key equality
    alone cannot tell one group's ``units`` from another's. Unique
    keys (the variable rows) act as group barriers: pairing across
    a barrier word-diffs the units value of one variable into
    another variable's row.
    """

    @staticmethod
    def _doc(rows: str) -> str:
        return (
            "\\documentclass{article}\n\\usepackage{longtable}\n"
            "\\begin{document}\n"
            "\\begin{longtable}{lllll}\n"
            "\\hline\n\\rowcolor{lightgray} \\textbf{Name} & "
            "\\textbf{Desc} & \\textbf{Type} & \\textbf{Value} & "
            "\\textbf{Dims}\\\\\n\\hline\n\\endfirsthead\n\\endfoot\n\\endlastfoot\n"
            + rows
            + "\\hline\n\\end{longtable}\n\\end{document}\n"
        )

    @staticmethod
    def _old_group(name: str, unit: str) -> str:
        return (
            f"\\rowcolor{{lightcyan}} \\textbf{{{name}}} & {name} desc & float"
            f" & vr & d1,d2 \\\\\n\\hline\n"
            "long\\_name & Variable long name & string & See & \\\\\n\\hline\n"
            f"units & Physical units & string & {unit} & \\\\\n\\hline\n"
            "valid\\_min & Valid minimum & float & -9.9e+36 & \\\\\n\\hline\n"
            "valid\\_max & Valid maximum & float & 9.9e+36 & \\\\\n\\hline\n"
            "\\_FillValue & Missing value & float & 9.9e+36 & \\\\\n\\hline\n"
        )

    @staticmethod
    def _new_group(name: str) -> str:
        return (
            f"\\rowcolor{{lightcyan}} \\textbf{{{name}}} & {name} desc & float"
            f" & vr & lon,lat \\\\\n\\hline\n"
            "scale\\_factor & Scale factor & double & 1 & \\\\\n\\hline\n"
            "add\\_offset & Offset & double & 0 & \\\\\n\\hline\n"
            "valid\\_range & Valid range & float & 0 to 3.4e38 & \\\\\n\\hline\n"
            "units & Physical units & string & - & \\\\\n\\hline\n"
            "\\_FillValue & Missing value & float & -1.1e-38 & \\\\\n\\hline\n"
            "long\\_name & Variable long name & string & See & \\\\\n\\hline\n"
        )

    def test_units_merge_stays_within_group(self, ):
        old = (
            self._old_group("var\\-\\_alpha", "Pa")
            + self._old_group("var\\-\\_beta", "K")
            + self._old_group("var\\-\\_gamma", "m2 s-2")
        )
        new = (
            self._new_group("var\\-\\_alpha")
            + self._new_group("var\\-\\_beta")
            + self._new_group("var\\-\\_gamma")
        )
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # each group's units row merges with ITS OWN old value (struck)
        for unit in ("Pa", "K", "m2 s-2"):
            assert f"\\DIFdel{{{unit}}}" in out
        # cross-group mixing would strike one unit value in a merged
        # row that sits under a DIFFERENT variable anchor: check the
        # units-per-group pairing by position - the struck Pa row
        # must precede the var_beta anchor row
        i_pa = out.find("\\DIFdel{Pa}")
        i_beta = out.find("\\textbf{var\\-\\_beta}")
        assert i_pa != -1 and i_beta != -1 and i_pa < i_beta
        i_k = out.find("\\DIFdel{K}")
        i_gamma = out.find("\\textbf{var\\-\\_gamma}")
        assert i_k != -1 and i_gamma != -1 and i_k < i_gamma

    def test_unique_key_row_never_paired_across_barrier(self):
        # a deleted attribute key with its counterpart only in a
        # LATER group must not pair across the variable-anchor row:
        # it retires (struck) instead of merging with the wrong group
        old = self._old_group("solo", "Hz") + self._old_group("other", "Pa")
        new = self._new_group("solo") + self._new_group("other")
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # Hz belongs to the first group: whatever its treatment, it
        # must appear before the second group's anchor
        i_hz = out.find("Hz")
        i_other = out.find("\\textbf{other}")
        assert i_hz != -1 and i_hz < i_other

    def test_reordered_groups_keep_attribute_rows_with_own_anchor(self):
        # variables reordered between revisions: plain sequence
        # alignment cannot express a "move", so old groups' attribute
        # rows would straddle other groups' new anchors. Block
        # alignment must word-diff each matched group pair so every
        # struck unit value stays inside its own group.
        old = (
            self._old_group("var\\-\\_alpha", "Pa")
            + self._old_group("var\\-\\_beta", "K")
            + self._old_group("var\\-\\_gamma", "m2 s-2")
        )
        new = (
            self._new_group("var\\-\\_alpha")
            + self._new_group("var\\-\\_gamma")
            + self._new_group("var\\-\\_beta")
        )
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # every struck unit value must sit INSIDE its own group: the
        # nearest anchor name before each struck unit is that
        # group's. Anchor rows render either struck (DIFdel) or
        # matched verbatim, and always contain the plain group
        # name, so search plain names.
        for unit, name in (
            ("Pa", "alpha"),
            ("K", "beta"),
            ("m2 s-2", "gamma"),
        ):
            i_unit = out.find(f"\\DIFdel{{{unit}}}")
            assert i_unit != -1, f"missing struck {unit}"
            before = out[:i_unit]
            nearest = max(
                before.rfind(t) for t in ("alpha", "beta", "gamma")
            )
            assert nearest != -1
            assert before.rfind(name) == nearest, (
                f"{unit} merged into the wrong group's rows"
                f" (nearest anchor is not {name})"
            )

    def test_reordered_groups_retire_whole_group_not_straddle(self):
        # a group dropped during a reorder must retire wholesale:
        # its struck rows stay together, not entangled with the added
        # rows of the group that took its position
        old = (
            self._old_group("var\\-\\_alpha", "Pa")
            + self._old_group("var\\-\\_beta", "K")
            + self._old_group("var\\-\\_gamma", "m2 s-2")
        )
        new = (
            self._new_group("var\\-\\_alpha")
            + self._new_group("var\\-\\_new")
            + self._new_group("var\\-\\_beta")
        )
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # gamma retired wholesale: from its LAST occurrence to the
        # end of the body no DIFadd of the inserted groups appears
        # inside the retired block, and no alpha/beta rows are
        # entangled in it
        gamma_all = [
            m.start() for m in __import__("re").finditer("gamma", out)
        ]
        assert gamma_all, "missing gamma"
        start = gamma_all[-1]
        end = out.find("\\end{longtable}", start)
        retired = out[start:end]
        assert "\\DIFadd" not in retired, (
            "retired group's rows are entangled with inserted rows"
        )


class TestGroupedPairingRegressions:
    """Regressions pinned from the rendered grouped-table pipeline.

    Each test reproduces a concrete mis-merge observed on a
    generated variable/attribute table diff: attribute rows merged
    across group boundaries, duplicated, or wholesale-retired even
    though a same-group counterpart existed. All of them share the
    TestGroupedRowPairing table scaffold (lightcyan + textbf
    variable anchors, plain attribute rows).
    """

    # ---- scaffold (same conventions as TestGroupedRowPairing) ----

    @staticmethod
    def _doc(rows: str) -> str:
        return (
            "\\documentclass{article}\n\\usepackage{longtable}\n"
            "\\begin{document}\n"
            "\\begin{longtable}{lllll}\n"
            "\\hline\n\\rowcolor{lightgray} \\textbf{Name} & "
            "\\textbf{Desc} & \\textbf{Type} & \\textbf{Value} & "
            "\\textbf{Dims}\\\\\n\\hline\n\\endfirsthead\n\\endfoot\n\\endlastfoot\n"
            + rows
            + "\\hline\n\\end{longtable}\n\\end{document}\n"
        )

    @staticmethod
    def _old_coord(name: str, desc: str, unit: str) -> str:
        # the old-style coordinate group: 6 rows, anchor first
        return (
            f"\\rowcolor{{lightcyan}} \\textbf{{{name}}} & {desc} & float"
            f" & vr & d1 \\\\\n\\hline\n"
            "long\\_name & Variable long name & string & See & \\\\\n\\hline\n"
            f"units & Physical units & string & {unit} & \\\\\n\\hline\n"
            "valid\\_min & Valid minimum & float & -9.9e+36 & \\\\\n\\hline\n"
            "valid\\_max & Valid maximum & float & 9.9e+36 & \\\\\n\\hline\n"
            "\\_FillValue & Missing value & float & 9.9e+36 & \\\\\n\\hline\n"
        )

    @staticmethod
    def _new_coord(name: str, desc: str) -> str:
        # the new-style coordinate group: 2 rows, anchor + _FillValue
        return (
            f"\\rowcolor{{lightcyan}} \\textbf{{{name}}} & {desc} & float"
            f" & vr & {name} \\\\\n\\hline\n"
            "\\_FillValue & Missing value & float & -1.1e-38 & \\\\\n\\hline\n"
        )

    @staticmethod
    def _old_group(name: str, unit: str) -> str:
        return TestGroupedRowPairing._old_group(name, unit)

    @staticmethod
    def _new_group(name: str) -> str:
        return TestGroupedRowPairing._new_group(name)

    # ---- regressions ----

    def test_moved_match_group_fill_value_merges_in_own_group(self):
        """lev case: matched means-scoped anchor + guides killer.

        A moved/rewritten group whose anchor appears as Delete AND
        Insert must not lose barrier status: the NEXT group's
        retired ``_FillValue`` must not merge with this group's
        inserted ``_FillValue``, and this group's own value change
        must render inside its own rows (before the next group's
        anchor), not after them.
        """
        old = (
            self._old_coord("lev", "Model levels", "")
            + self._old_coord("lev\\-\\_2", "Retired levels", "")
            + self._old_group("tmem", "s")
        )
        new = (
            self._new_coord("lev", "Model levels")
            + self._new_coord("tmem", "Model time")
        )
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # lev's _FillValue value change merges within the lev group:
        # struck old value and blue new value in ONE row that
        # appears BEFORE the retired lev_2 anchor
        i_merge = out.find("\\DIFdel{9.9e+36}")
        assert i_merge != -1, "lev FillValue change not merged"
        i_lev2 = out.find("lev\\-\\_\\allowbreak 2")
        assert i_lev2 != -1
        assert i_merge < i_lev2, (
            "fill-value merge rendered after the retired lev_2 anchor"
        )
        # the retired lev_2 group must NOT absorb a blue fill value
        retired = out[i_lev2 : out.find("tmem", i_lev2)]
        assert "DIFadd{-1.1e-38}" not in retired

    def test_inserted_new_group_rows_not_merged_into_retired_group(self):
        """cloud_optical_thickness / lat2 case.

        A retired group followed by whole-brand-new groups
        (no old counterpart): the retired group's ``_FillValue``
        delete must stay fully struck - it must not pair with the
        new group's ``_FillValue`` insert that follows it.
        """
        old = (
            self._old_group("cloud\\-\\_op", "")
            + self._old_coord("old\\-\\_aux", "Aux", "Pa")
        )
        new = (
            self._new_coord("lat2", "Latitude 2")
            + self._new_coord("lon2", "Longitude 2")
        )
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # the retired cloud group retires wholesale: its rows are
        # all fully struck - no merged row pairs its ``_FillValue``
        # with the inserted lat2 group's fill value. A cross-group
        # merge shows up as a plain-key fill-value row carrying
        # BOTH the struck old and blue new value; the retired rows
        # instead render the WHOLE row struck (key included).
        import re

        merged_fill = re.findall(
            r"FillValue & Missing value & float &"
            r"\\DIFdelbegin \\DIFdel\{9\.9e\+36\}",
            out,
        )
        assert len(merged_fill) == 1, (
            f"expected exactly one merged fill-value row (lat2's), "
            f"got {len(merged_fill)}"
        )
        struck_fill = re.findall(
            r"\\DIFdel\{\\_\\allowbreak FillValue\}", out
        )
        assert struck_fill, "retired cloud group lost its struck rows"
        # the retired group's struck fill row must not contain a
        # blue value on the same source line as the struck one
        for m in re.finditer(
            r"\\DIFdel\{\\_\\allowbreak FillValue\}[^\\n]*", out
        ):
            assert "\\DIFadd" not in m.group(0), (
                "retired fill-value row was merged with a new group's insert"
            )

    def test_insert_first_group_merges_attributes(self):
        """surface_pressure case: insert-first emission order.

        A rewritten group can emit its INSERT rows first and its
        old attribute DELETES after; the pre-pass must pair them
        anyway or the attribute renders twice (blue insert + struck
        retire) with no in-place merge.
        """
        old = self._old_group("press", "Pa") + self._old_group("geo", "m2 s-2")
        new = self._new_group("press") + self._new_group("geo")
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # units merged in place: struck Pa and blue - in ONE row
        i_pa = out.find("\\DIFdel{Pa}")
        i_dash = out.find("\\DIFadd{-}")
        assert i_pa != -1 and i_dash != -1
        # the units row renders once, merged - no duplicate retired
        # copy of the units row after the merged one. The retired
        # copy would carry a struck "Physical units" cell while the
        # merged copy keeps it plain: count plain occurrences of the
        # attribute text spanning old groups.
        assert out.count("Physical units") == 2  # one per group, merged
        # _FillValue merged in the same way
        merged_rows = [
            m.start()
            for m in __import__("re").finditer(r"\\_FillValue & Missing", out)
        ]
        assert len(merged_rows) == 2  # one per group, not one per side

    def test_one_off_attribute_does_not_split_group(self):
        """chlor_a case: unique-key attribute as false anchor.

        A group holding an attribute no other group repeats
        (chlor_a's scale_factor) must stay ONE group: its units row
        pairs with the new units row (in-place value merge) instead
        of wholesale retire + re-add.
        """
        old = (
            "\\rowcolor{lightcyan} \\textbf{chl\\-\\_a} & Chlorophyll & float"
            " & vr & d1,d2 \\\\\n\\hline\n"
            "long\\_name & Variable long name & string & See & \\\\\n\\hline\n"
            "units & Physical units & string & mg m-3 & \\\\\n\\hline\n"
            "scale\\_factor & Scale factor & float & 1 & \\\\\n\\hline\n"
            "valid\\_max & Valid maximum & float & 1000 & \\\\\n\\hline\n"
            "\\_FillValue & Missing value & float & 9.9e+36 & \\\\\n\\hline\n"
        ) + self._old_group("var\\-\\_next", "Pa")
        new = (
            "\\rowcolor{lightcyan} \\textbf{chl\\-\\_a} & Chlorophyll & float"
            " & vr & lat,lon \\\\\n\\hline\n"
            "long\\_name & Variable long name & string & See & \\\\\n\\hline\n"
            "units & Physical units & string & milligram m-3 & \\\\\n\\hline\n"
            "scale\\_factor & Scale factor & double & 1 & \\\\\n\\hline\n"
            "valid\\_range & Valid range & float & 0 to 1000 & \\\\\n\\hline\n"
            "\\_FillValue & Missing value & float & -1.1e-38 & \\\\\n\\hline\n"
        ) + self._new_group("var\\-\\_next")
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # the units value changed in place (merge), not retire+re-add
        assert "\\DIFdel{mg m-3}" in out
        assert "\\DIFadd{milligram m-3}" in out
        # a wholesale re-add would strike the WHOLE units row: the
        # plain "Physical units" cells of the group render once
        i_del = out.find("\\DIFdel{mg m-3}")
        i_add = out.find("\\DIFadd{milligram m-3}")
        # merged row: plain key cell stays plain
        assert i_add != -1 and i_del != -1 and i_del < i_add

    def test_truncated_anchor_group_retires_wholesale(self):
        """organic-matter case: like-named truncated anchor.

        An old group whose anchor text differs from the new group's
        (truncated/renamed, e.g. ``...mater`` vs ``...matter``):
        the old group must retire wholesale with NO merged blue
        attribute rows inside it (the duplicate black
        units/_FillValue merge-row confusion).
        """
        old = (
            self._old_group("hyd\\-\\_org\\-\\_mater", "kg kg-1")
            + self._old_group("hyd\\-\\_ph\\-\\_org\\-\\_matter", "kg kg-1")
            + self._old_group("sulf", "kg kg-1")
        )
        new = (
            self._new_group("hyd\\-\\_org\\-\\_matter")
            + self._new_group("hyd\\-\\_ph\\-\\_org\\-\\_matter")
            + self._new_group("sulf")
        )
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # the retired truncated group: no blue inserted rows inside
        # (struck anchors render allowbreak-wrapped)
        import re

        i_ret = out.find("org\\-\\_\\allowbreak mater}}")
        assert i_ret != -1, "truncated-anchor group did not retire"
        # the retired block runs to the next group's anchor row
        # (matched phobic group renders \textbf{hyd\-\_ph\-\_...})
        m_next = re.search(
            r"\\textbf\{hyd\\-\\_ph", out[i_ret:]
        )
        assert m_next is not None
        retired = out[i_ret : i_ret + m_next.start()]
        # the retire run itself must end before the wholesale insert of
        # the matched new group (\DIFaddbegin) — a merged row would
        # place \DIFadd INSIDE the retire run, i.e. before it
        first_add = retired.find("\\DIFaddbegin")
        assert first_add != -1 and "\\DIFadd" not in retired[:first_add], (
            "retired truncated group absorbed merged rows of the new group"
        )

    def test_group_tags_pin_interleaved_group_alignment(self):
        """Renders of interleaved groups stay pair-scoped (tags).

        The group tag stamped by the grouped-row aligner scopes
        same-key pairing exactly: with two rewritten groups where
        alignments interleave deletes and inserts heavily, every
        merged units value must sit inside its own group's rows.
        """
        # two groups, both rewritten, both with same attribute keys
        old = self._old_group("gg\\-\\_one", "Pa") + self._old_group(
            "gg\\-\\_two", "K"
        )
        new = self._new_group("gg\\-\\_one") + self._new_group("gg\\-\\_two")
        out = diff_documents(self._doc(old), self._doc(new), inject_preamble=False).marked_up
        # both groups' _FillValue merge exactly once each: a merged
        # row keeps a plain key cell and carries both values; a
        # retire+re-add pair would render the row twice (once fully
        # struck, once fully blue) instead
        import re

        merged_fill = re.findall(
            r"FillValue & Missing value & float &"
            r"\\DIFdelbegin \\DIFdel\{9\.9e\+36\}",
            out,
        )
        assert len(merged_fill) == 2, (
            f"expected 2 merged fill-value rows, got {len(merged_fill)}"
        )
        # struck units values stay inside their own groups (same
        # nearest-anchor discipline as the reorder test)
        for unit, name in (("Pa", "one"), ("K", "two")):
            i_unit = out.find(f"\\DIFdel{{{unit}}}")
            assert i_unit != -1, f"missing struck {unit}"
            before = out[:i_unit]
            nearest = max(
                before.rfind(t) for t in ("gg\\-\\_one", "gg\\-\\_two")
            )
            assert name in out[nearest : nearest + 12], (
                f"{unit} merged into the wrong group"
            )

    def test_decorated_macro_cell_strikes_not_just_colours(self):
        r"""Range-cell case: \emph{..} cells must strike, not just colour.

        A changed cell containing only decoration macros with
        pure-text arguments (``\emph{valid\_min} to
        \emph{valid\_max}``) is LR-safe inside \DIFdel/\DIFadd:
        font commands survive \sout/\uwave. It must render with the
        inline strike markup, not degrade to the colour-switch-only
        fallback (\DIFdelbegin..\DIFdelend), which paints the old
        text red without striking it.
        """
        old_src = (
            "\\documentclass{article}\\begin{document}\n"
            "\\begin{longtable}{ll}\n\\caption{vars}\\\\\n"
            "\\rowcolor{lightcyan} \\textbf{lat} & Latitude & float & "
            "\\emph{valid\\_min} to \\emph{valid\\_max} & d1\\\\\n"
            "\\end{longtable}\\end{document}\n"
        )
        new_src = old_src.replace(
            "\\emph{valid\\_min} to \\emph{valid\\_max} & d1",
            "defined by \\emph{valid\\_range} & lat",
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        # the old range cell is STRUCK inside \DIFdel{..}, not merely
        # coloured by the block fallback
        assert "\\DIFdel{\\emph{valid\\_min} to \\emph{valid\\_max}}" in out
        # no colour-only degradation for the pair
        assert "\\DIFdelbegin{} \\emph{valid" not in out
        # new side waves its markup too
        assert "\\DIFadd{defined by \\emph{valid\\_range}}" in out

    def test_text_to_empty_cell_change_is_marked(self):
        r"""Text->empty cell change must strike the old cell.

        When a cell's content is removed entirely (the regenerated
        spec leaves the Range column blank), the per-cell merge used
        to emit the OLD cell verbatim with no markup at all: the
        retired value silently appeared as unchanged text. It must
        render struck red (and its counterpart, empty->text, wavy
        blue).
        """
        old_src = (
            "\\documentclass{article}\\begin{document}\n"
            "\\begin{longtable}{lll}\n"
            "z0 & 0 & 0 \\\\\n"
            "z1 & 1 & 1 \\\\\n"
            "\\rowcolor{lightcyan} \\textbf{day} & Date of Day & "
            "\\emph{valid\\_min} to \\emph{valid\\_max} \\\\\n"
            "w & 5 & 6 \\\\\n"
            "\\end{longtable}\\end{document}\n"
        )
        new_src = old_src.replace(
            "\\emph{valid\\_min} to \\emph{valid\\_max} \\\\",
            " \\\\",
        )
        out = diff_documents(old_src, new_src, inject_preamble=False).marked_up
        assert "\\DIFdel{\\emph{valid\\_min} to \\emph{valid\\_max}}" in out
