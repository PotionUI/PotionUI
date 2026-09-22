"""Tests for `_place_vae`'s fit-aware DiT-offload-before-VAE-move guard, and
for the sibling post-sampling residency helpers `_dit_fits_resident`/
`_next_gpu_consumer_gb`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import torch

from src.pipelines.pipes.generator.video_minimax_h3.main import (
    _dit_fits_resident,
    _MiniMaxH3Ctx,
    _next_gpu_consumer_gb,
    _place_vae,
)

_PATCH_TARGET = "src.pipelines.pipes.generator.video_minimax_h3.main.effective_free_vram_gb"


def _ctx(dit_offload=None, **over) -> _MiniMaxH3Ctx:
    bundle = SimpleNamespace(dit=SimpleNamespace(offload=dit_offload or Mock()))
    fields = dict(
        bundle=bundle, conditioning=[], steps=1, height=1, width=1, frames=1,
        num_latent_frames=1, latent_height=1, latent_width=1, num_audio_latents=1,
        device="cuda:0", dtype=torch.float32, spec=None,
    )
    fields.update(over)
    return _MiniMaxH3Ctx(**fields)


def test_place_vae_with_plenty_of_free_vram_moves_without_offloading_dit():
    dit_offload = Mock()
    c = _ctx(dit_offload)
    vae = SimpleNamespace(estimated_vram_gb=2.0, move_to=Mock())
    with patch(_PATCH_TARGET, return_value=10.0):
        _place_vae(c, vae)
    dit_offload.assert_not_called()
    vae.move_to.assert_called_once_with(c.device)


def test_place_vae_offloads_dit_first_when_vae_would_not_otherwise_fit():
    calls = []
    dit_offload = Mock(side_effect=lambda: calls.append("dit_offload"))
    c = _ctx(dit_offload)
    vae = SimpleNamespace(
        estimated_vram_gb=6.0, move_to=Mock(side_effect=lambda d: calls.append("vae_move_to")),
    )
    with patch(_PATCH_TARGET, return_value=2.0):
        _place_vae(c, vae)
    dit_offload.assert_called_once()
    vae.move_to.assert_called_once_with(c.device)
    assert calls == ["dit_offload", "vae_move_to"]


def test_place_vae_with_unknown_free_vram_moves_without_offloading_dit():
    dit_offload = Mock()
    c = _ctx(dit_offload)
    vae = SimpleNamespace(estimated_vram_gb=100.0, move_to=Mock())
    with patch(_PATCH_TARGET, return_value=None):
        _place_vae(c, vae)
    dit_offload.assert_not_called()
    vae.move_to.assert_called_once_with(c.device)


# -- `_dit_fits_resident` / `_next_gpu_consumer_gb` ----------------

def test_dit_fits_resident_true_when_free_vram_covers_need_plus_reserve():
    c = _ctx()
    with patch(_PATCH_TARGET, return_value=10.0):
        assert _dit_fits_resident(c, 5.0) is True


def test_dit_fits_resident_false_when_free_vram_is_short():
    c = _ctx()
    with patch(_PATCH_TARGET, return_value=5.5):
        assert _dit_fits_resident(c, 5.0) is False


def test_dit_fits_resident_false_when_free_vram_unknown():
    c = _ctx()
    with patch(_PATCH_TARGET, return_value=None):
        assert _dit_fits_resident(c, 0.0) is False


def _residency_bundle(video_vae_module=None) -> SimpleNamespace:
    return SimpleNamespace(
        dit=SimpleNamespace(offload=Mock()),
        audio_vae=SimpleNamespace(estimated_vram_gb=0.75),
        video_vae=SimpleNamespace(estimated_vram_gb=2.0, module=video_vae_module),
    )


def test_next_gpu_consumer_gb_is_audio_vae_size_when_audio_source_is_generate():
    c = _ctx(bundle=_residency_bundle(), audio_source="generate", decode=True)
    assert _next_gpu_consumer_gb(c) == pytest.approx(0.75)


def test_next_gpu_consumer_gb_is_zero_when_no_decode_and_no_audio_generate():
    c = _ctx(bundle=_residency_bundle(), audio_source="passthrough", decode=False)
    assert _next_gpu_consumer_gb(c) == 0.0


def test_next_gpu_consumer_gb_falls_back_to_video_vae_weight_when_module_missing():
    c = _ctx(bundle=_residency_bundle(video_vae_module=None), audio_source="passthrough", decode=True)
    assert _next_gpu_consumer_gb(c) == pytest.approx(2.0)


def test_next_gpu_consumer_gb_adds_decode_peak_estimate_for_video_vae():
    vae_module = SimpleNamespace(
        decoder=SimpleNamespace(
            transformer_blocks=[SimpleNamespace(
                attn=SimpleNamespace(to_out=SimpleNamespace(out_features=4, in_features=4)),
                ff=SimpleNamespace(w2=SimpleNamespace(in_features=8)),
            )],
            proj_out=SimpleNamespace(out_features=3),
            rope=SimpleNamespace(inv_freq=torch.zeros(2), num_axes=3),
            num_register_tokens=1,
        ),
        spatial_compression_ratio=1,
        use_tiling=False,
        tokens_chunk_size=1,
        token_overlap=0,
        parameters=lambda: iter([torch.zeros(1, dtype=torch.float16)]),
    )
    c = _ctx(
        bundle=_residency_bundle(video_vae_module=vae_module), audio_source="passthrough", decode=True,
        latent_height=2, latent_width=2,
    )
    assert _next_gpu_consumer_gb(c) > 2.0
