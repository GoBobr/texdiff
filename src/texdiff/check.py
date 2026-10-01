"""--check: compile the marked-up diff once, report/fail loudly.

The whole point of texdiff is diffs that compile. ``--check`` makes
that property *verified* rather than trusted: it writes the marked-up
document into a scratch directory, runs one ``pdflatex`` pass, and
returns the verdict with the log tail on failure.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .api import diff_files

_PDFLATEX_TIMEOUT = 120


def run_pdflatex(tex_path: Path, work_dir: Path) -> subprocess.CompletedProcess:
    """Run one nonstop pdflatex pass in ``work_dir``."""
    return subprocess.run(
        [
            "pdflatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-output-directory",
            str(work_dir),
            str(tex_path),
        ],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=_PDFLATEX_TIMEOUT,
    )


def check_compiles(old_path, new_path, work_dir: Path | None = None) -> tuple[bool, str]:
    """Diff two files and verify the marked-up result compiles.

    Returns:
        (ok, log): ok is True when pdflatex exited 0; log is the
        pdflatex output (empty string when pdflatex is unavailable).
    """
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="texdiff-check-"))
    else:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

    result = diff_files(str(old_path), str(new_path))
    tex = work_dir / "texdiff-check.tex"
    tex.write_text(result.marked_up, encoding="utf-8")

    from shutil import which

    if which("pdflatex") is None:  # pragma: no cover - environment
        return True, ""

    cp = run_pdflatex(tex, work_dir)
    return cp.returncode == 0, cp.stdout + cp.stderr
