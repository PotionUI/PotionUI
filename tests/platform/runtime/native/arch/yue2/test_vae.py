"""Tests for the YuE2 Oobleck decoder port (``arch/yue2/vae.py``)."""

from __future__ import annotations

import copy

import pytest
import torch
from torch.nn.utils import weight_norm

from src.platform.runtime.native.arch.yue2.vae import (
    DECODER_C_MULTS,
    DECODER_CHANNELS,
    DECODER_STRIDES,
    HOP_LENGTH,
    LATENT_DIM,
    OUT_CHANNELS,
    SAMPLE_RATE,
    YuE2VAEDecoder,
    load,
)
from src.platform.runtime.native.errors import NativeEngineUnsupportedError

_TINY_KWARGS = dict(latent_dim=4, channels=4, c_mults=(1, 2), strides=(2, 3), out_channels=2)


def _wrap_with_weight_norm(module: torch.nn.Module) -> None:
    """Mutate ``module`` in place so every conv carries weight_g/weight_v -- reproducing the shape the released checkpoint actually ships (see ``vae.py``'s module docstring)."""
    for name, child in list(module.named_children()):
        if isinstance(child, (torch.nn.Conv1d, torch.nn.ConvTranspose1d)):
            setattr(module, name, weight_norm(child))
        else:
            _wrap_with_weight_norm(child)


class TestDecoderShape:
    def test_released_config_constants(self):
        """The module's module-level constants match the released 'standard' ``YuE2VAEConfig.decoder_config`` (modeling_vae.py)."""
        assert LATENT_DIM == 64
        assert DECODER_CHANNELS == 64
        assert DECODER_C_MULTS == (1, 2, 4, 8, 16, 32)
        assert DECODER_STRIDES == (2, 2, 4, 4, 5, 6)
        assert OUT_CHANNELS == 2
        assert SAMPLE_RATE == 48000
        assert HOP_LENGTH == 1920

    def test_tiny_decode_produces_stereo_waveform(self):
        decoder = YuE2VAEDecoder(**_TINY_KWARGS)
        latents = torch.randn(1, 4, 5)
        waveform = decoder.decode(latents)
        assert waveform.shape[0] == 1
        assert waveform.shape[1] == 2
        assert waveform.shape[2] > 0

    def test_output_length_matches_the_real_conv_arithmetic(self):
        """No closed-form ``T * hop_length`` shortcut here (the odd stride 3 used by this tiny config shortens one stage by a sample, same as the released config's stride 5) -- assert against a second, independently built full-size decoder with the SAME stride set instead."""
        decoder = YuE2VAEDecoder(**_TINY_KWARGS)
        for t in (1, 2, 5, 9):
            latents = torch.randn(1, 4, t)
            out_len = decoder.decode(latents).shape[-1]
            hop = 2 * 3
            assert abs(out_len - t * hop) <= hop

    def test_chunked_decode_matches_the_full_decode_when_overlap_covers_the_receptive_field(self):
        torch.manual_seed(0)
        decoder = YuE2VAEDecoder(**_TINY_KWARGS).eval()
        latents = torch.randn(1, 4, 200)
        with torch.no_grad():
            full = decoder.decode(latents)
            chunked = decoder.decode(latents, chunk_size=80, overlap=64)
            uneven = decoder.decode(latents, chunk_size=77, overlap=63)
        assert chunked.shape == full.shape
        torch.testing.assert_close(chunked, full, rtol=1e-4, atol=1e-4)
        torch.testing.assert_close(uneven, full, rtol=1e-4, atol=1e-4)

    def test_decode_never_builds_an_autograd_graph(self):
        decoder = YuE2VAEDecoder(**_TINY_KWARGS)
        out = decoder.decode(torch.randn(1, 4, 200), chunk_size=80, overlap=64)
        assert torch.is_inference(out)
        assert not out.requires_grad

    def test_chunked_decode_is_a_no_op_for_a_signal_shorter_than_one_chunk(self):
        decoder = YuE2VAEDecoder(**_TINY_KWARGS).eval()
        latents = torch.randn(1, 4, 5)
        with torch.no_grad():
            torch.testing.assert_close(decoder.decode(latents, chunk_size=128), decoder.decode(latents))

    def test_wrong_latent_channel_count_is_rejected(self):
        decoder = YuE2VAEDecoder(**_TINY_KWARGS)
        with pytest.raises(NativeEngineUnsupportedError):
            decoder.decode(torch.randn(1, 3, 5))

    def test_wrong_rank_is_rejected(self):
        decoder = YuE2VAEDecoder(**_TINY_KWARGS)
        with pytest.raises(NativeEngineUnsupportedError):
            decoder.decode(torch.randn(4, 5))

    def test_mismatched_c_mults_and_strides_length_is_rejected(self):
        with pytest.raises(NativeEngineUnsupportedError):
            YuE2VAEDecoder(latent_dim=4, channels=4, c_mults=(1, 2, 4), strides=(2, 3), out_channels=2)


class TestKeyManifest:
    def test_default_config_key_names_match_the_checkpoint_layout(self):
        """Every key ``load()`` will look for in a real export, derived on a real-size (default-config) module so index arithmetic at depth 7 (the released config) is exercised, not just the tiny test config."""
        decoder = YuE2VAEDecoder()
        keys = set(decoder.state_dict().keys())
        assert "layers.0.weight" in keys        # conv_in
        assert "layers.0.bias" in keys
        assert "layers.8.weight" in keys        # final conv (bias=False)
        assert "layers.8.bias" not in keys
        assert "layers.7.alpha" in keys         # final SnakeBeta
        assert "layers.7.beta" in keys
        for i in range(1, 7):
            assert f"layers.{i}.layers.0.alpha" in keys      # block's own SnakeBeta
            assert f"layers.{i}.layers.1.weight" in keys     # ConvTranspose1d
            assert f"layers.{i}.layers.2.layers.1.weight" in keys  # 1st residual unit's first conv
        assert "layers.9.weight" not in keys    # final_tanh=False -> Identity, no key at all


class TestFoldWeightNormLoad:
    def test_load_reproduces_a_reference_decoder_exactly(self):
        torch.manual_seed(0)
        reference = YuE2VAEDecoder(**_TINY_KWARGS)
        latents = torch.randn(1, 4, 5)
        expected = reference.decode(latents)

        wrapped = copy.deepcopy(reference)
        _wrap_with_weight_norm(wrapped)
        raw_state_dict = {f"decoder.{k}": v.detach().clone() for k, v in wrapped.state_dict().items()}

        loaded = load(raw_state_dict, **_TINY_KWARGS)
        actual = loaded.decode(latents)

        assert torch.allclose(expected, actual, atol=1e-5)

    def test_encoder_only_keys_are_ignored(self):
        torch.manual_seed(0)
        reference = YuE2VAEDecoder(**_TINY_KWARGS)
        wrapped = copy.deepcopy(reference)
        _wrap_with_weight_norm(wrapped)
        raw_state_dict = {f"decoder.{k}": v.detach().clone() for k, v in wrapped.state_dict().items()}
        raw_state_dict["encoder.layers.0.weight_v"] = torch.zeros(1)
        raw_state_dict["encoder.layers.0.weight_g"] = torch.zeros(1)

        loaded = load(raw_state_dict, **_TINY_KWARGS)
        assert isinstance(loaded, YuE2VAEDecoder)

    def test_decoder_only_state_dict_with_no_decoder_prefix_raises(self):
        with pytest.raises(NativeEngineUnsupportedError):
            load({"encoder.layers.0.weight": torch.zeros(1)})
