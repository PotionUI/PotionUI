"""Unit tests for the YuE2 sampling primitives: repetition penalty, phase
vocab masking, CFG blending, and id sampling.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.yue2.protocol import ABC_END, CODEC_OFFSET, CODEC_SIZE, EOD, MUSIC_END, Sampling, VOCAB_SIZE
from src.platform.runtime.native.arch.yue2.sampling import guided_scores, repetition_penalty, sample_id


class TestRepetitionPenalty:
    def test_no_op_when_penalty_is_one(self):
        scores = torch.randn(1, 10)
        assert torch.equal(repetition_penalty(scores, [1, 2], 1.0), scores)

    def test_no_op_when_history_is_empty(self):
        scores = torch.randn(1, 10)
        assert torch.equal(repetition_penalty(scores, [], 1.2), scores)

    def test_positive_score_is_divided_by_penalty(self):
        scores = torch.zeros(1, 5)
        scores[0, 2] = 4.0
        out = repetition_penalty(scores, [2], 2.0)
        assert out[0, 2].item() == 2.0

    def test_negative_score_is_multiplied_by_penalty(self):
        scores = torch.zeros(1, 5)
        scores[0, 2] = -4.0
        out = repetition_penalty(scores, [2], 2.0)
        assert out[0, 2].item() == -8.0


class TestGuidedScoresPhaseMask:
    def test_semantic_phase_masks_everything_outside_the_codec_window(self):
        logits = torch.zeros(1, VOCAB_SIZE)
        sampling = Sampling(min_tokens=0, top_p=1.0)
        scores = guided_scores(logits, None, 1.0, sampling, [], 0, "semantic")
        assert torch.isneginf(scores[0, 0])
        assert not torch.isneginf(scores[0, CODEC_OFFSET])
        assert not torch.isneginf(scores[0, CODEC_OFFSET + CODEC_SIZE - 1])
        assert torch.isneginf(scores[0, CODEC_OFFSET + CODEC_SIZE])
        assert not torch.isneginf(scores[0, MUSIC_END])

    def test_abc_phase_masks_the_codec_window(self):
        logits = torch.zeros(1, VOCAB_SIZE)
        sampling = Sampling(min_tokens=0, max_tokens=10, top_p=1.0)
        scores = guided_scores(logits, None, 1.0, sampling, [], 0, "abc")
        assert not torch.isneginf(scores[0, 0])
        assert torch.isneginf(scores[0, CODEC_OFFSET])
        assert not torch.isneginf(scores[0, ABC_END])

    def test_min_tokens_gate_blocks_the_end_token(self):
        logits = torch.zeros(1, VOCAB_SIZE)
        sampling = Sampling(min_tokens=5, max_tokens=10, top_p=1.0)
        scores = guided_scores(logits, None, 1.0, sampling, [], 0, "semantic")
        assert torch.isneginf(scores[0, MUSIC_END])
        scores_later = guided_scores(logits, None, 1.0, sampling, [], 5, "semantic")
        assert not torch.isneginf(scores_later[0, MUSIC_END])


class TestGuidedScoresCfgBlend:
    def test_scale_one_ignores_unconditional_branch(self):
        cond = torch.zeros(1, VOCAB_SIZE)
        cond[0, CODEC_OFFSET] = 5.0
        sampling = Sampling(min_tokens=0)
        scores = guided_scores(cond, None, 1.0, sampling, [], 0, "semantic")
        assert scores[0, CODEC_OFFSET].item() == 5.0

    def test_requires_unconditional_branch_when_scale_is_not_one(self):
        cond = torch.zeros(1, VOCAB_SIZE)
        sampling = Sampling(min_tokens=0)
        try:
            guided_scores(cond, None, 1.5, sampling, [], 0, "semantic")
        except ValueError:
            return
        raise AssertionError("expected ValueError")

    def test_blends_conditional_and_unconditional(self):
        cond = torch.zeros(1, VOCAB_SIZE)
        uncond = torch.zeros(1, VOCAB_SIZE)
        cond[0, CODEC_OFFSET] = 10.0
        uncond[0, CODEC_OFFSET] = 2.0
        sampling = Sampling(min_tokens=0)
        scores = guided_scores(cond, uncond, 2.0, sampling, [], 0, "semantic")
        assert scores[0, CODEC_OFFSET].item() == 2.0 + 2.0 * (10.0 - 2.0)


class TestSampleId:
    def test_temperature_zero_is_deterministic_argmax(self):
        scores = torch.full((1, 20), -1.0)
        scores[0, 7] = 5.0
        sampling = Sampling(temperature=0, min_tokens=0)
        generator = torch.Generator().manual_seed(0)
        token = sample_id(scores, sampling, generator)
        assert token.shape == (1, 1)
        assert token.item() == 7

    def test_seeded_generator_is_reproducible(self):
        scores = torch.randn(1, 50)
        sampling = Sampling(min_tokens=0)
        a = sample_id(scores.clone(), sampling, torch.Generator().manual_seed(42))
        b = sample_id(scores.clone(), sampling, torch.Generator().manual_seed(42))
        assert torch.equal(a, b)
