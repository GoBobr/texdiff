"""Contract tests for line-wise verbatim markup (COLORLISTINGS style).

Changed ``lstlisting`` environments merge into one environment whose
lines carry ``%DIF <`` / ``%DIF >`` markers (hidden by the DIFcode
listings language); deleted verbatim environments are commented out
with ``%DIFDELCMD`` so nothing renders.
"""

from __future__ import annotations

from texdiff.align import Delete, Insert, Match, Modify
from texdiff.emit import (
    PREAMBLE_TEMPLATE,
    _add_alsolanguage,
    _color_lines,
    _comment_out,
    _split_verbatim,
    render,
)
from texdiff.nodes import Node, env, text_node


def verbatim_env(name: str, body: str) -> Node:
    """Build a verbatim env node the way the parser produces them."""
    text = f"\\begin{{{name}}}\n{body}\\end{{{name}}}"
    return Node(kind="env", text=text, name=name, atom=True)


class TestVerbatimModify:
    def test_single_merged_environment(self):
        old = verbatim_env("lstlisting", "a\nb\n")
        new = verbatim_env("lstlisting", "a\nc\n")
        out = render([Match(node=text_node("")), Modify(old=old, new=new)])
        # exactly ONE listing, not one per revision
        assert out.count("\\begin{lstlisting}") == 1
        assert out.count("\\end{lstlisting}") == 1

    def test_line_markers_hidden_by_difcode(self):
        old = verbatim_env("lstlisting", "old line\n")
        new = verbatim_env("lstlisting", "new line\n")
        out = render([Modify(old=old, new=new)])
        assert "%DIF < old line" in out
        assert "%DIF > new line" in out
        # the markers are interpreted by the DIFcode language
        assert "alsolanguage=DIFcode" in out

    def test_equal_lines_kept_verbatim_no_markers(self):
        old = verbatim_env("lstlisting", "same\n")
        new = verbatim_env("lstlisting", "same\n")
        # identical text is a Match, but the Modify path with equal
        # bodies (e.g. only the optional argument changed) must not
        # mark every line either
        modified_old = Node(
            kind="env",
            text="\\begin{lstlisting}[language=C++]\nsame\n\\end{lstlisting}",
            name="lstlisting",
            atom=True,
        )
        out = render([Modify(old=modified_old, new=new)])
        assert "%DIF" not in out

    def test_mod_markers_wrap_the_listing(self):
        old = verbatim_env("lstlisting", "x\n")
        new = verbatim_env("lstlisting", "y\n")
        out = render([Modify(old=old, new=new)])
        assert out.startswith("\\DIFmodbegin\n")
        assert out.rstrip().endswith("\\end{lstlisting}\\DIFmodend")

    def test_identical_bodies_unchanged(self):
        body = "\\begin{lstlisting}[language=C]\nint x;\n\\end{lstlisting}"
        old = Node(kind="env", text=body, name="lstlisting", atom=True)
        new = Node(kind="env", text=body, name="lstlisting", atom=True)
        out = render([Modify(old=old, new=new)])
        assert out == body

    def test_unsplittable_env_falls_back_to_block_wrap(self):
        # no newline after \begin: _split_verbatim returns None and
        # the generic Modify path takes over - the old listing is
        # commented out (verbatim deletion policy), the new one gets
        # the visible add-block form
        old = Node(
            kind="env", text="\\begin{lstlisting}x\\end{lstlisting}",
            name="lstlisting", atom=True,
        )
        new = Node(
            kind="env", text="\\begin{lstlisting}y\\end{lstlisting}",
            name="lstlisting", atom=True,
        )
        out = render([Modify(old=old, new=new)])
        assert "\\DIFmodbegin" not in out
        assert out.startswith("%DIFDELCMD < \\begin{lstlisting}x")
        assert "\\DIFaddbegin" in out and "\\DIFaddend" in out

    def test_inserted_listing_keeps_wavy_block_form(self):
        # a purely inserted listing has no old counterpart: it takes
        # the regular add-block form (visible, blue)
        new = verbatim_env("lstlisting", "fresh\n")
        out = render([Insert(new=new)])
        assert "\\DIFaddbegin" in out
        assert "\\begin{lstlisting}" in out


class TestDeletedVerbatim:
    def test_deleted_listing_commented_out(self):
        old = verbatim_env("lstlisting", "line 1\nline 2\n")
        out = render([Delete(old=old)])
        assert "%DIFDELCMD < \\begin{lstlisting}" in out
        assert "%DIFDELCMD < line 1" in out
        assert "%DIFDELCMD < line 2" in out
        # nothing renderable left: no markup macros, no bare lines
        assert out.lstrip().startswith("%DIFDELCMD")

    def test_deleted_verbatim_env_commented_out(self):
        old = verbatim_env("verbatim", "raw text\n")
        out = render([Delete(old=old)])
        assert "%DIFDELCMD < raw text" in out
        assert "\\DIFdel" not in out

    def test_blank_interior_line_gets_bare_marker(self):
        old = Node(
            kind="env",
            text="\\begin{lstlisting}\nx\n\n\\end{lstlisting}",
            name="lstlisting",
            atom=True,
        )
        out = render([Delete(old=old)])
        assert "%DIFDELCMD < x\n%DIFDELCMD <\n" in out
        assert out.endswith("%DIFDELCMD < \\end{lstlisting}\n")


class TestSplitVerbatim:
    def test_split_plain(self):
        node = verbatim_env("lstlisting", "a\nb\n")
        parts = _split_verbatim(node)
        assert parts is not None
        begin, lines, end = parts
        assert begin == "\\begin{lstlisting}"
        assert lines == ["a", "b", ""]
        assert end == "\\end{lstlisting}"

    def test_split_with_options(self):
        node = Node(
            kind="env",
            text="\\begin{lstlisting}[language=C]\nx\n\\end{lstlisting}",
            name="lstlisting",
            atom=True,
        )
        begin, lines, end = _split_verbatim(node)
        assert begin == "\\begin{lstlisting}[language=C]"

    def test_split_malformed_returns_none(self):
        node = Node(
            kind="env", text="\\begin{lstlisting} x \\end{lstlisting}",
            name="lstlisting", atom=True,
        )
        assert _split_verbatim(node) is None

    def test_split_no_name_returns_none(self):
        node = Node(kind="env", text="\\begin{lstlisting}\n\\end{lstlisting}", atom=True)
        assert _split_verbatim(node) is None


class TestAlsolanguage:
    def test_appended_to_existing_options(self):
        out = _add_alsolanguage("\\begin{lstlisting}[language=C]")
        assert out == "\\begin{lstlisting}[language=C,alsolanguage=DIFcode]"

    def test_created_when_missing(self):
        out = _add_alsolanguage("\\begin{lstlisting}")
        assert out == "\\begin{lstlisting}[alsolanguage=DIFcode]"

    def test_unparseable_begin_unchanged(self):
        assert _add_alsolanguage("not a begin") == "not a begin"


class TestPreambleTemplate:
    def test_difcode_language_defined(self):
        assert "\\lstdefinelanguage{DIFcode}" in PREAMBLE_TEMPLATE
        assert "moredelim=[il][\\color{red}\\sout]{\\%DIF\\ <\\ }" in PREAMBLE_TEMPLATE

    def test_mod_markers_noop(self):
        assert "\\providecommand{\\DIFmodbegin}{}" in PREAMBLE_TEMPLATE
        assert "\\providecommand{\\DIFmodend}{}" in PREAMBLE_TEMPLATE

    def test_listings_package_required(self):
        assert "\\RequirePackage{listings}" in PREAMBLE_TEMPLATE


class TestRowMarkupHelpers:
    def test_comment_out_marks_each_line(self):
        out = _comment_out("first\nsecond\n\n")
        assert out == "%DIFDELCMD < first \\%%\n%DIFDELCMD < second \\%%\n"

    def test_comment_out_blank_text_untouched(self):
        assert _comment_out("  \n") == "  \n"

    def test_color_lines_prefixes_content_lines(self):
        out = _color_lines("hello\n\\hline\n")
        assert out == "\\color{blue} hello\n\\hline\n"

    def test_color_lines_restarts_after_cells(self):
        out = _color_lines("a & b\n")
        assert out == "\\color{blue} a & \\color{blue}  b\n"

    def test_color_lines_structure_line_untouched(self):
        out = _color_lines("    \\hline\n")
        assert out == "    \\hline\n"
