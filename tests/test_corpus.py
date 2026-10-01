"""Golden-corpus regression tests.

Synthetic but realistic documents exercising the constructs that break
latexdiff in the wild: restructured longtables (header/footer rows),
reordered + inserted rows, changed list items, math, verbatim blocks
and labels. The corpus is fully synthetic - no user data.

Guards:

* identical documents round-trip byte-exactly;
* changed documents produce compilable markup (when pdflatex exists);
* the diff of the corpus is stable (golden properties, not golden
  bytes - byte-level goldens churn with formatting).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from texdiff import diff_documents

CORPUS = Path(__file__).parent / "corpus"

OLD = (CORPUS / "sample-old.tex").read_text(encoding="utf-8")
NEW = (CORPUS / "sample-new.tex").read_text(encoding="utf-8")

HAS_PDFLATEX = shutil.which("pdflatex") is not None


class TestRoundTrip:
    def test_identical_old_round_trips(self):
        r = diff_documents(OLD, OLD)
        assert r.marked_up == OLD
        assert r.stats.changed == 0

    def test_identical_new_round_trips(self):
        r = diff_documents(NEW, NEW)
        assert r.marked_up == NEW
        assert r.stats.changed == 0


class TestDiffProperties:
    @pytest.fixture(scope="class")
    def result(self):
        return diff_documents(OLD, NEW)

    def test_changed_paragraph_word_marked(self, result):
        # three -> four (word-level inline markup)
        assert "\\DIFdel{three}" in result.marked_up
        assert "\\DIFadd{four}" in result.marked_up

    def test_math_unchanged(self, result):
        # the equation must appear verbatim, no markup inside it
        assert "\\epsilon = \\sqrt{(\\Delta x)^2 + (\\Delta y)^2}" in result.marked_up

    def test_verbatim_unchanged(self, result):
        assert "band 1: offset = 0.42" in result.marked_up
        # verbatim body must not carry any markup
        verb = result.marked_up.split("calibration_report_v2.txt")[1][:70]
        assert "\\DIFadd" not in verb and "\\DIFdel" not in verb

    def test_table_row_insertion_marked(self, result):
        assert "nir" in result.marked_up
        assert "uncertainty" in result.marked_up
        # each inserted row keeps its own region (no gluing)
        add_regions = result.marked_up.split("\\DIFaddbegin")
        assert len(add_regions) >= 3

    def test_list_item_change_and_insert(self, result):
        assert "monthly" in result.marked_up
        assert "quarterly" in result.marked_up
        assert "parallax" in result.marked_up

    def test_markup_balanced(self, result):
        m = result.marked_up
        assert m.count("\\DIFaddbegin") == m.count("\\DIFaddend")
        assert m.count("\\DIFdelbegin") == m.count("\\DIFdelend")

    def test_table_structure_intact(self, result):
        assert result.marked_up.count("\\begin{longtable}") == 2
        assert result.marked_up.count("\\end{longtable}") == 2
        assert result.marked_up.count("\\endfirsthead") == 1

    def test_labels_unchanged(self, result):
        assert "\\label{tab:bands}" in result.marked_up
        assert "\\label{sec:format}" in result.marked_up


@pytest.mark.skipif(not HAS_PDFLATEX, reason="pdflatex not installed")
class TestCorpusCompiles:
    def test_marked_up_diff_compiles(self, tmp_path):
        import subprocess

        tex = tmp_path / "diff.tex"
        tex.write_text(diff_documents(OLD, NEW).marked_up, encoding="utf-8")
        cp = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-output-directory",
                str(tmp_path),
                str(tex),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert cp.returncode == 0, cp.stdout[-2000:]
