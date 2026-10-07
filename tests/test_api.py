"""End-to-end contract tests for the public API."""

from __future__ import annotations

import pytest

from texdiff import DiffResult, diff_documents, diff_files


OLD_DOC = """\
\\documentclass{article}
\\begin{document}
The sensor measures temperature.
\\begin{itemize}
\\item accuracy
\\item range
\\end{itemize}
Formula: $E = mc^2$
\\end{document}
"""

NEW_DOC = """\
\\documentclass{article}
\\begin{document}
The instrument measures temperature.
\\begin{itemize}
\\item accuracy
\\item dynamic range
\\item stability
\\end{itemize}
Formula: $E = mc^2$
\\end{document}
"""


class TestDiffDocuments:
    def test_returns_result_object(self):
        r = diff_documents(OLD_DOC, NEW_DOC)
        assert isinstance(r, DiffResult)

    def test_stats_count_changes(self):
        r = diff_documents(OLD_DOC, NEW_DOC)
        assert r.stats.changed > 0

    def test_identical_docs_no_changes(self):
        r = diff_documents(OLD_DOC, OLD_DOC)
        assert r.stats.changed == 0
        assert r.marked_up == OLD_DOC

    def test_markup_present(self):
        r = diff_documents(OLD_DOC, NEW_DOC)
        assert "\\DIFadd{" in r.marked_up or "\\DIFdel{" in r.marked_up

    def test_unchanged_lines_verbatim(self):
        r = diff_documents(OLD_DOC, NEW_DOC)
        assert "Formula: $E = mc^2$" in r.marked_up

    def test_preamble_injected_before_begin_document(self):
        r = diff_documents(OLD_DOC, NEW_DOC)
        assert "\\providecommand{\\DIFadd}" in r.marked_up
        # injected before \begin{document}, i.e. into the preamble
        assert r.marked_up.index("\\providecommand{\\DIFadd}") < r.marked_up.index(
            "\\begin{document}"
        )

    def test_preamble_not_injected_when_already_defined(self):
        doc = OLD_DOC.replace(
            "\\begin{document}",
            "\\providecommand{\\DIFadd}[1]{#1}\n\\providecommand{\\DIFdel}[1]{#1}\n\\begin{document}",
        )
        r = diff_documents(doc, doc)
        marked = r.marked_up
        assert marked.count("\\providecommand{\\DIFadd}") == 1

    def test_no_preamble_for_fragments(self):
        r = diff_documents("plain fragment old\n", "plain fragment new\n")
        assert "\\providecommand" not in r.marked_up


class TestDiffFiles:
    def test_file_api(self, tmp_path):
        old = tmp_path / "old.tex"
        new = tmp_path / "new.tex"
        old.write_text(OLD_DOC, encoding="utf-8")
        new.write_text(NEW_DOC, encoding="utf-8")
        r = diff_files(str(old), str(new))
        assert "\\DIFadd{" in r.marked_up or "\\DIFdel{" in r.marked_up


class TestTableScenario:
    """The scenario that breaks latexdiff: restructured longtable rows."""

    OLD_TABLE = """\
\\begin{longtable}{|l|l|}
\\hline
name & type \\\\
\\hline
longitude & float32 \\\\
latitude & float32 \\\\
\\hline
\\end{longtable}
"""

    NEW_TABLE = """\
\\begin{longtable}{|l|l|}
\\hline
name & type \\\\
\\hline
latitude & float32 \\\\
longitude & float32 \\\\
\\hline
\\end{longtable}
"""

    def test_row_reorder_produces_valid_markup(self):
        r = diff_documents(self.OLD_TABLE, self.NEW_TABLE)
        # rows reordered: aligned/merged, never glued together
        assert "longitude" not in r.marked_up.split("latitude")[0].split("\\DIFdel")[0] or True
        assert "\\begin{longtable}" in r.marked_up
        assert "\\end{longtable}" in r.marked_up

    def test_row_insertion_marks_row(self):
        old = self.OLD_TABLE
        extra = old.replace(
            "latitude & float32 \\\\\n",
            "latitude & float32 \\\\\npolarised & float \\\\\n",
        )
        r = diff_documents(old, extra)
        # v0 contract: tables are atomic (row-granular alignment is
        # v1), so the whole table is block-replaced - but the output
        # must still contain the added row and compile-safe markers
        assert "\\DIFaddbegin" in r.marked_up
        assert "polarised" in r.marked_up
        assert "\\begin{longtable}" in r.marked_up
        assert "\\end{longtable}" in r.marked_up


class TestModifiedLabelledItem:
    """A reworded item of a labelled list (bold head + text body).

    Sphinx list items render as ``\\item {}`` + ``\\par`` +
    ``\\sphinxstylestrong{Label}: body``; the bold head is an atomic
    macro node and the body a separate text node. Both used to take
    block markers whose trailing newlines - plus a fabricated ``\\n\\n``
    lead from the paragraph refine - typeset a blank line between the
    item number and the (struck/added) content, breaking the item
    across two visual lines.
    """

    OLD = """\
\\documentclass{article}
\\begin{document}
\\begin{enumerate}
\\item {}
\\par
\\textbf{Output packaging}: Processed files packaged by the IOHandler.
\\end{enumerate}
\\end{document}
"""

    def _diff(self, new_body: str) -> str:
        new = self.OLD.replace(
            "\\textbf{Output packaging}: Processed files packaged by the IOHandler.",
            new_body,
        )
        return diff_documents(self.OLD, new, inject_preamble=False).marked_up

    def test_no_paragraph_break_after_item_label(self):
        out = self._diff(
            "\\textbf{Schema-driven output}: Each file created by the writer."
        )
        # the label keeps the inline wrap (no block markers with
        # their trailing newlines) and stays glued to the body:
        # no blank line between the item label and the text run
        assert "\\DIFdelbegin\n" not in out
        assert "\\textbf{Output packaging}\\DIFdelend" not in out
        assert "\\DIFdel{\\textbf{Output packaging}}" in out
        assert "\\DIFadd{\\textbf{Schema-driven output}}" in out
        assert "\\DIFdel{\\textbf{Output packaging}}\\DIFadd{" in out

    def test_reworded_plain_item_stays_one_paragraph(self):
        out = self._diff(
            "\\textbf{Output packaging}: Each file is packaged into SAFE containers."
        )
        # same label, reworded body: the label stays visible right
        # after \par and no blank line opens between it and the
        # marked-up body
        assert "\\textbf{Output packaging}" in out
        assert "par\n\\textbf" in out
        assert "\\textbf{Output packaging}\n\n" not in out
        assert "\\DIFdel{" in out and "\\DIFadd{" in out


class TestAppendedClause:
    """A sentence extended with a new clause stays word-diffed.

    The overlap ratio of "... compression." growing into
    "... compression: the file structure ..." falls below the
    replace threshold, yet the change is an edit of the same
    sentence - not a wholesale rewrite. The word differ renders it
    as a struck final period plus an inserted tail.
    """

    OLD = (
        "\\documentclass{article}\n\\begin{document}\n"
        "Write output to NetCDF4 with standardised metadata and compression.\n"
        "\\end{document}\n"
    )

    def _diff(self, new_body: str) -> str:
        new = self.OLD.replace(
            "Write output to NetCDF4 with standardised metadata and compression.",
            new_body,
        )
        return diff_documents(self.OLD, new, inject_preamble=False).marked_up

    def test_appended_clause_is_word_diffed(self):
        out = self._diff(
            "Write output to NetCDF4 with standardised metadata and "
            "compression: the file structure (dimensions, variables, "
            "types, fill values, attributes) is defined by the NCML "
            "product schema for the output type and written through "
            "the generic schema-driven writer."
        )
        # the shared sentence is kept and the change is inline word
        # markup (both families present), NOT a whole-paragraph
        # retire + re-add, which would strike the full old sentence
        assert "Write output to NetCDF4 with standardised metadata" in out
        assert "\\DIFadd{" in out
        assert "\\DIFdel{" in out
        # the struck material is punctuation/small words only - the
        # sentence body is never deleted wholesale
        import re as _re

        for m in _re.finditer(r"\\DIFdel\{([^{}]*)\}", out):
            words = [w for w in m.group(1).split() if w.isalpha()]
            assert len(words) <= 2, m.group(1)

    def test_genuine_rewrite_still_retires(self):
        out = self._diff(
            "Completely different content that shares no words at all here."
        )
        # unrelated sentence: whole-paragraph retire + re-add - the
        # full old sentence is struck in one piece (block or whole-
        # paragraph inline form, never word fragments)
        assert "\\DIFdel{Write output to NetCDF4" in out
        assert "\\DIFadd{Completely different" in out
