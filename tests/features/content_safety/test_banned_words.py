from src.features.content_safety.banned_words import (
    BannedWordsMatcher,
    collect_positive_texts,
    validate_entries,
)


def test_empty_list_matches_nothing():
    matcher = BannedWordsMatcher([])

    assert matcher.empty
    assert matcher.first_match("anything at all") is None


def test_match_is_case_insensitive_and_reports_the_entry_index():
    matcher = BannedWordsMatcher(["alpha", "Bravo"])

    assert matcher.first_match("a photo of BRAVO") == 1
    assert matcher.first_match("an ALPHA photo") == 0


def test_whole_word_only():
    matcher = BannedWordsMatcher(["ass"])

    assert matcher.first_match("a classic assassin") is None
    assert matcher.first_match("just an ass, really") == 0


def test_wildcard_matches_any_word_ending():
    matcher = BannedWordsMatcher(["nud*"])

    assert matcher.first_match("a nude figure") == 0
    assert matcher.first_match("nudity") == 0
    assert matcher.first_match("denuded") is None


def test_leading_wildcard_and_phrase():
    matcher = BannedWordsMatcher(["*gore", "red   paint"])

    assert matcher.first_match("splatter gore here") == 0
    assert matcher.first_match("some red paint") == 1


def test_unicode_is_normalised_before_matching():
    matcher = BannedWordsMatcher(["naked"])

    assert matcher.first_match("ｎａｋｅｄ") == 0
    assert matcher.first_match("NAKED") == 0


def test_punctuation_is_a_word_boundary():
    matcher = BannedWordsMatcher(["blood"])

    assert matcher.first_match("(blood), dark") == 0
    assert matcher.first_match("bloodline") is None


def test_regex_metacharacters_in_entries_are_literal():
    matcher = BannedWordsMatcher(["a+b", "(x)"])

    assert matcher.first_match("a+b together") == 0
    assert matcher.first_match("aab") is None


def test_blank_entries_are_ignored():
    matcher = BannedWordsMatcher(["", "  ", "real"])

    assert matcher.first_match("real thing") == 2
    assert matcher.first_match("") is None


def test_validation_accepts_a_list_of_words():
    assert validate_entries(["a", "b*", ""]) is None


def test_validation_rejects_bad_shapes():
    assert validate_entries("a, b") is not None
    assert validate_entries(["ok", 3]) is not None
    assert validate_entries(["*"]) is not None
    assert validate_entries(["x" * 500]) is not None


def test_collects_every_expanded_pair():
    prompts = [{"positive": "one", "negative": "neg"}, {"positive": "two", "negative": ""}]

    assert collect_positive_texts(prompts, {}) == ["one", "two"]


def test_collects_director_segment_prompts_but_not_negatives():
    form = {"video_director": {"segments": [
        {"prompt": "first shot", "negative_prompt": "hidden negative"},
        {"prompt": "second shot"},
    ]}}

    assert collect_positive_texts([], form) == ["first shot", "second shot"]


def test_collects_music_document_text():
    form = {"music_director": {
        "description": "a song",
        "compiled_lyrics": "la la",
        "sections": [{"lyrics": "verse", "style_hint": "soft"}],
    }}

    assert collect_positive_texts(None, form) == ["a song", "la la", "verse", "soft"]


def test_pathological_wildcard_entry_finishes_in_bounded_time():
    import time

    matcher = BannedWordsMatcher(["*a*a*a*a*a*a*b"])

    started = time.perf_counter()
    result = matcher.first_match("a" * 300)
    elapsed = time.perf_counter() - started

    assert result is None
    assert elapsed < 0.05


def test_consecutive_wildcards_collapse():
    assert BannedWordsMatcher(["n***e"]).first_match("nude") == 0


def test_many_words_and_entries_stay_linear_enough():
    import time

    matcher = BannedWordsMatcher([f"word{i}*" for i in range(2000)])
    started = time.perf_counter()
    matcher.first_match("lorem ipsum " * 4000)

    assert time.perf_counter() - started < 0.5


def test_validation_caps_wildcards_per_entry():
    assert validate_entries(["a*b*c*d"]) is None
    assert validate_entries(["a*b*c*d*e"]) is not None
    assert validate_entries(["*a* *b* *c*"]) is not None
