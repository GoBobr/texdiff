# Emitter

The emitter turns the edit script into LaTeX source. Markup is chosen
through the `LatexdiffMarkup` record — latexdiff UNDERLINE flavour by
default:

```latex
\DIFadd{added text}   % blue wavy underline (via ulem \uwave + xcolor)
\DIFdel{deleted text} % red strikeout      (via ulem \sout + xcolor)
```

`;PREAMBLE_TEMPLATE` provides `\providecommand` definitions to splice
before `\begin{document}` so plain documents compile out of the box:
documents that already define `\DIFadd`/`\DIFdel` (existing latexdiff
preambles) are unaffected because `providecommand` does not override.

## Rendering rules

| Edit       | Emission                                                       |
|------------|----------------------------------------------------------------|
| `Match`    | the node's exact source bytes                                  |
| `Modify`   | `\DIFdel{old}` immediately followed by `\DIFadd{new}` (or the recursively marked-up pair) |
| `Insert`   | `\DIFadd{...}` around the node text                            |
| `Delete`   | `\DIFdel{...}` around the node text                            |

For `Delete` inside \*table rows and environments, the emitter renders the
*whole environment* with its content struck out; a deleted `longtable`
row is rendered as a row whose cells are individually struck out, keeping
`&` and `\\` structure intact (never markup across `&`).

## Alternating spans

Adjacent `DIFdel`/`DIFadd` regions are grouped inside one
`\DIFdelbegin ... \DIFdelend \DIFaddbegin ... \DIFaddend` span, and a
*paragraph break rule* avoids injecting markup across `\\` or table row
boundaries that LaTeX cannot handle: whole-block fallback is used there.
