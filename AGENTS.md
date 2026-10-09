# texdiff — Design Principles for Agents

High-level rules distilled from the project's evolution. Read before
changing anything in `src/texdiff/` or `tests/`. Details live in the
code docstrings; this file is the *why*, not the *how*.

## What texdiff is

An AST-driven semantic diff for LaTeX. Pipeline: **parse → flatten
(`\input`) → align trees → emit latexdiff-compatible markup**.
The core promise: *"texdiff diffs structure and never breaks
characters"* — unlike latexdiff, which diffs characters and breaks
structure. Every change must keep this true: the output must always
compile and never mangle source text.

## Architecture

- `parse.py` / `nodes.py` — tokenizes into a Node tree (text, macro,
  env, row kinds). The parser intentionally *glues* longtable head
  material (`\endfoot\endlastfoot`, `\hline \endfirsthead \hline`)
  onto adjacent row nodes; emit must tolerate and de-duplicate this.
- `flatten.py` — resolves `\input`/`\include` before diffing.
- `align.py` — sequence alignment producing `Match / Modify /
  Insert / Delete` edits. Grouped-table rows get a `group` tag that
  scopes pairing.
- `emit.py` — the big one. Converts edits to markup; contains the
  table handling cascade and all ulem/LR-mode safety logic.
- `tables.py` — wholesale retired/inserted table rendering (heavily
  restructured or pathological table pairs render as whole
  struck/blue tables).
- Rendered markup is **latexdiff FL-style compatible**; the preamble
  it injects defines `\DIFdel{..}` as red+`\sout` and
  `\DIFdelbegin` as red color *only* — this distinction matters
  constantly: color-switch fallbacks are legitimate degraded mode,
  never a substitute for striking when striking is possible.

## Essential design decisions

1. **Everything must render.** A markup choice that would make
   pdflatex/ulem abort (LR-mode violations, arity breakage) is worse
   than an uglier-but-safe degradation. The safety probe
   (`_is_safe_inline`) is the gatekeeper — but its rejection list
   must stay *minimal*: decoration macros (`\emph`, `\textbf`, …)
   with pure-text arguments are safe and must be masked, not
   rejected (v0.2.4 facet-2 bug: cells went red-unstruck).

2. **No silent content loss.** If old content disappears from the
   output without a strike, that is a bug, full stop. The
   text→empty cell bug (v0.2.4 facet 1) is the canonical example.
   When touching per-cell merge code, always ask: can any old/new
   cell value end up unmarked, dropped, or duplicated?

3. **Grouped tables pair within their group.** Variable/attribute
   tables repeat attribute keys (`units`, `_FillValue`) in every
   group; key equality alone cannot pair rows. Group tags from the
   aligner scope same-key pairing; decorated anchors
   (`\rowcolor` + `\textbf` first cell) delimit groups, with a
   unique-key fallback for undecorated tables. Never let pairing
   cross a group barrier — it word-diffs one variable's value into
   another variable's row.

4. **Struck rows render the whole row struck; merged rows keep a
   plain key cell.** A merged (deleted+inserted) attribute row shows
   a plain key cell and both values; a retired row is fully struck
   including the key. Tests distinguish them by these shapes —
   keep the shapes stable.

5. **Retire-then-insert ordering.** When a table/row is replaced,
   the red retired version must precede the blue replacement in the
   output, even when alignment emits the Insert first. Hoisting
   logic in `emit.py` enforces this.

6. **Breakability beats beauty.** Struck/waved text in narrow
   `W{}`-columns must wrap: long tokens get `\allowbreak` /
   word-per-`\sout` treatment; a control word eats the following
   space, so `\DIFdelend{}` keeps interword spaces real. Never emit
   one unbreakable box where a column can't fit it.

7. **Spurious change suppression.** Differences that typeset
   identically (escaping-only `_` vs `\_`, generator quoting
   artifacts, whitespace, soft hyphens) must render unmarked.
   Normalize before comparing; a diff full of noise hides real
   changes.

8. **Commented-out deleted content is a last resort**, not a
   default. `%DIFDELCMD` lines exist for structure that cannot be
   shown inline (whole environments, multi-line macros) — visible
   struck red is always preferred where ulem permits.

## Testing discipline

- Tests assert on the **rendered markup string** via the public API
  (`diff_documents(..., inject_preamble=False).marked_up`), not on
  internals. Raw-string regexes: literal `\DIFdelbegin` is
  `r"\\DIFdelbegin"` — mind brace escaping in `re.findall`.
- Count merges with regex, not `.count()` — plain-string counts
  collide across rows (e.g. `9.9e+36` in both fill and valid_max).
- Single-row tables take the *wholesale* path — regression tests
  for row-merging need surrounding context rows.
- Every bug fix gets a regression test named after the symptom.
  Full suite must pass before any commit/tag (~255 tests).
- Real-world validation: rebuild the target document's diff PDF
  (`build-diff.sh` in the downstream spec repos) — unit-green is
  not render-green.

## Release process

Conventional commits, anonymized (no project/customer names).
Version in *both* `pyproject.toml` and `src/texdiff/__init__.py`.
Tag `vX.Y.Z`, push, `gh release create` with notes describing the
delta. **Do NOT upload to PyPI manually** — packages are built and
published by the GitHub release workflow; a manual `twine upload`
bypasses/duplicates it.
Downstream CI installs *latest* texdiff — an unpublished commit
means downstream pipelines silently run old code.
