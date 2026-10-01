"""Contract tests for texdiff.parse: LaTeX source → Node tree."""

from __future__ import annotations

import pytest

from texdiff.nodes import Node
from texdiff.parse import ParseError, parse


class TestRoundTrip:
    """INvariant: concatenating node texts reproduces the source."""

    def test_plain_text_round_trips(self):
        src = "Hello world.\n"
        nodes = parse(src)
        assert "".join(n.text for n in nodes) == src

    def test_macro_round_trips(self):
        src = "A \\textbf{bold} word.\n"
        assert "".join(n.text for n in parse(src)) == src

    def test_environment_round_trips(self):
        src = "\\begin{itemize}\\item one \\item two\\end{itemize}\n"
        assert "".join(n.text for n in parse(src)) == src

    def test_math_round_trips(self):
        src = "Value $x^2 + 1$ and display:\\[a+b\\]\n"
        assert "".join(n.text for n in parse(src)) == src

    def test_verbatim_round_trips(self):
        src = "\\begin{verbatim}wysiwyg %DIF> \\raw\\end{verbatim}\n"
        assert "".join(n.text for n in parse(src)) == src

    def test_comment_round_trips(self):
        src = "text % a comment\nmore\n"
        assert "".join(n.text for n in parse(src)) == src


class TestStructure:
    def test_text_node_kind(self):
        nodes = parse("hello")
        assert nodes[0].kind == "text"

    def test_env_node_name_and_children(self):
        nodes = parse("\\begin{itemize}\\item a\\end{itemize}")
        env = next(n for n in nodes if n.kind == "env")
        assert env.name == "itemize"
        assert len(env.children) > 0
        assert env.children[0].text.startswith("\\item")

    def test_env_node_verbatim_is_atomic(self):
        nodes = parse("\\begin{verbatim}raw\\end{verbatim}")
        vb = next(n for n in nodes if n.kind == "env")
        assert vb.atom is True
        assert vb.children == []

    def test_document_env_is_recursable(self):
        nodes = parse("\\begin{document}body\\end{document}")
        doc = next(n for n in nodes if n.kind == "env")
        assert doc.name == "document"
        assert doc.atom is False

    def test_group_children(self):
        nodes = parse("a{b c}d")
        grp = next(n for n in nodes if n.kind == "group")
        assert "{b c}" == grp.text
        assert any(c.text == "b c" for c in grp.children)

    def test_inline_math_is_atomic(self):
        nodes = parse("$x + y$ text")
        math = next(n for n in nodes if n.kind == "math")
        assert math.atom is True
        assert math.text == "$x + y$"

    def test_comment_is_separate_node(self):
        nodes = parse("a % note\nb")
        comment = [n for n in nodes if n.kind == "comment"]
        assert len(comment) == 1
        # pylatexenc includes the trailing newline in the comment span
        # (it is part of the comment token); round-trip relies on it
        assert comment[0].text == "% note\n"


class TestRobustness:
    def test_unbalanced_brace_raises(self):
        # tolerant parsing swallows a lone '{' into a faux group, but
        # our contract is to reject malformed documents
        with pytest.raises(ParseError):
            parse("{ \\begin{itemize} \\item \\end{itemize")

    def test_unclosed_environment_raises(self):
        with pytest.raises(ParseError):
            parse("\\begin{itemize} \\item")

    def test_tolerant_parsing_used_for_real_docs(self):
        # constructs which appear in real documents but are not
        # expressible in pylatexenc's strict mode must parse tolerantly
        src = "\\makecell{a\\\\b} & \\cite[see][p.3]{x2020}"
        nodes = parse(src)
        assert "".join(n.text for n in nodes) == src

    def test_empty_source(self):
        assert parse("") == []

    def test_parse_error_message_mentions_position(self):
        with pytest.raises(ParseError, match="malformed|cannot parse"):
            parse("\\begin{itemize} no end")
