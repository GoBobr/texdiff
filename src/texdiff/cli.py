"""Command line interface: ``texdiff OLD.tex NEW.tex`` → marked-up diff.tex."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="texdiff",
        description="AST-driven semantic diff for LaTeX documents.",
    )
    parser.add_argument("old", help="old revision .tex file")
    parser.add_argument("new", help="new revision .tex file")
    parser.add_argument("-o", "--output", default="-", help="output path (default: stdout)")
    parser.add_argument(
        "--stats",
        action="store_true",
        help="print change statistics to stderr",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compile the marked-up diff once and exit non-zero on failure",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="scratch directory for --check (default: temp dir)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from .api import diff_files

    if args.check:
        from pathlib import Path

        from .check import check_compiles

        workdir = Path(args.workdir) if args.workdir else None
        ok, log = check_compiles(args.old, args.new, work_dir=workdir)
        if not ok:
            print("texdiff --check FAILED: marked-up diff does not compile", file=sys.stderr)
            for line in log.splitlines():
                if line.startswith("!") or "l." in line[:3]:
                    print(line, file=sys.stderr)
            return 2
        print("texdiff --check OK: diff compiles", file=sys.stderr)
        if args.output != "-":
            from pathlib import Path

            result = diff_files(args.old, args.new)
            Path(args.output).write_text(result.marked_up, encoding="utf-8")
        if args.stats:
            result = diff_files(args.old, args.new)
            print(result.stats, file=sys.stderr)
        return 0

    result = diff_files(args.old, args.new)

    if args.output == "-":
        sys.stdout.write(result.marked_up)
    else:
        from pathlib import Path

        Path(args.output).write_text(result.marked_up, encoding="utf-8")

    if args.stats:
        print(result.stats, file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
