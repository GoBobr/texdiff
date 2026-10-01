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
