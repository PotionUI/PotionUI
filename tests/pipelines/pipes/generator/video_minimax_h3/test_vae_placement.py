"""Tests for `_place_vae`'s fit-aware DiT-offload-before-VAE-move guard."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import torch

from src.pipelines.pipes.generator.video_minimax_h3.main import _MiniMaxH3Ctx, _place_vae

_PATCH_TARGET = "src.pipelines.pipes.generator.video_minimax_h3.main.effective_free_vram_gb"


def _ctx(dit_offload) -> _MiniMaxH3Ctx:
    bundle = SimpleNamespace(dit=SimpleNamespace(offload=dit_offload))
    return _MiniMaxH3Ctx(
        bundle=bundle, conditioning=[], steps=1, height=1, width=1, frames=1,
        num_latent_frames=1, latent_height=1, latent_width=1, num_audio_latents=1,
        device="cuda:0", dtype=torch.float32, spec=None,
    )


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
