"""Tests for the reference-alignment behaviours added for the ICD build:

- sectioning heading title marking (single node + coalesced runs),
- table row insert/delete pair merging with line-colour fidelity,
- word-level marking of paired row lines,
- hidden listing markers (empty marker drop, lstset language strip),
- oldlines norm/in_old substring semantics.
"""

from __future__ import annotations

import pytest

from texdiff.api import diff_documents
from texdiff.emit import LatexdiffMarkup
from texdiff.emit import (
    _mark_heading_arg,
    _mark_heading_args_in_run,
    _render_row_region,
    _render_verbatim_modify,
    _word_marked_line,
    _wrap,
)
from texdiff.align import Delete, Insert, Modify
from texdiff.nodes import Node
from texdiff import oldlines


def _node(text: str) -> Node:
    return Node(kind="text", name="", text=text)


# --- sectioning heading title marking -------------------------------------


def test_mark_heading_arg_wraps_title() -> None:
    out = _mark_heading_arg(r"\section{Introduction}")
    assert out == r"\section{\DIFadd{Introduction}}"


def test_mark_heading_arg_skips_titles_with_embedded_macros() -> None:
    # a \label inside the title argument makes it unsafe for inline
    # markup: the title stays unwrapped (block-only treatment)
    out = _mark_heading_arg(r"\subsection{Config\label{sec:cfg}}")
    assert out == r"\subsection{Config\label{sec:cfg}}"


def test_mark_heading_args_in_run_handles_label_on_same_line() -> None:
    line = r"\subsection{Configuration file SCE_2__CNF____AX}\label{sec:cnf-config}"
    out = _mark_heading_args_in_run(line)
    assert out.startswith(r"\subsection{\DIFadd{Configuration file SCE_2__CNF____AX}}")
    assert out.endswith(r"\label{sec:cnf-config}")


def test_mark_heading_args_in_run_leaves_plain_lines_alone() -> None:
    text = "plain paragraph text\n" + r"\subsection{Title}" + "\nmore text"
    assert _mark_heading_args_in_run(text) == text.replace(
        r"\subsection{Title}", r"\subsection{\DIFadd{Title}}"
    )


def test_mark_heading_args_in_run_marks_headings_in_run() -> None:
    # a single-line run with a trailing second heading: only headings
    # whose title is inline-safe take the wrap, the rest stays verbatim
    line = r"\section{Alpha}\label{a} text \section{Beta text}"
    out = _mark_heading_args_in_run(line)
    assert r"text \section{Beta text}" in out
    # Alpha DID get marked - the mark lives inside the braces
    assert r"\DIFadd{Alpha}" in out
    assert r"\label{a}" in out


# --- oldlines norm / in_old semantics --------------------------------------


def test_norm_line_strips_diff_markers_and_structure_chars() -> None:
    assert oldlines.norm_line(r"  \textbf{Hello} world \\") == "textbfHelloworld"
    # %DIF markers poison the remainder of the line - it normalises empty
    assert oldlines.norm_line(r"%DIF > \textbf{Hello}") == ""


def test_in_old_uses_substring_matching() -> None:
    oldlines.mark_old_lines(
        "a makecell cell body that is quite long enough & trailing \\"
    )
    # new line is a prefix (old line had extra trailing structure) -> matched
    assert oldlines.in_old("a makecell cell body that is quite long enough")
    # genuinely new content -> not matched
    assert not oldlines.in_old("totally different content line right here")
    # short norms never match (reference len>=10 guard)
    assert not oldlines.in_old("short")


def test_in_old_is_reset_by_mark_old_lines() -> None:
    oldlines.mark_old_lines("first revision line content here for blob")
    assert oldlines.in_old("first revision line content here for blob")
    oldlines.mark_old_lines("second revision line content here for blob")
    assert not oldlines.in_old("first revision line content here for blob")


# --- row insert/delete pair merging ----------------------------------------


def test_word_marked_line_marks_single_character_change() -> None:
    out = _word_marked_line("Inpupt product files", "Input product files")
    assert "\\DIFdel{Inpu" in out
    assert "\\DIFadd{Inpu" in out
    assert "product files" in out
    # the whole marked line must stay inline-safe
    assert "\n" not in out


def test_word_marked_line_marks_added_word_in_shared_context() -> None:
    out = _word_marked_line(
        "DateTime(const std::string& tstamp);",
        "explicit DateTime(const std::string& tstamp);",
    )
    assert "\\DIFadd{explicit" in out
    rest = out.replace("\\DIFadd{explicit }", "")
    assert "DateTime(const std::string& tstamp);" in rest
    # unchanged shared context carries no markers of its own
    assert "\\DIFdel{" not in rest


ROW_OLD = (
    "\\textbf{ClimL2 IPF} & \\makecell[tl]{\\swlibone} \\\\\n"
    "\\hline"
)
ROW_NEW = (
    "\\textbf{ClimL2 IPF} & \\makecell[tl]{\\swlibtwo} \\\\\n"
    "\\hline"
)


def test_render_row_region_merges_row_pair() -> None:
    oldlines.mark_old_lines(ROW_OLD)
    markup = LatexdiffMarkup()
    edits = [
        Delete(old=Node(kind="row", text=ROW_OLD)),
        Insert(new=Node(kind="row", text=ROW_NEW)),
    ]
    out = _render_row_region(edits, markup)
    # one merged row (not a deleted row followed by an added one): a
    # single row region carrying both del and add marks
    assert out.count("ClimL2 IPF") == 1
    assert "\\DIFaddbeginFL" in out
    # the paired cell lines were marked word-by-word
    assert "\\DIFdel{" in out and "swlibone" in out
    assert "swlibtwo" in out
    assert "DIFadd{" in out


def test_render_row_pair_comments_out_old_only_lines() -> None:
    oldlines.mark_old_lines("placeholder long line for the old blob here")
    markup = LatexdiffMarkup()
    old_row = "\\textbf{ClimL2 IPF} & \\makecell[tl]{\\swlibone} \\\\\nsecond old cell line \\\\\n\\hline"
    new_row = "\\textbf{ClimL2 IPF} & \\makecell[tl]{\\swlibone} \\\\\nbrand new cell line content \\\\\n\\hline"
    edits = [
        Delete(old=Node(kind="row", text=old_row)),
        Insert(new=Node(kind="row", text=new_row)),
    ]
    out = _render_row_region(edits, markup)
    # old-only line lives inside a commented deletion shadow
    assert "%DIFDELCMD < second old cell line" in out
    assert "\\DIFdelbeginFL" in out
    assert "brand new cell line content" in out


def test_render_row_region_leaves_disparate_rows_separate() -> None:
    oldlines.mark_old_lines("something entirely different altogether here")
    markup = LatexdiffMarkup()
    edits = [
        Delete(old=Node(kind="row", text=ROW_OLD)),
        Insert(new=Node(kind="row", text="\\textbf{Unrelated} & other \\\\\n\\hline")),
    ]
    out = _render_row_region(edits, markup)
    # rows share no normalised lines: both rows rendered in full
    assert "ClimL2 IPF" in out
    assert "Unrelated" in out


# --- hidden listing markers -------------------------------------------------


LST_OLD = (
    "\\begin{lstlisting}[language=C++]\n"
    "int old_api(int a);\n"
    "\\end{lstlisting}\n"
)


def _verbatim_modify(text: str) -> str | None:
    modify = Modify(
        old=Node(kind="env", text=LST_OLD, name="lstlisting", atom=True),
        new=Node(kind="env", text=text, name="lstlisting", atom=True),
    )
    return _render_verbatim_modify(modify)


def test_render_verbatim_modify_drops_empty_marker_lines() -> None:
    out = _verbatim_modify(
        "\\begin{lstlisting}[language=C++]\n"
        "int new_api(int a);\n"
        "\n"
        "int extra_line(void);\n"
        "\\end{lstlisting}\n"
    )
    assert out is not None
    lines = out.split("\n")
    # no marker line may be bare (empty remainder) - those leak as literal
    assert all(ln.strip() != "%DIF >" for ln in lines), out
    assert any("%DIF <" in ln and "old_api" in ln for ln in lines)
    assert any("%DIF >" in ln and "new_api" in ln for ln in lines)
    # the blank interior line got no redundant marker for whitespace
    assert not any(ln.strip().startswith("%DIF") and not ln.strip()[5:].strip()
                   for ln in lines if ln.strip().startswith("%DIF"))


def test_fix_listing_languages_strips_lstset_language() -> None:
    old = (
        "\\documentclass{book}\n"
        "\\begin{document}\n"
        "\\chapter{Alpha}\nold body text line one\n"
        "\\end{document}\n"
    )
    new = (
        "\\documentclass{book}\n"
        "\\usepackage{listings}\n"
        "\\begin{document}\n"
        "\\chapter{Alpha}\nnew body text line here\n"
        "\\lstset{language=C++,basicstyle=\\ttfamily}\n"
        "\\end{document}\n"
    )
    out = diff_documents(old, new).marked_up
    assert "\\lstset{language=" not in out
    assert "\\lstset{basicstyle=" in out


def test_diff_documents_marks_added_heading_title() -> None:
    old = (
        "\\documentclass{book}\n\\begin{document}\n"
        "\\chapter{Existing}\nold text\n"
        "\\end{document}\n"
    )
    new = (
        "\\documentclass{book}\n\\begin{document}\n"
        "\\chapter{Existing}\nold text\n"
        "\\section{A whole new section}\nfresh body\n"
        "\\end{document}\n"
    )
    out = diff_documents(old, new).marked_up
    assert "\\section{\\DIFadd{A whole new section}}" in out


def test_wrap_wraps_text_in_markup_macros() -> None:
    out = _wrap("highlighted", "\\DIFadd{", "}")
    assert out == "\\DIFadd{highlighted}"


# --- prefix/suffix anchoring of align() -------------------------------------


def test_inserted_chapter_does_not_recolour_previous_passage() -> None:
    # an inserted chapter between two near-identical passages must not
    # drag the earlier passage into the insertion (blue bleed). The
    # SequenceMatcher longest-block greedy choice pairs the trailing
    # text with a later position; the common-prefix anchor prevents it.
    old = (
        "\\subsubsection*{set_log}\nsee Scene L2 API section\n\n\n"
        "\\newpage\n\n\\subsection{Job Order}\nOld text here.\n"
    )
    new = (
        "\\subsubsection*{set_log}\nsee Scene L2 API section\n\n\n"
        "\\newpage\n\n\\subsection{Config}\nWhole new chapter.\n\n"
        "\\newpage\n\n\\subsection{Job Order}\nOld text here.\n"
    )
    out = diff_documents(old, new).marked_up
    # the 'see Scene L2 API section' passage stays unmarked (black)
    assert "see Scene L2 API section\n\n\n\\newpage" in out
    # ... and only the new chapter body is blue
    assert "\\DIFadd{Config}" in out


def test_block_head_similarity_pairing_of_wildcard_text() -> None:
    # the wildcard 'text' signature lets a matching block glom a pair
    # of unrelated text nodes; the right partner sits in the insertion
    # gap right before the block. The refinement must pair by
    # similarity: the trailing-whitespace delete + paragraph insert
    # stay word-level, not whole-node.
    old = (
        "Some para about CpuCores. Whether this is set by the PI "
        "is currently unclear and is TBC. \n\n"
        "\\subsubsection{Examples}\nExample body.\n"
    )
    new = (
        "Some para about CpuCores. Whether this is set by the PI "
        "is currently unclear and is TBC.\n\n"
        "Dynamic parameters with the prefix are used.\n\n"
        "\\subsubsection{Examples}\nExample body.\n"
    )
    out = diff_documents(old, new).marked_up
    # the unchanged sentence stays black...
    assert "is currently unclear and is TBC.\\DIFdelbegin" in out
    # ...and only whitespace was deleted (invisible strikethrough)
    assert "\\DIFdel{is currently unclear" not in out


# --- escape folding (parse) --------------------------------------------------


def test_escape_macros_fold_into_adjacent_text_nodes() -> None:
    # a revision writing an identifier with escaped underscores must
    # present the same node granularity as one writing it plain,
    # otherwise the word diff strikes the whole unchanged tail
    from texdiff.parse import parse

    old = (
        "The duration is one S7A_CO2_1A_GEO file type. More text.\n"
    )
    new = (
        "The duration is one CO2\\_1A\\_GEO\\_\\_\\_\\_\\_\\_ file type. "
        "More text.\n"
    )
    out = diff_documents(old, new).marked_up
    assert "file type. More text." in out  # unchanged tail stays black
    assert "\\DIFdel{S7A_CO2_1A_GEO}" in out
    assert "\\DIFadd{CO2\\_1A\\_GEO\\_\\_\\_\\_\\_\\_}" in out


def test_escape_folding_keeps_round_trip() -> None:
    from texdiff.parse import parse

    src = "a\\_b and c\\&d and eighty\\% sure\n"
    nodes = parse(src)
    assert "".join(n.text for n in nodes) == src


# --- added listings inside insert runs --------------------------------------


def test_added_listing_lines_get_dif_markers() -> None:
    # an inserted chapter's listing must render line-marked (%DIF >)
    # inside a DIFcode alsolanguage environment, not fall back to the
    # listings language styles (magenta # comments vs blue chapter)
    old = "\\documentclass{book}\n\\begin{document}\nold\n\\end{document}\n"
    new = (
        "\\documentclass{book}\n\\begin{document}\nold\n"
        "\\section{New YAML stuff}\n"
        "Sample:\n\\begin{lstlisting}\nkey: value\n# a comment\n\\end{lstlisting}\n"
        "\\end{document}\n"
    )
    out = diff_documents(old, new).marked_up
    assert "\\begin{lstlisting}[alsolanguage=DIFcode]" in out
    assert "%DIF > key: value" in out
    assert "%DIF > # a comment" in out


def test_modified_listing_lines_get_dif_markers() -> None:
    old = (
        "\\documentclass{book}\n\\begin{document}\n"
        "\\begin{lstlisting}\nkeep = 1\nold = 2\n\\end{lstlisting}\n"
        "\\end{document}\n"
    )
    new = (
        "\\documentclass{book}\n\\begin{document}\n"
        "\\begin{lstlisting}\nkeep = 1\nnew = 3\n\\end{lstlisting}\n"
        "\\end{document}\n"
    )
    out = diff_documents(old, new).marked_up
    assert "%DIF < old = 2" in out
    assert "%DIF > new = 3" in out
    assert "\\DIFmodbegin" in out


def test_moved_listing_block_not_retired_and_readded() -> None:
    # a struct block that moved position must emit once, unmarked,
    # instead of being struck through at the old place and re-added
    # blue at the new one
    old = (
        "\\documentclass{book}\n\\begin{document}\n"
        "\\begin{lstlisting}\nhead();\nstruct A {\nint x;\n};\n"
        "other();\nstruct B {\nint y;\n};\ntail();\n\\end{lstlisting}\n"
        "\\end{document}\n"
    )
    new = (
        "\\documentclass{book}\n\\begin{document}\n"
        "\\begin{lstlisting}\nhead();\n"
        "struct B {\nint z;\n};\n"
        "other();\nstruct A {\nint x;\n};\ntail();\n\\end{lstlisting}\n"
        "\\end{document}\n"
    )
    out = diff_documents(old, new).marked_up
    assert out.count("struct A {") == 1  # moved, not duplicated
    assert out.count("int x;") == 1
    assert "%DIF < int y;" in out
    assert "%DIF > int z;" in out


def test_reindentation_of_listing_not_marked() -> None:
    # lines whose only change is indentation compare equal on
    # stripped text and must not be retired and re-added wholesale
    old = (
        "\\documentclass{book}\n\\begin{document}\n"
        "\\begin{lstlisting}\nstruct A {\nint x;\n};\n\\end{lstlisting}\n"
        "\\end{document}\n"
    )
    new = (
        "\\documentclass{book}\n\\begin{document}\n"
        "\\begin{lstlisting}\n  struct A {\n    int x;\n  };\n\\end{lstlisting}\n"
        "\\end{document}\n"
    )
    out = diff_documents(old, new).marked_up
    body = out.split("\\begin{document}", 1)[1]
    assert "%DIF <" not in body
    assert "%DIF >" not in body
