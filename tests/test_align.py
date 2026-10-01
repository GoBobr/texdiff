"""Contract tests for texdiff.align: node-list alignment."""

from __future__ import annotations

from texdiff.align import Delete, Insert, Match, Modify, align
from texdiff.nodes import env, group, text_node


class TestIdentical:
    def test_identical_lists_match_verbatim(self):
        old = [text_node("abc"), text_node("def")]
        edits = align(old, old)
        assert all(isinstance(e, Match) for e in edits)
        assert len(edits) == 2


class TestReplacement:
    def test_plain_text_change_is_modify(self):
        old = [text_node("one two")]
        new = [text_node("one three")]
        (edit,) = align(old, new)
        assert isinstance(edit, Modify)


class TestInsertDelete:
    def test_added_env_is_insert(self):
        old = [text_node("a ")]
        new = [text_node("a "), env("itemize", text_node("\\item x"))]
        edits = align(old, new)
        assert any(isinstance(e, Insert) for e in edits)

    def test_removed_env_is_delete(self):
        old = [env("itemize", text_node("\\item x")), text_node("a")]
        new = [text_node("a")]
        edits = align(old, new)
        assert any(isinstance(e, Delete) for e in edits)

    def test_restructured_env_marks_del_and_insert(self):
        # a changed environment NAME cannot pair: old deleted, new added
        old = [env("itemize", text_node("\\item x"))]
        new = [env("enumerate", text_node("1. x"))]
        edits = align(old, new)
        assert any(isinstance(e, Delete) for e in edits)
        assert any(isinstance(e, Insert) for e in edits)


class TestDocumentOrder:
    def test_edits_follow_document_order(self):
        old = [text_node("a"), env("itemize", text_node("x")), text_node("b")]
        new = [text_node("A"), env("itemize", text_node("x")), text_node("b"), text_node("!")]
        edits = align(old, new)
        # first edit touches 'a'/'A', last the insertion
        assert isinstance(edits[0], Modify) or isinstance(edits[0], Delete)
        assert isinstance(edits[-1], Insert)


class TestSameSignatureDifferentText:
    def test_env_with_same_name_pairs_up_for_recursion(self):
        old = [env("itemize", text_node("\\item a"))]
        new = [env("itemize", text_node("\\item a \\item b"))]
        (edit,) = align(old, new)
        assert isinstance(edit, Modify)
        assert edit.old.name == edit.new.name == "itemize"

    def test_run_of_same_signature_pairs_positionally(self):
        old = [env("itemize", text_node("a")), env("itemize", text_node("b"))]
        new = [env("itemize", text_node("a")), env("itemize", text_node("B"))]
        edits = align(old, new)
        mods = [e for e in edits if isinstance(e, Modify)]
        assert len(mods) == 1
        assert mods[0].new.children[0].text == "B"
