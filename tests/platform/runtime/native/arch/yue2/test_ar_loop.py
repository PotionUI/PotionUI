"""Behavioral tests for the YuE2 AR generation loop against a fake backbone:
end-token stopping, max-token honoring, cancellation, and progress callback.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.arch.yue2 import ar_loop
from src.platform.runtime.native.arch.yue2.protocol import CODEC_OFFSET, MUSIC_END, Sampling, VOCAB_SIZE
from src.platform.runtime.native.errors import SamplingCancelled


class _FakeModel:
    def __init__(self, dominant_token: int, hidden_size: int = 4, max_position_embeddings: int = 100_000):
        self.cfg = SimpleNamespace(max_position_embeddings=max_position_embeddings)
        self._dominant = dominant_token
        self._hidden_size = hidden_size
        self._param = torch.zeros(1)
        self.prefill_calls = 0
        self.step_calls = 0

    def parameters(self):
        yield self._param

    def new_kv_cache(self, max_len, batch=1, device=None, dtype=torch.bfloat16):
        return SimpleNamespace(filled_len=0, max_len=max_len)

    def prefill(self, ids, cache):
        self.prefill_calls += 1
        cache.filled_len = ids.shape[1]
        return torch.zeros(ids.shape[0], ids.shape[1], self._hidden_size)

    def step(self, ids, cache):
        self.step_calls += 1
        cache.filled_len += 1
        return torch.zeros(ids.shape[0], 1, self._hidden_size)

    def lm_head_logits(self, hidden):
        logits = torch.full((hidden.shape[0], VOCAB_SIZE), -1e4)
        logits[..., self._dominant] = 1e4
        return logits


def _sampling(max_tokens: int) -> Sampling:
    return Sampling(min_tokens=0, max_tokens=max_tokens)


class TestStopping:
    def test_stops_immediately_at_end_token(self):
        model = _FakeModel(dominant_token=MUSIC_END)
        history, stopped = ar_loop.generate(model, [1, 2, 3], _sampling(10), seed=0, phase="semantic")
        assert history == []
        assert stopped is True

    def test_honours_max_tokens_when_end_token_never_wins(self):
        model = _FakeModel(dominant_token=CODEC_OFFSET + 3)
        history, stopped = ar_loop.generate(model, [1, 2, 3], _sampling(5), seed=0, phase="semantic")
        assert len(history) == 5
        assert stopped is False
        assert all(token == CODEC_OFFSET + 3 for token in history)


class TestCancellation:
    def test_raises_before_prefill_when_already_cancelled(self):
        model = _FakeModel(dominant_token=CODEC_OFFSET + 1)
        with pytest.raises(SamplingCancelled):
            ar_loop.generate(model, [1, 2, 3], _sampling(5), seed=0, phase="semantic", is_cancelled=lambda: True)

    def test_raises_mid_loop_after_n_steps(self):
        model = _FakeModel(dominant_token=CODEC_OFFSET + 1)
        seen = {"count": 0}

        def is_cancelled():
            seen["count"] += 1
            return seen["count"] > 2

        with pytest.raises(SamplingCancelled):
            ar_loop.generate(model, [1, 2, 3], _sampling(10), seed=0, phase="semantic", is_cancelled=is_cancelled)


class TestOnFrame:
    def test_called_once_per_step_with_running_total(self):
        model = _FakeModel(dominant_token=CODEC_OFFSET + 1)
        calls = []
        ar_loop.generate(model, [1, 2, 3], _sampling(4), seed=0, phase="semantic",
                          on_frame=lambda i, total: calls.append((i, total)))
        assert calls == [(0, 4), (1, 4), (2, 4), (3, 4)]


class TestCfg:
    def test_requires_negative_prefix_when_scale_is_not_one(self):
        model = _FakeModel(dominant_token=CODEC_OFFSET + 1)
        with pytest.raises(ValueError):
            ar_loop.generate(model, [1, 2, 3], _sampling(5), seed=0, phase="semantic", cfg_scale=1.5)

    def test_negative_branch_runs_once_per_step_when_cfg_active(self):
        model = _FakeModel(dominant_token=CODEC_OFFSET + 1)
        ar_loop.generate(model, [1, 2, 3], _sampling(3), seed=0, phase="semantic",
                          negative_ids=[9, 9], cfg_scale=1.01)
        assert model.prefill_calls == 2
        assert model.step_calls == 4

    def test_no_negative_branch_when_scale_is_one(self):
        model = _FakeModel(dominant_token=CODEC_OFFSET + 1)
        ar_loop.generate(model, [1, 2, 3], _sampling(3), seed=0, phase="semantic")
        assert model.prefill_calls == 1
        assert model.step_calls == 2
