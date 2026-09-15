"""Prompt-id assembly, the context-budget guard, and acoustic chunk ranges."""

from __future__ import annotations

import pytest

from src.platform.runtime.native.arch.yue2.protocol import (
    ABC_END,
    ABC_START,
    CODEC_OFFSET,
    CODEC_SIZE,
    CONTEXT,
    EOD,
    MUSIC_END,
    MUSIC_START,
    VOCAB_SIZE,
    Sampling,
    build_negative_prompt_ids,
    build_prompt_ids,
    chunk_ranges,
    ensure_prompt_fits,
    guidance_scale,
)


def _encode(text: str) -> list[int]:
    return [len(word) for word in text.split()]


class TestConstants:
    def test_special_token_ids(self):
        assert EOD == 151643
        assert (ABC_START, ABC_END) == (151847, 151848)
        assert (MUSIC_START, MUSIC_END) == (151851, 151852)
        assert (CODEC_OFFSET, CODEC_SIZE) == (151853, 32768)
        assert (VOCAB_SIZE, CONTEXT) == (184704, 24576)


class TestBuildPromptIds:
    def test_cot_off_has_no_abc_span(self):
        ids = build_prompt_ids(_encode, "do the thing", "pop", "la la", cot="off")
        assert ids[0] == EOD
        assert ids[-3:] == [ABC_START, ABC_END, MUSIC_START]

    def test_cot_full_without_abc_stops_after_abc_start(self):
        ids = build_prompt_ids(_encode, "do the thing", "pop", "la la", cot="full")
        assert ids[-1] == ABC_START
        assert MUSIC_START not in ids

    def test_cot_full_with_abc_appends_end_and_music_start(self):
        ids = build_prompt_ids(_encode, "do the thing", "pop", "la la", abc="X:1", cot="full")
        assert ids[-4] == ABC_START
        assert ids[-2:] == [ABC_END, MUSIC_START]

    def test_prompt_body_precedes_abc_span(self):
        ids = build_prompt_ids(_encode, "do the thing", "pop", "la la", abc="X:1", cot="full")
        body = _encode("do the thing\n[Tags]\npop\n[Lyrics]\nla la\n")
        assert ids[:1 + len(body)] == [EOD, *body]

    def test_rejects_invalid_cot(self):
        with pytest.raises(ValueError):
            build_prompt_ids(_encode, "x", "y", "z", cot="nonsense")

    def test_rejects_abc_ids_touching_the_special_token_region(self):
        def encode_out_of_range(text: str) -> list[int]:
            return [EOD] if text == "bad" else _encode(text)

        with pytest.raises(ValueError):
            build_prompt_ids(encode_out_of_range, "x", "y", "z", abc="bad", cot="full")


class TestBuildNegativePromptIds:
    def test_cot_off_shape(self):
        ids = build_negative_prompt_ids(_encode, "off")
        assert ids[0] == EOD
        assert ids[-1] == MUSIC_START

    def test_cot_full_requires_abc_ids(self):
        with pytest.raises(ValueError):
            build_negative_prompt_ids(_encode, "full")

    def test_cot_full_echoes_exact_abc_ids(self):
        ids = build_negative_prompt_ids(_encode, "full", abc_ids=[7, 8, 9])
        assert ids[-6:] == [ABC_START, 7, 8, 9, ABC_END, MUSIC_START]


class TestGuidanceScale:
    def test_off_defaults_to_1_01(self):
        assert guidance_scale("off") == 1.01

    def test_full_defaults_to_1_0(self):
        assert guidance_scale("full") == 1.0

    def test_explicit_override_wins(self):
        assert guidance_scale("off", cfg_scale=3.0) == 3.0


class TestEnsurePromptFits:
    def test_passes_when_within_context(self):
        ensure_prompt_fits(list(range(100)), 500, context=1000)

    def test_raises_when_prompt_plus_budget_exceeds_context(self):
        with pytest.raises(ValueError):
            ensure_prompt_fits(list(range(600)), 500, context=1000)


class TestSamplingDefaults:
    def test_semantic_defaults(self):
        s = Sampling()
        assert (s.temperature, s.top_p, s.top_k) == (1.0, 0.95, 100)
        assert (s.repetition_penalty, s.penalty_window) == (1.2, 50)
        assert (s.min_tokens, s.max_tokens) == (200, 9000)

    def test_rejects_min_tokens_above_max_tokens(self):
        with pytest.raises(ValueError):
            Sampling(min_tokens=100, max_tokens=50)


class TestChunkRanges:
    def test_single_window_when_codec_is_short(self):
        ranges = chunk_ranges(frames=10, prefix_tokens=5, context=1000)
        assert ranges == [(0, 10)]

    def test_tiles_without_gap_or_overlap(self):
        ranges = chunk_ranges(frames=25, prefix_tokens=0, context=23)
        assert ranges[0][0] == 0
        assert ranges[-1][1] == 25
        for (_, end), (start, _) in zip(ranges, ranges[1:]):
            assert end == start

    def test_raises_when_prefix_leaves_no_room(self):
        with pytest.raises(ValueError):
            chunk_ranges(frames=10, prefix_tokens=1000, context=1000)
