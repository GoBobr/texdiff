# texdiff

**AST-driven semantic diff for LaTeX documents.**

`texdiff` compares two revisions of a LaTeX document and produces a single
marked-up `diff.tex` that compiles with `pdflatex` — added text in blue,
deleted text struck out — while preserving the original document's styling,
cross-references and custom classes.

## Why

[`latexdiff`](https://www.ctan.org/pkg/latexdiff) is the de-facto standard for
review-marked LaTeX, but it diffs *token soup*: it guesses word boundaries with
regexes and re-injects markup into the merged text, hoping braces still
balance. On real-world documents the result is markup that crosses brace /
environment boundaries, glues old and new table cells together, and corrupts
listings — which is why every serious latexdiff user eventually ends up
maintaining a pile of fragile pre/post-processing scripts around it.

`texdiff` takes the other road: **diff the tree, never the text.**

1. Parse old and new documents into a LaTeX syntax tree
   ([pylatexenc](https://github.com/phfaist/pylatexenc) — a faithful concrete
   syntax tree carrying exact source spans, no macro expansion).
2. Align the trees level by level (document → environments → groups → text
   runs) with difflib-style sequence matching. A change can never leak across
   a node boundary.
3. Walk the merged tree and emit `\DIFadd{...}` / `\DIFdel{...}` markup —
   latexdiff-compatible rendering, valid by construction.

Restructured tables (re-ordered rows, changed column layouts), math,
listings and verbatim environments are handled as atomic or row-aligned
blocks instead of being shredded by a word diff.

## Goal

```sh
texdiff old/main.tex new/main.tex > diff.tex   # \input expansion built in
pdflatex diff.tex                              # done - no pre/postprocessing
```

Status: **early concept, not yet usable.** Development notes and design
decisions will land in this repository.

## License

MIT
