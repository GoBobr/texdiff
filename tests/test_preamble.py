"""Contract tests for the preamble policy (v1).

Policy (latexdiff-compatible):

* the marked-up document uses the **new** revision's preamble - the
  diff must render with current styling, not old styling;
* preamble *changes* (new packages, changed macros) are not marked up
  inline (they would not survive \\DIF markup); they are counted in
  ``stats.preamble_changes`` so a caller can report them;
* texdiff's markup macros are injected into the new preamble.
"""

from __future__ import annotations

import pytest

from texdiff.api import diff_documents
from texdiff.preamble import new_preamble, preamble_tuple, split_preamble


OLD_DOC = """\\documentclass{article}
\\usepackage{amsmath}
\\begin{document}
Body text.
\\end{document}
"""

NEW_DOC = """\\documentclass{report}
\\usepackage{amsmath}
\\usepackage{longtable}
\\begin{document}
Body text changed.
\\end{document}
"""


class TestSplitPreamble:
    def test_split_returns_preamble_and_body(self):
        pre, body, post = split_preamble(NEW_DOC)
        assert pre.startswith("\\documentclass{report}")
        assert "\\begin{document}" in pre
        assert body.startswith("Body text changed.")
        assert post.startswith("\\end{document}")

    def test_fragment_without_document_env(self):
        pre, body, post = split_preamble("Just body.\n")
        assert pre == ""
        assert body == "Just body.\n"
        assert post == ""

    def test_round_trip(self):
        pre, body, post = split_preamble(NEW_DOC)
        assert pre + body + post == NEW_DOC


class TestPreamblePolicy:
    def test_marked_up_uses_new_preamble(self):
        r = diff_documents(OLD_DOC, NEW_DOC)
        assert "\\documentclass{report}" in r.marked_up
        assert "\\documentclass{article}" not in r.marked_up
        assert "\\usepackage{longtable}" in r.marked_up

    def test_body_still_diffed(self):
        r = diff_documents(OLD_DOC, NEW_DOC)
        assert "\\DIFdel{" in r.marked_up and "\\DIFadd{" in r.marked_up

    def test_identical_preambles_not_counted(self):
        r = diff_documents(OLD_DOC, OLD_DOC.replace("Body", "Body2"))
        assert r.stats.preamble_changes == 0

    def test_preamble_changes_counted(self):
        r = diff_documents(OLD_DOC, NEW_DOC)
        assert r.stats.preamble_changes > 0


class TestHelpers:
    def test_preamble_tuple(self):
        pre, post = preamble_tuple(NEW_DOC)
        assert pre == split_preamble(NEW_DOC)[0]
        assert post == split_preamble(NEW_DOC)[2]

    def test_new_preamble_prefers_new(self):
        assert new_preamble(NEW_DOC) == split_preamble(NEW_DOC)[0]
