"""Contract tests for the --check mode: compile the diff, fail loudly."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from texdiff.check import check_compiles, run_pdflatex
from texdiff.cli import main

OLD = "\\documentclass{article}\n\\begin{document}\nold text\n\\end{document}\n"
NEW = "\\documentclass{article}\n\\begin{document}\nnew text\n\\end{document}\n"


@pytest.fixture()
def docs(tmp_path):
    old = tmp_path / "old.tex"
    new = tmp_path / "new.tex"
    old.write_text(OLD)
    new.write_text(NEW)
    return old, new


class TestRunPdflatex:
    def test_returns_completed_process(self, tmp_path):
        tex = tmp_path / "d.tex"
        tex.write_text(NEW)
        if not _have_pdflatex():  # pragma: no cover
            pytest.skip("pdflatex not installed")
        cp = run_pdflatex(tex, tmp_path)
        assert cp.returncode in (0, 1)

    def test_exit_code_zero_on_success(self, tmp_path):
        if not _have_pdflatex():  # pragma: no cover
            pytest.skip("pdflatex not installed")
        tex = tmp_path / "d.tex"
        tex.write_text(NEW)
        cp = run_pdflatex(tex, tmp_path)
        assert cp.returncode == 0

    def test_passes_nonstopmode(self, tmp_path):
        if not _have_pdflatex():  # pragma: no cover
            pytest.skip("pdflatex not installed")
        tex = tmp_path / "d.tex"
        tex.write_text(NEW)
        with mock.patch("subprocess.run", wraps=subprocess.run) as spy:
            run_pdflatex(tex, tmp_path)
            args = spy.call_args[0][0]
            assert "-interaction=nonstopmode" in args

    def test_timeout_propagates(self, tmp_path):
        tex = tmp_path / "d.tex"
        tex.write_text(NEW)
        with mock.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("pdflatex", 5)
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                run_pdflatex(tex, tmp_path)


class TestCheckCompiles:
    def test_true_on_compilable_diff(self, docs, tmp_path):
        if not _have_pdflatex():  # pragma: no cover
            pytest.skip("pdflatex not installed")
        old, new = docs
        ok, log = check_compiles(old, new, work_dir=tmp_path)
        assert ok is True
        assert log != ""

    def test_false_and_log_on_broken_markup(self, docs, tmp_path, monkeypatch):
        old, new = docs
        # simulate the failure mode texdiff exists to prevent: markup
        # that breaks compilation
        monkeypatch.setattr(
            "texdiff.check.diff_files",
            lambda o, n: _FakeResult(marked_up="\\undefinedmacro{broken"),
        )
        if not _have_pdflatex():  # pragma: no cover
            pytest.skip("pdflatex not installed")
        ok, log = check_compiles(old, new, work_dir=tmp_path)
        assert ok is False
        assert "Undefined control sequence" in log or "!" in log


class _FakeResult:
    def __init__(self, marked_up):
        self.marked_up = marked_up


class TestCLICheck:
    def test_check_flag_exits_zero_on_clean(self, docs, capsys):
        if not _have_pdflatex():  # pragma: no cover
            pytest.skip("pdflatex not installed")
        old, new = docs
        rc = main([str(old), str(new), "--check"])
        assert rc == 0

    def test_check_failure_exits_nonzero(self, docs, capsys, monkeypatch):
        old, new = docs
        monkeypatch.setattr(
            "texdiff.check.diff_files",
            lambda o, n: _FakeResult(marked_up="\\undefinedmacro{broken"),
        )
        if not _have_pdflatex():  # pragma: no cover
            pytest.skip("pdflatex not installed")
        rc = main([str(old), str(new), "--check"])
        assert rc == 2

    def test_check_writes_pdf_to_workdir(self, docs, tmp_path):
        if not _have_pdflatex():  # pragma: no cover
            pytest.skip("pdflatex not installed")
        old, new = docs
        rc = main([str(old), str(new), "--check", "--workdir", str(tmp_path)])
        assert rc == 0
        assert (tmp_path / "texdiff-check.pdf").exists()


def _have_pdflatex() -> bool:
    from shutil import which

    return which("pdflatex") is not None
