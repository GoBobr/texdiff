# Alignment

Alignment converts two node lists into an ordered *edit script*:

| Edit      | Produced when                                        |
|-----------|------------------------------------------------------|
| `Match`   | same signature and identical source text             |
| `Modify`  | same signature, different content → diff recursively |
| `Insert`  | node only in new                                     |
| `Delete`  | node only in old                                     |

The *signature* of a node is `kind:name` (`env:itemize`, `macro:textbf`,
`text`, ...). Alignment of the top level (and of each recursable node's
children) uses `difflib.SequenceMatcher` on signature sequences:

1. `get_matching_blocks()` yields runs of equal signatures;
2. inside a run, nodes are paired positionally — identical pairs become
   `Match`, differing pairs `Modify`;
3. gap regions pair up as `Delete`/`Insert` sequences in document order.

## Why this handles tables

A longtable body is a run of row-ish nodes with *identical signatures*
(e.g. `text` runs separated by `\\`). The positional pairing inside a
signature run gives exactly the row-LCS behaviour the
`sanitize-tables.pl` Perl heuristic implements — but as a property of
the alignment, not a repair pass. Reordered rows pair up; a row present
on one side only becomes `Delete`/`Insert` of one row, never glued cell
content.

## Table strategy (v0)

Environments declared *table-like* (`longtable`, `tabular`, ...) align at
**row granularity**: the parser splits their body into row nodes [`text`
+ `\\` boundaries] so the aligner operates on rows, and modified row
pairs render as an old row (struck out) followed by the new row, keeping
the column structure of whichever side the row belongs to.

Restructured tables (changed column count in the preamble argument) fall
back to whole-block `Delete` + `Insert` — the "both versions" rendering:
old version struck out, new version highlighted, never merged.
