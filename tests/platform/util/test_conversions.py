"""Tests for src.platform.util.conversions"""

import pytest

from src.platform.util.conversions import str_to_bool


# ---- str_to_bool ----

class TestStrToBool:
    # -- True values --

    def test_true_string_lowercase(self):
        assert str_to_bool("true") is True

    def test_true_string_uppercase(self):
        assert str_to_bool("TRUE") is True

    def test_true_string_mixed_case(self):
        assert str_to_bool("True") is True
        assert str_to_bool("tRuE") is True

    def test_one_string(self):
        assert str_to_bool("1") is True

    def test_yes_string(self):
        assert str_to_bool("yes") is True
        assert str_to_bool("YES") is True
        assert str_to_bool("Yes") is True

    def test_on_string(self):
        assert str_to_bool("on") is True
        assert str_to_bool("ON") is True
        assert str_to_bool("On") is True

    # -- False values --

    def test_false_string_lowercase(self):
        assert str_to_bool("false") is False

    def test_false_string_uppercase(self):
        assert str_to_bool("FALSE") is False

    def test_false_string_mixed_case(self):
        assert str_to_bool("False") is False
        assert str_to_bool("fAlSe") is False

    def test_zero_string(self):
        assert str_to_bool("0") is False

    def test_no_string(self):
        assert str_to_bool("no") is False
        assert str_to_bool("NO") is False
        assert str_to_bool("No") is False

    def test_off_string(self):
        assert str_to_bool("off") is False
        assert str_to_bool("OFF") is False
        assert str_to_bool("Off") is False

    # -- Boolean inputs --

    def test_bool_true(self):
        assert str_to_bool(True) is True

    def test_bool_false(self):
        assert str_to_bool(False) is False

    # -- Integer inputs --

    def test_int_one(self):
        assert str_to_bool(1) is True

    def test_int_zero(self):
        assert str_to_bool(0) is False

    def test_positive_int(self):
        assert str_to_bool(42) is True

    def test_negative_int(self):
        assert str_to_bool(-1) is True

    # -- Edge cases --

    def test_empty_string(self):
        """Empty string is not in the truthy set, so should be False."""
        assert str_to_bool("") is False

    def test_none(self):
        """None is falsy via bool(), so should be False."""
        assert str_to_bool(None) is False

    def test_whitespace_string(self):
        """Whitespace-only string is not in the truthy set."""
        assert str_to_bool("  ") is False

    def test_random_string(self):
        """Unrecognized strings should be False."""
        assert str_to_bool("maybe") is False
        assert str_to_bool("absolutely") is False
        assert str_to_bool("nope") is False

    def test_float_truthy(self):
        """Non-zero float should be True via bool()."""
        assert str_to_bool(1.5) is True

    def test_float_zero(self):
        """Zero float should be False via bool()."""
        assert str_to_bool(0.0) is False

    def test_nonempty_list(self):
        """Non-empty list is truthy via bool()."""
        assert str_to_bool([1, 2]) is True

    def test_empty_list(self):
        """Empty list is falsy via bool()."""
        assert str_to_bool([]) is False
