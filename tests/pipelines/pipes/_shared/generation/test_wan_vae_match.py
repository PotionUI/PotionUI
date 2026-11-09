from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.pipelines.pipes._shared.generation.wan_ti2v import require_matching_wan_vae, wan_vae_latent_channels


def _vae(channels):
    return SimpleNamespace(module=SimpleNamespace(conv2=SimpleNamespace(weight=torch.zeros(channels, channels, 1, 1, 1))))


def _dit(in_dim):
    return SimpleNamespace(module=SimpleNamespace(in_dim=in_dim))


def test_the_latent_channels_are_read_from_the_vae_bottleneck():
    assert wan_vae_latent_channels(_vae(48)) == 48
    assert wan_vae_latent_channels(_vae(16)) == 16
    assert wan_vae_latent_channels(SimpleNamespace(module=SimpleNamespace())) is None


@pytest.mark.parametrize("in_dim, channels", [(48, 48), (16, 16), (36, 16)])
def test_a_matching_vae_passes(in_dim, channels):
    require_matching_wan_vae(_vae(channels), _dit(in_dim), generator="g", set_name="t2v", variant="v")


@pytest.mark.parametrize("in_dim, channels, named", [(48, 16, "wan2.2_vae.safetensors"),
                                                      (16, 48, "wan_2.1_vae.safetensors"),
                                                      (36, 48, "wan_2.1_vae.safetensors")])
def test_a_mismatched_vae_is_refused_naming_the_vae_the_set_needs(in_dim, channels, named):
    with pytest.raises(ValueError, match=named):
        require_matching_wan_vae(_vae(channels), _dit(in_dim), generator="g", set_name="i2v", variant="v")


def test_a_vae_whose_channels_cannot_be_read_is_not_refused():
    require_matching_wan_vae(SimpleNamespace(module=SimpleNamespace()), _dit(48), generator="g", set_name="i2v", variant="v")
