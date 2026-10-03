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


class TestPreambleMacroMarkup:
    """Body-invoked \\def macros with table-row bodies (GitHub #1)."""

    def _doc(self, rows, tail_rows=()):
        rows_tex = "\n".join(
            f"{v} & 01/01/2026 & ID & desc of change {v}\\\\" for v in rows
        )
        tail = "\n".join(
            f"{v} & 01/01/2026 & ID & desc of change {v}\\\\" for v in tail_rows
        )
        # closing structure rows sit BETWEEN the common prefix rows
        # and the appended rows, like a real change record
        return (
            "\\documentclass{article}\n"
            "\\def\\changerecord{%s\n\\hline}\n"
            "\\begin{document}\n\\changerecord\n\\end{document}\n" % (rows_tex,)
        )

    def test_def_macro_keeps_closing_brace_when_body_grows(self):
        # the new revision APPENDS rows to a \\def-held change record
        # table: the emitted def must still close. The bug paired the
        # old-side equal range with new-side indices shifted by the
        # insertion length, dropping the "\\hline}" closer and never
        # closing the macro ("Illegal parameter number in definition")
        old = self._doc(["1", "2"])
        new = self._doc(["1", "2"], tail_rows=["3", "4", "5"])
        # rebuild new so the appended rows come after the closer
        rows = "\n".join(
            f"{v} & 01/01/2026 & ID & desc of change {v}\\\\"
            for v in ["1", "2", "3", "4", "5"]
        )
        new = (
            "\\documentclass{article}\n"
            "\\def\\changerecord{%s\n\\hline}\n"
            "\\begin{document}\n\\changerecord\n\\end{document}\n" % rows
        )
        out = diff_documents(old, new, inject_preamble=False).marked_up
        i = out.find("\\def\\changerecord{")
        assert i >= 0
        # track brace depth over the def the way TeX reads it
        j = i + len("\\def\\changerecord")
        depth = 0
        opened = False
        k = j
        while k < len(out):
            c = out[k]
            if c == "\\":
                k += 2
                continue
            if c == "%":
                nl = out.find("\n", k)
                k = len(out) if nl < 0 else nl + 1
                continue
            if c == "{":
                depth += 1
                opened = True
            elif c == "}":
                depth -= 1
                if opened and depth == 0:
                    break
            k += 1
        assert opened and depth == 0, (
            "\\def\\changerecord never closes in the emitted preamble"
        )

    def test_added_rows_of_def_macro_coloured(self):
        rows_old = "\n".join(
            f"1 & 01/01/2026 & ID & original change\\\\" for _ in range(1)
        )
        rows_new = "\n".join(
            ["1 & 01/01/2026 & ID & another change\\\\"]
            + [f"{v} & 01/01/2026 & ID & brand new row {v}\\\\" for v in (2, 3)]
        )
        old = (
            "\\documentclass{article}\n\\def\\changerecord{%s\n\\hline}\n"
            "\\begin{document}\n\\changerecord\n\\end{document}\n" % rows_old
        )
        new = (
            "\\documentclass{article}\n\\def\\changerecord{%s\n\\hline}\n"
            "\\begin{document}\n\\changerecord\n\\end{document}\n" % rows_new
        )
        out = diff_documents(old, new, inject_preamble=False).marked_up
        assert "brand new row" in out
        assert "\\color{blue}" in out
