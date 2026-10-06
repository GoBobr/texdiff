# Roadmap

## v0 — core pipeline (current)

- [x] repo, README, license, docs skeleton
- [x] `Node` model with round-trip guarantee
- [x] parser: pylatexenc → nodes (tolerant for real docs)
- [x] aligner: signature-based SequenceMatcher edit script
- [x] word diff with semantic cleanup for text runs
- [x] emitter: latexdiff-compatible `\DIFadd`/`\DIFdel`
- [x] CLI: `texdiff old.tex new.tex > diff.tex`
- [x] contract test suite, coverage gate ≥ 80%

## v1 — real documents (current, in development toward 0.2.1)

- [x] `\input`/`\include` expansion (flatten inside the tool, with
      per-tree asset resolution and marker comments)
- [x] preamble policy: take the new revision's preamble verbatim,
      report changed preamble lines in `--stats`
- [x] table row alignment at row granularity: longtable/tabular
      bodies become row-node sequences; row signatures ignore
      structural tokens (`\hline`, `\\`) so regenerated tables still
      pair with their counterparts; cell markup keeps `&` and `\\`
      outside `\DIFadd`/`\DIFdel` (alignment-safe by construction)
- [x] verbatim/listings passthrough (verbatim environments are atomic
      nodes round-tripped byte-identically; covered by the corpus)
- [x] `--check` mode: compile the diff with `pdflatex`, fail loudly
      (exit code 2) with the offending `!` log lines
- [ ] move-detection threshold (like latexdiff's `--allow-spaces` and
      word-runs) to keep public diffs small — deferred: in practice
      the signature anchoring already produces small diffs on
      generated documents

## v2 — ecosystem (in progress)

- [x] golden-output regression corpus with a compile guarantee
      (synthetic multi-revision documents exercising longtable
      headers, math, verbatim and row insertions)
- [x] PyPI release: `publish` workflow on `v*` tags via trusted
      publishing (OIDC); version 0.2.0; next: 0.2.1 with the table
      rendering fixes
- [ ] `latexdiff` fallback engine behind `--engine` — deferred until
      there is a document class texdiff cannot digest
- [x] ~~HTML side-by-side report~~ — removed from scope: rendered PDF
      plus the marked-up source cover the review workflow

## Validated against

- the synthetic golden corpus (CI, compile-checked), and
- a 65-page heavily generated real-world document with code-generated
  longtables whose regeneration moves `\hline` across row boundaries
  (36 hard compile errors and an infinite `longtable` loop reduced to
  a clean 5-second build).

## Out of scope (forever)

- TeX macro *expansion* / catcode games — we stay a concrete-syntax-tree
  tool; `\newcommand` semantics are policy, not parsing.
- Byte-exact reproduction of latexdiff output.
