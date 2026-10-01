"""texdiff - AST-driven semantic diff for LaTeX documents.

The pipeline IS::

    old.tex ─┐
             ├─> parse → align trees → emit markup → diff.tex
    new.tex ─┘

 latexdiff diffs characters and breaks structure;
 texdiff diffs structure and never breaks characters.
"""

from .align import Delete, Edit, Insert, Match, Modify, align
from .api import DiffResult, DiffStats, diff_documents, diff_files
from .emit import LatexdiffMarkup, render
from .nodes import Node
from .parse import ParseError, parse, parse_file
from .textdiff import Chunk, word_diff

__all__ = [
    "align",
    "align",
    "Chunk",
    "Delete",
    "diff_documents",
    "diff_files",
    "DiffResult",
    "DiffStats",
    "Edit",
    "Insert",
    "LatexdiffMarkup",
    "Match",
    "Modify",
    "Node",
    "parse",
    "ParseError",
    "parse_file",
    "render",
    "word_diff",
]

__version__ = "0.1.0"
