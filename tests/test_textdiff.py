"""Contract tests for texdiff.textdiff: word-level diffs of text runs."""

from __future__ import annotations

from texdiff.textdiff import Chunk, word_diff


def apply(chunks: list[Chunk], old: bool) -> str:
    return "".join(
        c.text for c in chunks if c.op == "equal" or (c.op == ("delete" if old else "insert"))
    )


class TestBasics:
    def test_identical_text_single_equal(self):
        chunks = word_diff("same text", "same text")
        assert chunks == [Chunk("equal", "same text")]

    def test_reconstruction(self):
        old, new = "the quick brown fox", "the quick red fox"
        chunks = word_diff(old, new)
        assert apply(chunks, True) == old
        assert apply(chunks, False) == new

    def test_changed_word_split_out(self):
        chunks = word_diff("the quick brown fox", "the quick red fox")
        ops = [c.op for c in chunks]
        assert ops.count("delete") == 1
        assert ops.count("insert") == 1
        deleted = [c for c in chunks if c.op == "delete"]
        inserted = [c for c in chunks if c.op == "insert"]
        assert deleted[0].text == "brown"
        assert inserted[0].text == "red"

    def test_insertion_in_middle(self):
        chunks = word_diff("a c", "a b c")
        ops = [c.op for c in chunks]
        assert ops == ["equal", "insert", "equal"]


class TestLaTeXSafety:
    def test_command_name_not_split_from_braces(self):
        chunks = word_diff("\\textbf{x}", "\\emph{x}")
        # no intermediate chunk may end between the backslash command
        # name and its argument braces
        for c in chunks:
            assert not (c.text.endswith("\\textb") or c.text.endswith("\\emp"))

    def test_whitespace_only_change_keeps_new(self):
        chunks = word_diff("a  b", "a b")
        assert apply(chunks, False) == "a b"
        assert apply(chunks, True) in ("a  b", "a b")


class TestSemanticCleanup:
    def test_adjacent_edits_merge_into_replacement(self):
        # a scattered single-char edit should become ONE delete+insert
        # region, not many alternating islands
        chunks = word_diff("xxaxx", "xxbxx")
        ops = [c.op for c in chunks if c.op != "equal"]
        assert ops == ["delete", "insert"]

    def test_reworded_sentence_not_shredded_word_by_word(self):
        # a reworded sentence shares filler words with its new version
        # ("with", "configurable", "zlib", ...); the sequence matcher
        # pairs them across the real edit, producing a del/ins mosaic.
        # The region between stable bridges must collapse into whole
        # delete + insert clauses - the track-changes look reviewers
        # expect - while the shared prefix and suffix stay equal.
        old = (
            "Common methods for reading HDF5 datasets (with masked-array "
            "support and HDF5 diagnostic suppression) and writing NetCDF4 "
            "outputs (with configurable zlib compression and shuffle "
            "filtering)."
        )
        new = (
            "Common methods for reading HDF5 datasets (with masked-array "
            "support and HDF5 diagnostic suppression). Writing of NetCDF4 "
            "outputs is performed through the generic schema-driven writer "
            "(see Overview), with configurable zlib compression and shuffle "
            "filtering."
        )
        chunks = word_diff(old, new)
        # contract: chunks still reconstruct both sides exactly
        assert apply(chunks, True) == old
        assert apply(chunks, False) == new
        # the shared prefix survives as an equal region; the long shared
        # tail ("configurable ... shuffle") too - it may be followed by
        # one tiny token pair ("filtering)." vs "filtering.")
        assert chunks[0].op == "equal" and "HDF5 diagnostic" in chunks[0].text
        equal_tails = [c for c in chunks if c.op == "equal" and "shuffle" in c.text]
        assert equal_tails
        # the reworded middle is coalesced: few change regions, each a
        # whole clause - not dozens of single-token del/ins islands
        change_groups = 0
        prev = "equal"
        for c in chunks:
            if c.op in ("delete", "insert") and prev == "equal":
                change_groups += 1
            prev = c.op
        assert change_groups <= 4
        # and the rewording appears as whole clauses: the crossed
        # "suppression) and writing" pair is a multi-word delete
        clausal = [t for t in (c.text for c in chunks if c.op == "delete")]
        assert any("suppression" in t and "writing" in t for t in clausal), clausal

    def test_separate_small_edits_keep_word_level(self):
        # two genuinely distinct single-word edits separated by a long
        # stable stretch: word-level markup, NOT coalesced into one
        # big replacement
        chunks = word_diff(
            "alpha beta gamma delta epsilon zeta eta theta",
            " ALPHA beta gamma delta epsilon ZETA eta theta",
        )
        # each case-only word change is its own small region; the long
        # bridge between them forbids coalescing
        groups = 0
        prev = "equal"
        for c in chunks:
            if c.op in ("delete", "insert") and prev == "equal":
                groups += 1
            prev = c.op
        assert groups == 2
