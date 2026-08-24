import pickle
from pathlib import Path

import pytest

from src.pipelines.pipes._shared.generation.prompt_diff import word_diff


class TestWordDiff:
    def test_identical_inputs_have_no_markers(self):
        result = word_diff("same text", "same text")
        assert all(marker is None for _, marker in result)
        assert "".join(token for token, _ in result) == "same text"

    def test_insertion_marks_new_words_with_plus(self):
        result = word_diff("hello world", "hello there world")
        inserted = [token for token, marker in result if marker == "+"]
        assert inserted == ["there", " "]

    def test_deletion_marks_removed_words_with_minus(self):
        result = word_diff("the quick brown fox", "the fox")
        removed = [token for token, marker in result if marker == "-"]
        assert removed == ["quick", " ", "brown", " "]

    def test_replacement_marks_old_as_minus_and_new_as_plus(self):
        result = word_diff("a cat", "a dog")
        assert ("cat", "-") in result
        assert ("dog", "+") in result
        assert ("a", None) in result

    def test_whitespace_is_preserved_as_separate_tokens(self):
        result = word_diff("a  b   c", "a b c")
        # Every space-only token must appear in the result, either changed or not
        space_tokens = [token for token, _ in result if token.isspace()]
        assert space_tokens  # spaces preserved, not silently dropped
        assert "".join(token for token, _ in result).replace(" ", "") == "abc"

    def test_empty_original_is_all_insertions(self):
        result = word_diff("", "new text here")
        assert result
        assert all(marker == "+" for _, marker in result)

    def test_empty_target_is_all_deletions(self):
        result = word_diff("old text here", "")
        assert result
        assert all(marker == "-" for _, marker in result)

    def test_both_empty_yields_empty_diff(self):
        assert word_diff("", "") == []

    def test_return_type_is_list_of_tuples(self):
        result = word_diff("a", "b")
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2


class TestWordDiffEquivalenceWithPreRefactorImplementations:
    """
    Before extracting `word_diff`, prompt_encoder.diff_prompts() and
    PromptExpanderPipe._generate_diff() each had their own copy of this
    algorithm. This replays fixture pairs captured from those two original
    implementations (see the scratch capture script used during the refactor)
    and asserts the shared implementation reproduces them exactly.
    """

    FIXTURE_PAIRS = [
        ("a cat", "a dog"),
        ("hello world", "hello there world"),
        ("the quick brown fox", "the fox"),
        ("same text", "same text"),
        ("a  b   c", "a b c"),
        ("", "new text here"),
        ("old text here", ""),
        ("multi\nline\ntext", "multi line text changed"),
        ("Hello, World!", "Hello, Brave New World!"),
    ]

    # Captured verbatim from the pre-refactor implementations of both
    # src.pipelines.pipes.prompt_encoder.main.diff_prompts() and
    # src.pipelines.pipes.prompt_expander.main.PromptExpanderPipe._generate_diff()
    # (the two were confirmed byte-for-byte identical on every fixture pair
    # prior to extraction).
    EXPECTED = [
        [("a", None), (" ", None), ("cat", "-"), ("dog", "+")],
        [("hello", None), (" ", None), ("there", "+"), (" ", "+"), ("world", None)],
        [("the", None), (" ", None), ("quick", "-"), (" ", "-"), ("brown", "-"), (" ", "-"), ("fox", None)],
        [("same", None), (" ", None), ("text", None)],
        [("a", None), (" ", "-"), (" ", None), ("b", None), (" ", None), (" ", "-"), (" ", "-"), ("c", None)],
        [("new", "+"), (" ", "+"), ("text", "+"), (" ", "+"), ("here", "+")],
        [("old", "-"), (" ", "-"), ("text", "-"), (" ", "-"), ("here", "-")],
        [("multi", None), ("\n", "-"), (" ", "+"), ("line", None), ("\n", "-"), (" ", "+"),
         ("text", None), (" ", "+"), ("changed", "+")],
        [("Hello,", None), (" ", None), ("Brave", "+"), (" ", "+"), ("New", "+"), (" ", "+"), ("World!", None)],
    ]

    @pytest.mark.parametrize("index", range(len(FIXTURE_PAIRS)))
    def test_matches_captured_pre_refactor_output(self, index):
        text1, text2 = self.FIXTURE_PAIRS[index]
        assert word_diff(text1, text2) == self.EXPECTED[index]
