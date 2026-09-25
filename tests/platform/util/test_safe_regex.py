import time

import pytest

from src.platform.util.safe_regex import (
    REGEX_MAX_LENGTH,
    InvalidRegex,
    compile_regex,
    compile_user_regex,
)


def test_case_insensitive_by_default():
    assert compile_regex("DOG").search("a dog") is not None


def test_case_sensitive_option():
    assert compile_regex("DOG", case_sensitive=True).search("a dog") is None


def test_backreference_raises_with_hint():
    with pytest.raises(InvalidRegex) as excinfo:
        compile_user_regex(r"(a)\1")
    assert "Backreferences and lookarounds are not supported" in str(excinfo.value)


def test_length_cap():
    compile_user_regex("a" * REGEX_MAX_LENGTH)
    with pytest.raises(InvalidRegex):
        compile_user_regex("a" * (REGEX_MAX_LENGTH + 1))


def test_catastrophic_backtracking_pattern_is_linear():
    compiled = compile_user_regex(r"(a|aa)+$")
    started = time.perf_counter()
    assert compiled.search("a" * 100_000 + "b") is None
    assert time.perf_counter() - started < 1.0
