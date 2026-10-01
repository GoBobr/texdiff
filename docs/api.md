# API Reference

::: texdiff.api
    options:
      members:
        - diff_documents
        - diff_files
        - DiffResult
        - DiffStats

::: texdiff.nodes
    options:
      members:
        - Node

::: texdiff.parse
    options:
      members:
        - parse
        - parse_file
        - ParseError

::: texdiff.align
    options:
      members:
        - align
        - Match
        - Modify
        - Insert
        - Delete
        - Edit

::: texdiff.textdiff
    options:
      members:
        - word_diff
        - Chunk

::: texdiff.emit
    options:
      members:
        - render
        - LatexdiffMarkup

::: texdiff.flatten
    options:
      members:
        - flatten_source
        - flatten_file
        - Flattener

::: texdiff.preamble
    options:
      members:
        - split_preamble
        - count_preamble_changes

::: texdiff.check
    options:
      members:
        - run_pdflatex
        - check_compiles
