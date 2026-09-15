"""Tests for the YuE2 acoustic (NAR) windowing and flow-matching loop."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.yue2 import nar
from src.platform.runtime.native.arch.yue2.config import YuE2Config
from src.platform.runtime.native.arch.yue2.model import YuE2Model
from src.platform.runtime.native.arch.yue2.protocol import CODEC_OFFSET, MUSIC_END
from src.platform.runtime.native.errors import SamplingCancelled
from vendor.gpl.comfyui.ops import disable_weight_init


def _tiny_model(seed: int = 0) -> YuE2Model:
    cfg = YuE2Config(
        hidden_size=16, num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=1,
        head_dim=8, intermediate_size=24, vocab_size=184704, rope_theta=10000.0,
        max_position_embeddings=256, latent_dim=64, max_latent_frames=64, timestep_shift=1.0,
    )
    model = YuE2Model(cfg, disable_weight_init)
    generator = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for p in model.parameters():
            p.copy_(torch.randn(p.shape, generator=generator) * 0.1)
    model.post_load()
    model.eval()
    return model


class TestSongChunks:
    def test_single_window_appends_music_end_and_offsets_codec_ids(self):
        chunks = nar.song_chunks([1, 2, 3], [0, 1, 2], seed=0, context=1000)
        assert len(chunks) == 1
        assert chunks[0].ar_tokens == [1, 2, 3, CODEC_OFFSET, CODEC_OFFSET + 1, CODEC_OFFSET + 2, MUSIC_END]
        assert chunks[0].noise.shape == (3, 64)

    def test_rejects_empty_codec(self):
        with pytest.raises(ValueError):
            nar.song_chunks([1, 2, 3], [], seed=0)

    def test_rejects_codec_ids_outside_the_codebook(self):
        with pytest.raises(ValueError):
            nar.song_chunks([1, 2, 3], [0, 40000], seed=0)

    def test_noise_is_deterministic_for_a_fixed_seed(self):
        a = nar.song_chunks([1, 2, 3], [0, 1, 2, 3, 4], seed=7, context=1000)
        b = nar.song_chunks([1, 2, 3], [0, 1, 2, 3, 4], seed=7, context=1000)
        torch.testing.assert_close(a[0].noise, b[0].noise)

    def test_windows_tile_the_full_codec_sequence(self):
        codec = list(range(20))
        chunks = nar.song_chunks([1] * 3, codec, seed=0, context=23)
        assert len(chunks) > 1
        total_noise_frames = sum(c.noise.shape[0] for c in chunks)
        assert total_noise_frames == len(codec)


class TestSynthesize:
    def test_output_shape(self):
        model = _tiny_model()
        latents = nar.synthesize(model, [1, 2, 3], [0, 1, 2], seed=0, steps=2)
        assert latents.shape == (1, 3, 64)
        assert latents.dtype == torch.float32

    def test_on_step_reaches_total_at_the_end(self):
        model = _tiny_model()
        calls = []
        nar.synthesize(model, [1, 2, 3], [0, 1, 2], seed=0, steps=4,
                        on_step=lambda done, total: calls.append((done, total)))
        assert calls[-1] == (4, 4)
        assert len(calls) == 4

    def test_rejects_nonpositive_steps(self):
        model = _tiny_model()
        with pytest.raises(ValueError):
            nar.synthesize(model, [1, 2, 3], [0, 1, 2], seed=0, steps=0)

    def test_cancellation_raises_sampling_cancelled(self):
        model = _tiny_model()
        with pytest.raises(SamplingCancelled):
            nar.synthesize(model, [1, 2, 3], [0, 1, 2], seed=0, steps=4, is_cancelled=lambda: True)

    def test_cancellation_mid_loop(self):
        model = _tiny_model()
        seen = {"count": 0}

        def is_cancelled():
            seen["count"] += 1
            return seen["count"] > 2

        with pytest.raises(SamplingCancelled):
            nar.synthesize(model, [1, 2, 3], [0, 1, 2], seed=0, steps=8, is_cancelled=is_cancelled)

    def test_multi_window_song_concatenates_all_windows(self):
        model = _tiny_model()
        codec = list(range(10))
        latents = nar.synthesize(model, [1] * 3, codec, seed=0, steps=1, context=13)
        assert latents.shape == (1, 10, 64)
