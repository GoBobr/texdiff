# texdiff

**AST-driven semantic diff for LaTeX documents.**

`texdiff` compares two revisions of a LaTeX document and produces a single
marked-up `diff.tex` that compiles with `pdflatex` — added text highlighted,
deleted text struck out — while preserving the original document's styling,
cross-references and custom classes.

## Why

[`latexdiff`](https://www.ctan.org/pkg/latexdiff) diffs *token soup*: it
guesses word boundaries with regexes and re-injects markup into the merged
text, hoping braces still balance. On real-world documents the result is
markup that crosses brace/environment boundaries, glues old and new table
cells together, and corrupts listings — which is why every serious latexdiff
user eventually maintains a pile of fragile pre/post-processing scripts.

`texdiff` takes the other road: **diff the tree, never the text.**

## Status

**v0.2.1:** parse (pylatexenc) → align → render pipeline with the
three latexdiff-killer guarantees:

1. unchanged node text round-trips byte-identically,
2. markup never appears inside an atomic node (math, verbatim, unknown
   macros, specials),
3. restructured tables align as row sequences instead of being shredded.

Now also in:

- `\input`/`\include` flattening (relative to each file, marker
  comments optional),
- row-granular table alignment that survives regenerated tables
  (`\hline` moving across row boundaries), with alignment-safe cell
  markup,
- new-revision preamble policy with change counts in `--stats`,
- `--check`: compile the marked-up result and fail loudly,
- compile-guaranteed golden corpus in CI.

Next (see [roadmap](roadmap.md)): move-detection thresholds, a
`latexdiff` fallback engine for hostile documents.

## Quick look

```python
from texdiff import diff_documents

result = diff_documents(old_source, new_source)
print(result.marked_up)  # diff.tex source, latexdiff-compatible markup
print(result.stats)      # "N unchanged, M modified, I added, D deleted"
```

```sh
pip install -e ".[dev]"
pytest                          # contract test-suite with coverage gate
texdiff old.tex new.tex -o diff.tex --check   # diff + compile check
```

Diffs of multi-file sources are flattened automatically (like
`latexdiff --flatten`), with `\input` targets resolved relative to
each file.

## License

MIT
