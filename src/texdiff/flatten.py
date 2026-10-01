"""\\input/\\include expansion: multi-file document → single source.

The equivalent of ``latexdiff --flatten`` / ``latexpand``, inside the
tool. texdiff parses per file, then expands references so the aligner
sees one document tree.

Design:

* textual (linear) expansion, matching latexpand's contract: no macro
  semantics, just file inclusion - what ``\\input`` does in TeX;
* files are resolved relative to the INCLUDING file's directory
  (TeX's kpathsea semantics simplified: the main file's dir, then the
  including chain);
* cycle protection: a file already on the expansion stack is kept
  verbatim instead of being recursed into (TeX would loop forever);
* missing files are kept verbatim rather than fatal - a broken
  ``\\input`` must not block the diff, and keeping the reference makes
  the output still compilable;
* comment lines and verbatim environments are skipped: a commented
  ``% \\input{x}`` is not an inclusion, and verbatim bodies must never
  be expanded.

The emitter later re-uses the skip logic for the same purpose (never
mark up inside verbatim).
"""

from __future__ import annotations

import re
from pathlib import Path

# \input{name} / \include{name}; name may lack the .tex extension
# (TeX appends it), may contain letters, /, -, _, . and spaces
_INPUT_RE = re.compile(r"\\(input|include)\*?\s*\{([^{}]+)\}")

# block of lines which must never be expanded: verbatim-like envs
_VERBATIM_ENVS = (
    "verbatim",
    "verbatim*",
    "lstlisting",
    "minted",
    "alltt",
    "filecontents",
    "filecontents*",
)

_MARKER_PREFIX = "% texdiff-flatten:"


class Flattener:
    """Expand ``\\input``/``\\include`` in a source string.

    Attributes:
        base_dir: directory files are resolved against.
        expanded: number of references successfully expanded.
        missing: number of references whose file did not exist
            (kept verbatim).
        markers: insert ``% texdiff-flatten:`` annotation comments
            around expanded content (for debugging / provenance).
    """

    def __init__(self, base_dir: Path | str | None = None, markers: bool = False):
        self.base_dir = Path(base_dir) if base_dir is not None else Path.cwd()
        self.markers = markers
        self.expanded = 0
        self.missing = 0

    def flatten(self, source: str) -> str:
        """Expand all \\input/\\include references in ``source``."""
        return self._expand(source, self.base_dir, stack=frozenset())

    def _expand(self, source: str, current_dir: Path, stack: frozenset[Path]) -> str:
        out: list[str] = []
        pos = 0
        for m in self._iter_skipping(source):
            out.append(source[pos : m.start()])
            cmd, name = m.group(1), m.group(2)
            target = self._resolve(name, current_dir)
            if target is None:
                self.missing += 1
                out.append(m.group(0))
            else:
                resolved = target.resolve()
                if resolved in stack:
                    # cycle: keep verbatim, do not recurse
                    out.append(m.group(0))
                    self.expanded += 1
                    pos = m.end()
                    continue
                content = target.read_text(encoding="utf-8", errors="replace")
                beginning = len(out)
                if self.markers:
                    out.append(f"{_MARKER_PREFIX} begin {target.name}\n")
                out.append(self._expand(content, resolved.parent, stack | {resolved}))
                if self.markers:
                    out.append(f"\n{_MARKER_PREFIX} end {target.name}\n")
                self.expanded += 1
                del beginning
            pos = m.end()
        out.append(source[pos:])
        return "".join(out)

    def _resolve(self, name: str, current_dir: Path) -> Path | None:
        """Resolve an \\input name to a file, TeX-style (with .tex)."""
        candidate = name.strip()
        if candidate.startswith("/"):
            # absolute: as-is (with .tex fallback)
            p = Path(candidate)
            return p if p.is_file() else self._with_tex(p)

        for base in (current_dir, self.base_dir):
            p = base / candidate
            if p.is_file():
                return p
            p = p.with_suffix(".tex")
            if p.is_file():
                with_tex = base / f"{candidate}.tex"
                if with_tex.is_file():
                    return with_tex
        return None

    def _with_tex(self, p: Path) -> Path | None:
        q = p.with_suffix(".tex")
        return p if p.is_file() else (q if q.is_file() else None)

    def _iter_skipping(self, source: str) -> list[re.Match]:
        """Matches of _INPUT_RE outside comments and verbatim bodies."""
        results: list[re.Match] = []
        i = 0
        n = len(source)
        while i < n:
            # comment: up to end of line (comment includes it)
            if source[i] == "%":
                nl = source.find("\n", i)
                i = n if nl < 0 else nl + 1
                continue
            # verbatim-like environment: skip whole body
            m_verb = re.compile(
                rf"\\begin\{{({'|'.join(re.escape(e) for e in _VERBATIM_ENVS)})\}}"
            ).match(source, i)
            if m_verb:
                end = re.compile(rf"\\end\{{{m_verb.group(1)}\}}")
                m_end = end.search(source, m_verb.end())
                skip_to = m_end.end() if m_end else n
                results_adjacent = _INPUT_RE.finditer(source, m_verb.end(), skip_to)
                # inside verbatim: do not expand - skip entirely
                i = skip_to
                continue
            m = _INPUT_RE.match(source, i)
            if m:
                results.append(m)
                i = m.end()
                continue
            i += 1
        return results


def flatten_source(
    source: str,
    base_dir: Path | str | None = None,
    markers: bool = False,
) -> str:
    """Expand \\input/\\include in a source string (base_dir=cwd)."""
    return Flattener(base_dir=base_dir, markers=markers).flatten(source)


def flatten_file(
    source_path: Path | str,
    base_dir: Path | str | None = None,
    markers: bool = False,
) -> str:
    """Read a file and expand all its \\input/\\include references.

    Args:
        source_path: the main .tex file.
        base_dir: fallback directory for unresolvable-relative names
            (default: the main file's directory).
        markers: insert provenance comments around expansions.

    Returns:
        The flattened source text.
    """
    sp = Path(source_path)
    f = Flattener(base_dir=base_dir if base_dir is not None else sp.parent, markers=markers)
    return f.flatten(sp.read_text(encoding="utf-8", errors="replace"))
