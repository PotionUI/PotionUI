"""Tests for `load_yue2_vae` (`vae/loader.py`'s YuE2 wrapper): detection gate,
config-key filtering into `arch.yue2.vae.load`, and the `sd`/`metadata`
passthrough that skips a second file read. No real weights -- tiny real
(non-meta) state dicts, CPU-only."""

from __future__ import annotations

import copy

import pytest
import torch
from torch.nn.utils import weight_norm

from src.platform.runtime.native.arch.yue2.vae import YuE2VAEDecoder
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.vae.loader import load_yue2_vae

_TINY_KWARGS = dict(latent_dim=4, out_channels=2)


def _wrap_with_weight_norm(module: torch.nn.Module) -> None:
    for name, child in list(module.named_children()):
        if isinstance(child, (torch.nn.Conv1d, torch.nn.ConvTranspose1d)):
            setattr(module, name, weight_norm(child))
        else:
            _wrap_with_weight_norm(child)


def _raw_yue2_state_dict(**kwargs):
    reference = YuE2VAEDecoder(**{**_TINY_KWARGS, **kwargs})
    wrapped = copy.deepcopy(reference)
    _wrap_with_weight_norm(wrapped)
    return reference, {f"decoder.{k}": v.detach().clone() for k, v in wrapped.state_dict().items()}


def test_loads_and_decodes_like_the_reference_module():
    torch.manual_seed(0)
    reference, sd = _raw_yue2_state_dict()

    module = load_yue2_vae("yue2_vae.safetensors", sd=sd, metadata={})

    assert isinstance(module, YuE2VAEDecoder)
    latents = torch.randn(1, _TINY_KWARGS["latent_dim"], 5)
    with torch.no_grad():
        expected = reference.decode(latents)
        actual = module.decode(latents)
    assert torch.allclose(expected, actual, atol=1e-5)


def test_config_keys_are_filtered_before_reaching_the_decoder_constructor():
    """`detect_yue2_vae_config` returns `downsampling_ratio` alongside
    `latent_dim`/`out_channels`/`sample_rate` -- `YuE2VAEDecoder.__init__`
    accepts none of that key, so passing the detected config through
    unfiltered would raise a TypeError."""
    torch.manual_seed(0)
    _reference, sd = _raw_yue2_state_dict()

    module = load_yue2_vae("yue2_vae.safetensors", sd=sd, metadata={})

    assert module.sample_rate == 48000


def test_a_non_yue2_checkpoint_raises():
    with pytest.raises(NativeEngineUnsupportedError):
        load_yue2_vae("not_yue2.safetensors", sd={"unrelated.weight": torch.zeros(1)}, metadata={})


def test_sd_and_metadata_passthrough_skips_a_second_file_read(monkeypatch):
    torch.manual_seed(0)
    _reference, sd = _raw_yue2_state_dict()

    def _boom(*args, **kwargs):
        raise AssertionError("load_torch_file must not be called when sd/metadata are already given")

    monkeypatch.setattr("src.platform.runtime.native.vae.loader.load_torch_file", _boom)

    module = load_yue2_vae("yue2_vae.safetensors", sd=sd, metadata={})
    assert isinstance(module, YuE2VAEDecoder)
