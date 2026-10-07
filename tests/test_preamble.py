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

import re

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


class TestMacroBodyMarkup:
    """Regression: body-invoked table macros get line-level markup.

    The equal ranges of the body alignment carry old-side (i1/i2)
    and new-side (j1/j2) indices separately; the kept bytes must be
    sliced from the NEW side. Slicing from the old side re-emitted
    an earlier new-revision line (duplicating a change-record row)
    and dropped the body tail - the macro's closing ``\\hline}`` -
    leaving ``\\def\\name{`` open so the injected DIF preamble ended
    up inside the definition ("Illegal parameter number").
    """

    OLD = """\\documentclass{article}
\\begin{document}
\\begin{longtable}{ll}
\\changerecord
\\end{longtable}
\\end{document}
"""

    @staticmethod
    def _doc(rows: str) -> str:
        return (
            "\\documentclass{article}\n"
            "\\def\\changerecord{%s}\n"
            "\\begin{document}\n"
            "\\begin{longtable}{ll}\n\\changerecord\n\\end{longtable}\n"
            "\\end{document}\n" % rows
        )

    def _run(self, old_rows: str, new_rows: str) -> str:
        r = diff_documents(self._doc(old_rows), self._doc(new_rows))
        return r.marked_up

    def test_closing_line_kept_when_rows_inserted(self):
        # rows appended before the closing \hline}: the equal tail
        # segment must keep the NEW body's closing line
        old_rows = "1 & 2022 & & Init \\\\\n\\hline"
        new_rows = (
            "1 & 2022 & & Init \\\\\n"
            "& & & NEW LINE ONE \\\\\n"
            "& & & NEW LINE TWO \\\\\n"
            "\\hline"
        )
        out = self._run(old_rows, new_rows)
        assert "\\hline}" in out
        # the changed macro's closing brace must balance the opener
        start = out.index("\\def\\changerecord{")
        body = out[start : out.index("\\begin{document}")]
        assert body.count("{") == body.count("}")

    def test_no_duplicated_row(self):
        # the buggy equal-slice re-emitted the first new-side line
        # of the trailing equal segment a second time
        old_rows = (
            "1 & 2022 & & Init \\\\\n"
            "2 & 2023 & & DCR1 \\\\\n"
            "\\hline"
        )
        new_rows = (
            "1 & 2022 & & Init \\\\\n"
            "& & & extra detail \\\\\n"
            "2 & 2023 & & DCR1 \\\\\n"
            "\\hline"
        )
        out = self._run(old_rows, new_rows)
        assert out.count("2 & 2023 & & DCR1") == 1
        assert "\\hline}" in out

    def test_definition_line_never_coloured(self):
        # a \color painted on the macro's own definition line would
        # execute while the PREAMBLE is being read - no group scopes
        # it there - leaking blue over the entire document (table
        # captions, running headers, page numbers)
        old_rows = "1 & 2022 & & Init \\\\\n\\hline"
        new_rows = "1 & 2022 & & Init \\\\\n2 & 2023 & & DCR1 \\\\\n\\hline"
        out = self._run(old_rows, new_rows)
        assert "\\color{blue} \\def\\changerecord{" not in out
        start = out.index("\\def\\changerecord{")
        assert not out[start:].startswith("\\color")

    def test_migrated_macro_keeps_unchanged_rows_black(self):
        # the macro defined in the old BODY and in the new PREAMBLE:
        # the old body must be searched for the counterpart rows, or
        # every row (including unchanged ones) is painted blue
        old = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\def\\changerecord{%\n"
            "1 & 2022 & & Init \\\\\n"
            "}\n"
            "\\begin{longtable}{ll}\n\\changerecord\n\\end{longtable}\n"
            "\\end{document}\n"
        )
        new = (
            "\\documentclass{article}\n"
            "\\def\\changerecord{%\n"
            "1 & 2022 & & Init \\\\\n"
            "2 & 2023 & & DCR1 \\\\\n"
            "}\n"
            "\\begin{document}\n"
            "\\begin{longtable}{ll}\n\\changerecord\n\\end{longtable}\n"
            "\\end{document}\n"
        )
        out = diff_documents(old, new).marked_up
        pre = out[: out.index("\\begin{document}")]
        # the unchanged row 1 keeps no colour declaration ahead of it
        assert "\\color{blue} 1 & 2022" not in pre
        # the new row 2 is coloured per cell
        assert re.search(
            r"\\color\{blue\} 2 & \\color\{blue\}\s+2023", pre
        )
        # the old body-side definition no longer typesets: it would
        # silently redefine the macro and override the marked one
        # (it must survive only inside %DIFDELCMD comments)
        body = out[out.index("\\begin{document}") :]
        body_code = "\n".join(
            l for l in body.splitlines() if not l.lstrip().startswith("%")
        )
        assert "\\def\\changerecord{" not in body_code
