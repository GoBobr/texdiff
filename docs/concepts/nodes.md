# Node model

`texdiff.nodes.Node` is a frozen dataclass — the only tree shape the rest
of the tool knows about. pylatexenc node classes never leak past
`texdiff.parse`.

| Field       | Meaning                                                        |
|-------------|----------------------------------------------------------------|
| `kind`      | `env`, `group`, `macro`, `text`, `math`, `comment`, `specials`, `verb` |
| `text`      | exact source span *including delimiters*                        |
| `name`      | environment/macro name (`None` for text)                        |
| `children`  | sub-nodes (only for recursable nodes)                           |
| `atom`      | if `True`: compared on exact text, never diffed inside          |

## Kind catalogue

- `env` — LaTeX environment. Recursable for text environments
  (`document`, `itemize`, `enumerate`, `quote`, ...), atomic for verbatim
  (`lstlisting`, `verbatim`, `minted`, ...) and display math.
- `group` — braced group / macro argument. Recursable.
- `macro` — control sequence with arguments. Atomic unless configured as
  text-bearing (policy lives in the parser, not the aligner).
- `text` — plain characters run; leaf, compared by word diff when
  modified.
- `math` — inline `$...$` / display `\[...\]` / `equation`-like. Atomic:
  an old/new formula renders as whole-block replacement (red formula +
  blue formula), never token-shredded.
- `comment` — `% ...` lines. Kept or dropped by policy.
- `specials` — pylatexenc "specials" (`$$..$$`, `~`, etc.). Atomic.

## Round-trip guarantee

`text` spans are *exact*: leading/trailing whitespace, comment
terminators, `\\` line breaks survive the diff untouched. This is what
makes listings and generated tables safe: an unchanged region is emitted
from input bytes, not reconstructed.
