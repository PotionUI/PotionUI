"""The matcher shared by phrasebook find and batch replace."""
import time

import pytest

from src.features.phrasebook.operations.matching import (
    InvalidPattern,
    compile_matcher,
    find_spans,
    substitute,
)


class TestContains:
    def test_case_insensitive_by_default(self):
        m = compile_matcher("dog", "contains")
        assert find_spans(m, "Dog, hotdog, DOGMA") == [(0, 3), (8, 11), (13, 16)]

    def test_case_sensitive(self):
        m = compile_matcher("dog", "contains", case_sensitive=True)
        assert find_spans(m, "Dog, hotdog, DOGMA") == [(8, 11)]

    def test_regex_metacharacters_are_literal(self):
        m = compile_matcher("a.b (c)", "contains")
        assert find_spans(m, "xa.b (c)x axb (c)") == [(1, 8)]

    def test_substitute_is_literal_including_backslashes(self):
        m = compile_matcher("dog", "contains")
        assert substitute(m, "a dog and a Dog", r"cat\1\n") == r"a cat\1\n and a cat\1\n"

    def test_no_hit_yields_no_spans(self):
        assert find_spans(compile_matcher("zzz", "contains"), "a dog") == []


class TestWord:
    def test_matches_whole_words_only(self):
        m = compile_matcher("dog", "word")
        assert find_spans(m, "dog hotdog dogs dog.") == [(0, 3), (16, 19)]

    def test_punctuation_counts_as_a_boundary(self):
        m = compile_matcher("dog", "word")
        assert find_spans(m, "(dog),dog;") == [(1, 4), (6, 9)]

    def test_multi_word_query(self):
        m = compile_matcher("small dog", "word")
        assert find_spans(m, "a small dog, a smallish small dogma") == [(2, 11)]

    def test_case_sensitive(self):
        m = compile_matcher("Dog", "word", case_sensitive=True)
        assert find_spans(m, "dog Dog") == [(4, 7)]

    def test_substitute_only_whole_words(self):
        m = compile_matcher("dog", "word")
        assert substitute(m, "dog hotdog", "cat") == "cat hotdog"


class TestRegex:
    def test_spans_from_finditer(self):
        m = compile_matcher(r"d\w+g", "regex")
        assert find_spans(m, "dog dug ding dg") == [(0, 3), (4, 7), (8, 12)]

    def test_zero_length_matches_are_dropped(self):
        m = compile_matcher(r"x*", "regex")
        assert find_spans(m, "axxb") == [(1, 3)]
        assert find_spans(m, "ab") == []

    def test_case_sensitive_flag(self):
        assert find_spans(compile_matcher("DOG", "regex"), "dog") == [(0, 3)]
        assert find_spans(compile_matcher("DOG", "regex", case_sensitive=True), "dog") == []

    def test_substitute_supports_numbered_and_named_groups(self):
        m = compile_matcher(r"(?P<adj>\w+) (dog|cat)", "regex")
        assert substitute(m, "small dog, big cat", r"\2 that is \g<adj>") == "dog that is small, cat that is big"

    def test_invalid_pattern(self):
        with pytest.raises(InvalidPattern) as excinfo:
            compile_matcher("(dog", "regex")
        assert "missing )" in str(excinfo.value)

    def test_backreference_is_rejected(self):
        with pytest.raises(InvalidPattern) as excinfo:
            compile_matcher(r"(dog)\1", "regex")
        assert "Backreferences" in str(excinfo.value)

    def test_lookaround_is_rejected(self):
        with pytest.raises(InvalidPattern):
            compile_matcher(r"dog(?=s)", "regex")

    def test_overlong_pattern_is_rejected(self):
        with pytest.raises(InvalidPattern) as excinfo:
            compile_matcher("a" * 201, "regex")
        assert "longer than 200" in str(excinfo.value)

    def test_pathological_pattern_finishes_quickly(self):
        m = compile_matcher(r"(a+)+$", "regex")
        started = time.perf_counter()
        assert find_spans(m, "a" * 50_000 + "b") == []
        assert time.perf_counter() - started < 1.0

    def test_trailing_backslash_in_replacement(self):
        m = compile_matcher("(dog)", "regex")
        with pytest.raises(InvalidPattern):
            substitute(m, "dog", "\\")

    def test_invalid_replacement_template(self):
        m = compile_matcher("(dog)", "regex")
        with pytest.raises(InvalidPattern):
            substitute(m, "dog", r"\2")
        with pytest.raises(InvalidPattern):
            substitute(m, "dog", r"\g<nope>")


def test_unknown_mode_is_rejected():
    with pytest.raises(InvalidPattern):
        compile_matcher("dog", "fuzzy")


def test_empty_text_is_safe():
    m = compile_matcher("dog", "contains")
    assert find_spans(m, "") == []
    assert find_spans(m, None) == []
    assert substitute(m, None, "cat") == ""
