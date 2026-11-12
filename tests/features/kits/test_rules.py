import pytest

from src.features.kits import rules
from src.platform.http.errors import ConflictError, InvalidInputError


def test_capitals_become_lowercase_and_spaces_underscores():
    assert rules.validate_path_name("Old Harbour", "kit") == "old_harbour"


@pytest.mark.parametrize("raw", ["", "1face", "_face", "face-left", "a" * 33, "naïve"])
def test_a_path_name_outside_the_rule_is_refused(raw):
    with pytest.raises(InvalidInputError) as caught:
        rules.validate_path_name(raw, "item")
    assert caught.value.code == "invalid_item_path"


def test_thirty_two_characters_is_the_longest_path_name():
    assert rules.validate_path_name("a" * 32, "kit") == "a" * 32


@pytest.mark.parametrize("display,expected", [
    ("Old harbour", "old_harbour"),
    ("  Mara  ", "mara"),
    ("Café 2", "caf_2"),
    ("2nd unit", "kit_2nd_unit"),
    ("???", "kit"),
])
def test_a_path_name_is_derived_from_the_display_name(display, expected):
    assert rules.derive_path_name(display, "kit") == expected


def test_a_derived_path_name_never_exceeds_the_limit():
    assert len(rules.derive_path_name("word " * 20, "kit")) <= rules.MAX_PATH_NAME_CHARS


def test_the_free_name_counts_up_from_two_and_stays_inside_the_limit():
    taken = {"face", "face2"}
    assert rules.free_path_name("face", taken.__contains__) == "face3"
    long_taken = {"a" * 32, "a" * 31 + "2"}
    assert rules.free_path_name("a" * 32, long_taken.__contains__) == "a" * 31 + "3"


def test_a_clash_carries_a_suggestion():
    error = rules.clash("item", "face", {"face"}.__contains__)
    assert isinstance(error, ConflictError)
    assert error.code == "item_path_taken"
    assert error.extra["suggestion"] == "face2"


def test_default_names_take_the_lowest_free_number_of_the_kind():
    assert rules.default_item_name("image", []) == "image1"
    assert rules.default_item_name("image", ["image1", "video1", "image2"]) == "image3"
    assert rules.default_item_name("audio", ["image1"]) == "audio1"
    assert rules.default_item_name("image", ["image2"]) == "image1"


def test_a_label_collapses_blanks_and_empty_becomes_none():
    assert rules.clean_label("  Actor   lead ") == "Actor lead"
    assert rules.clean_label("   ") is None
    assert rules.clean_label(None) is None
