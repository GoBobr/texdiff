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
