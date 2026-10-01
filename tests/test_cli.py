"""Command line contract tests."""

from __future__ import annotations

import subprocess
import sys

import pytest

from texdiff.cli import main

OLD = "\\documentclass{article}\\begin{document}old text\\end{document}\n"
NEW = "\\documentclass{article}\\begin{document}new text\\end{document}\n"

# word-level diff: only the changed word carries markup, " text" stays
EXPECTED_SUBSTRINGS = ("\\DIFdel{old}", "\\DIFadd{new}", " text\\end{document}")


@pytest.fixture()
def docs(tmp_path):
    old = tmp_path / "old.tex"
    new = tmp_path / "new.tex"
    old.write_text(OLD)
    new.write_text(NEW)
    return old, new


class TestCLI:
    def test_stdout_output(self, docs, capsys):
        old, new = docs
        rc = main([str(old), str(new)])
        assert rc == 0
        out = capsys.readouterr().out
        for s in EXPECTED_SUBSTRINGS:
            assert s in out

    def test_output_file(self, docs, tmp_path):
        old, new = docs
        out = tmp_path / "diff.tex"
        rc = main([str(old), str(new), "-o", str(out)])
        assert rc == 0
        for s in EXPECTED_SUBSTRINGS:
            assert s in out.read_text(encoding="utf-8")

    def test_stats_on_stderr(self, docs, capsys):
        old, new = docs
        main([str(old), str(new), "--stats"])
        err = capsys.readouterr().err
        assert "modified" in err or "added" in err

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as e:
            main(["--version"])
        assert e.value.code == 0

    def test_console_script_registered(self, docs):
        # the entry point must exist (pyproject [project.scripts]);
        # plain text check: tomllib is stdlib only since 3.11
        from pathlib import Path

        pyproject = (
            Path(__file__).resolve().parents[1] / "pyproject.toml"
        ).read_text()
        assert 'texdiff = "texdiff.cli:main"' in pyproject
