"""Contract tests for texdiff.emit: edit script → marked-up LaTeX."""

from __future__ import annotations

from texdiff.align import Delete, Insert, Match, Modify
from texdiff.emit import LatexdiffMarkup, render
from texdiff.nodes import Node, env, text_node


class TestMatch:
    def test_unchanged_text_emitted_verbatim(self):
        node = text_node("  kept text  ")
        out = render([Match(node=node)])
        assert out == "  kept text  "


class TestWhitespaceHoisting:
    def test_markup_hoists_surrounding_whitespace(self):
        node = text_node(" padded ")
        out = render([Insert(new=node)])
        assert out == " \\DIFadd{padded} "


class TestMarkup:
    def test_insert_wrapped_in_difadd(self):
        node = text_node("added block")
        out = render([Insert(new=node)])
        assert "\\DIFadd{" in out and "added block" in out

    def test_delete_wrapped_in_difdel(self):
        node = text_node("gone")
        out = render([Delete(old=node)])
        assert "\\DIFdel{" in out and "gone" in out

    def test_modify_renders_both_versions(self):
        old, new = text_node("old text"), text_node("new text")
        out = render([Modify(old=old, new=new)])
        assert "\\DIFdel{old text}" in out
        assert "\\DIFadd{new text}" in out

    def test_custom_markup(self):
        node = text_node("x")
        out = render([Insert(new=node)], markup=LatexdiffMarkup(add_open="[+", add_close="]"))
        assert "[+x]" in out


class TestEnvironment:
    def test_env_insert_keeps_structure(self):
        e = env("itemize", text_node("\\item new"))
        out = render([Insert(new=e)])
        assert "\\begin{itemize}" in out and "\\end{itemize}" in out

    def test_env_delete_renders_full_environment(self):
        e = env("quote", text_node("old quote"))
        out = render([Delete(old=e)])
        # environments take the block form: \sout cannot span \begin/\end
        assert "\\DIFdelbegin" in out and "\\DIFdelend" in out
        assert "\\begin{quote}" in out and "\\end{quote}" in out
        assert "old quote" in out


class TestInlineVsBlock:
    def test_macro_nodes_take_block_form(self):
        """\DIFadd{\cmd} would steal the argument braces - must not happen."""
        node = Node(kind="macro", text="\\section{Intro}", name="section")
        out = render([Insert(new=node)])
        assert "\\DIFaddbegin" in out
        assert "\\DIFadd{" not in out

    def test_multiline_text_takes_block_form(self):
        node = text_node("first\nsecond")
        out = render([Insert(new=node)])
        assert "\\DIFaddbegin" in out

    def test_argument_macro_in_text_takes_block_form(self):
        # merged/coalesced runs lose node kind; a macro with a brace
        # argument inside a text run must still take the block form
        node = text_node("\\section{Intro}")
        out = render([Insert(new=node)])
        assert "\\DIFaddbegin" in out

    def test_single_line_text_takes_inline_form(self):
        node = text_node("plain words")
        out = render([Insert(new=node)])
        assert "\\DIFadd{plain words}" in out


class TestPreamble:
    def test_preamble_template_provides_both_macros(self):
        from texdiff.emit import PREAMBLE_TEMPLATE

        assert "\\providecommand{\\DIFadd}" in PREAMBLE_TEMPLATE
        assert "\\providecommand{\\DIFdel}" in PREAMBLE_TEMPLATE
