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

## v1 — real documents

- [ ] `\input`/`\include` expansion (flatten inside the tool, with
      per-tree asset resolution)
- [ ] preamble policy: take new preamble, diff configured macros
      (changerecord-style change tables) as row sequences
- [ ] table row alignment at row granularity (split longtable bodies
      into row nodes in the parser)
- [ ] verbatim/listings-passthrough tests against the ADS corpus
- [ ] `--check` mode: compile the diff once, fail loudly
- [ ] move-detection threshold (like latexdiff's `--allow-spaces` and
      word-runs) to keep public diffs small

## v2 — ecosystem

- [ ] golden-output regression corpus from the ADS/ICD git histories
- [ ] `latexdiff` fallback engine behind `--engine`
- [ ] HTML side-by-side report (optional extra)
- [ ] PyPI release (`texdiff` name is free)

## Out of scope (forever)

- TeX macro *expansion* / catcode games — we stay a concrete-syntax-tree
  tool; `\newcommand` semantics are policy, not parsing.
- Byte-exact reproduction of latexdiff output.
