"""A wrong Video-VAE pick (another family's file) loads cleanly under its own
architecture and used to die deep in conditioning with a bare AttributeError
('LTXDiffusionVideoVAE' object has no attribute 'latents_mean' - observed on a
real refs-mode run). The guard fails at first use naming the fix."""

import pytest
import torch

from src.pipelines.pipes.generator.video_minimax_h3.main import _require_h3_video_vae


class _WrongFamilyVAE(torch.nn.Module):
    pass


class _H3LikeVAE(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.latents_mean = torch.zeros(24)
        self.latents_std = torch.ones(24)


def test_wrong_family_vae_raises_named_error():
    with pytest.raises(ValueError, match="_WrongFamilyVAE.*not a.*MiniMax-H3 video VAE"):
        _require_h3_video_vae(_WrongFamilyVAE())


def test_h3_shaped_vae_passes_through():
    module = _H3LikeVAE()
    assert _require_h3_video_vae(module) is module
