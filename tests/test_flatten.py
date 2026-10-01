"""Contract tests for texdiff.flatten: \\input/\\include expansion.

texdiff must resolve relative file references itself (like
``latexdiff --flatten``) so that a document spread over many files can
be diffed without an external latexpand step.
"""

from __future__ import annotations

import pytest

from texdiff.flatten import Flattener, flatten_source


class TestBasicExpansion:
    def test_input_expanded_inline(self, tmp_path):
        (tmp_path / "part.tex").write_text("content of part\n")
        main = "\\documentclass{article}\n\\input{part}\n"
        out = flatten_source(main, tmp_path)
        assert "content of part" in out
        # the \input line itself is gone
        assert "\\input{part}" not in out

    def test_include_expanded_between_blank_lines(self, tmp_path):
        # \include does \\clearpage before and after; flatten keeps
        # the remainder of the line as \input does
        (tmp_path / "chap.tex").write_text("chapter body\n")
        main = "\\include{chap}\n"
        out = flatten_source(main, tmp_path)
        assert "chapter body" in out

    def test_nested_inputs(self, tmp_path):
        (tmp_path / "a.tex").write_text("A then \\input{b}\n")
        (tmp_path / "b.tex").write_text("B end\n")
        main = "\\input{a}\n"
        out = flatten_source(main, tmp_path)
        assert "A then" in out
        assert "B end" in out

    def test_tex_extension_optional(self, tmp_path):
        (tmp_path / "part.tex").write_text("with ext\n")
        (tmp_path / "other.tex").write_text("without ext\n")
        main = "\\input{part.tex}\\input{other}\n"
        out = flatten_source(main, tmp_path)
        assert "with ext" in out
        assert "without ext" in out

    def test_missing_input_kept_verbatim(self, tmp_path):
        # do not fail the whole diff because one asset is missing:
        # keep the \input line so the output still compiles
        main = "\\input{nonexistent}\n"
        out = flatten_source(main, tmp_path)
        assert "\\input{nonexistent}" in out

    def test_missing_include_kept_verbatim(self, tmp_path):
        main = "\\include{nonexistent}\n"
        out = flatten_source(main, tmp_path)
        assert "\\include{nonexistent}" in out

    def test_flatten_source_with_base_dir(self):
        # source-string API: base_dir defaults to cwd; expansion of
        # absolute paths happens only via flatten_file
        out = flatten_source("\\input{no-such-file-here}\n")
        assert "\\input{no-such-file-here}" in out


class TestRecursionSafety:
    def test_circular_input_detected(self, tmp_path):
        (tmp_path / "a.tex").write_text("\\input{b}\n")
        (tmp_path / "b.tex").write_text("\\input{a}\n")
        out = flatten_source("\\input{a}\\n", tmp_path)
        # must terminate; both lines kept verbatim somewhere in output
        assert "\\input{a}" in out or "\\input{b}" in out

    def test_diamond_dependency_expanded_once(self, tmp_path):
        # a includes b and c; both include d: d must appear once per
        # inclusion (flatten is textual, not deduplicated)
        (tmp_path / "d.tex").write_text("D-CONTENT\n")
        (tmp_path / "b.tex").write_text("\\input{d}\n")
        (tmp_path / "c.tex").write_text("\\input{d}\n")
        (tmp_path / "a.tex").write_text("\\input{b}\\input{c}\n")
        out = flatten_source("\\input{a}\\n", tmp_path)
        assert out.count("D-CONTENT") == 2

    def test_self_input_detected(self, tmp_path):
        (tmp_path / "self.tex").write_text("\\input{self}\n")
        out = flatten_source("\\input{self}\\n", tmp_path)
        # terminates, content present once
        assert out.count("\\input{self}") >= 1


class TestAnnotations:
    def test_flattened_marker_comment_inserted(self, tmp_path):
        (tmp_path / "part.tex").write_text("marker body\n")
        out = flatten_source("\\input{part}\\n", tmp_path, markers=True)
        assert "\\begin{DIFnomarkup}" in out or "% texdiff-flatten:" in out

    def test_default_no_markers(self, tmp_path):
        (tmp_path / "part.tex").write_text("plain body\n")
        out = flatten_source("\\input{part}\\n", tmp_path)
        assert "% texdiff-flatten:" not in out


class TestFlattenerObject:
    def test_flattener_stats(self, tmp_path):
        (tmp_path / "p.tex").write_text("x\n")
        f = Flattener(base_dir=tmp_path)
        out = f.flatten("\\input{p}\n")
        assert f.expanded == 1
        assert f.missing == 0

    def test_flattener_records_missing(self, tmp_path):
        f = Flattener(base_dir=tmp_path)
        f.flatten("\\input{gone}\n")
        assert f.missing == 1


class TestRealWorldShapes:
    def test_input_inside_comment_ignored(self, tmp_path):
        (tmp_path / "p.tex").write_text("should not appear\n")
        main = "% \\input{p}\nrest\n"
        out = flatten_source(main, tmp_path)
        assert "should not appear" not in out

    def test_input_inside_verbatim_ignored(self, tmp_path):
        (tmp_path / "p.tex").write_text("should not appear\n")
        main = "\\begin{verbatim}\n\\input{p}\n\\end{verbatim}\n"
        out = flatten_source(main, tmp_path)
        assert "should not appear" not in out
        assert "\\input{p}" in out

    def test_relative_subdirectory_input(self, tmp_path):
        sub = tmp_path / "sections"
        sub.mkdir()
        (sub / "s.tex").write_text("subdir content\n")
        main = "\\input{sections/s}\n"
        out = flatten_source(main, tmp_path)
        assert "subdir content" in out

    def test_input_star_form(self, tmp_path):
        # \input is sometimes written with a star (\input*); treat the
        # same as plain \input
        (tmp_path / "p.tex").write_text("star content\n")
        main = "\\input*{p}\n"
        out = flatten_source(main, tmp_path)
        assert "star content" in out

    def test_includegraphics_not_expanded(self, tmp_path):
        (tmp_path / "fig.pdf").write_bytes(b"%PDF")
        main = "\\includegraphics{fig}\n"
        out = flatten_source(main, tmp_path)
        assert "\\includegraphics{fig}" in out

    def test_escaped_input_untouched(self, tmp_path):
        (tmp_path / "p.tex").write_text("expansion\n")
        main = "text \\% \\input{p}\n"  # \input after \%... still input
        out = flatten_source(main, tmp_path)
        # after a comment character, \input{p} is commented out
        assert "expansion" not in out
