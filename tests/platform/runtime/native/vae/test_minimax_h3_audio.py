"""Tests for the MiniMax-H3 audio VAE (DAC encoder + BigVGAN decoder).

No real weights -- CPU-only, tiny configs. Real checkpoint headers were used
to derive the module's key layout (see ``vae/minimax_h3_audio.py`` module
docstring), not downloaded here.
"""

from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.vae.loader import _VaeSpec
from src.platform.runtime.native.vae.minimax_h3_audio import MiniMaxH3AudioVAE, _SnakeBeta
from vendor.gpl.comfyui.ops import disable_weight_init, manual_cast

_TINY_CONFIG = dict(
    encoder_dim=4,
    encoder_rates=(2, 2),
    latent_dim=8,
    latent_channels=2,
    num_attention_heads=1,
    decoder_dim=8,
    decoder_rates=(2, 2),
    decoder_kernel_sizes=(4, 4),
    resblock_kernel_sizes=(3,),
    resblock_dilation_sizes=((1,),),
    sample_rate=32000,
)


def _build(*, operations=disable_weight_init, config: dict = _TINY_CONFIG) -> MiniMaxH3AudioVAE:
    module = MiniMaxH3AudioVAE.from_config(config, operations)
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
        for name, b in module.named_buffers():
            if b is not None and b.is_floating_point() and "filter" not in name:
                b.zero_()
    return module


class TestMiniMaxH3AudioVAETiny:
    def test_hop_length_is_product_of_encoder_rates(self):
        module = _build()
        assert module.hop_length == math.prod(_TINY_CONFIG["encoder_rates"])

    def test_self_consistent_state_dict_passes_load_integrity(self):
        module = _build()
        sd = module.state_dict()
        module2 = MiniMaxH3AudioVAE.from_config(_TINY_CONFIG, disable_weight_init)
        load_into_module(module2, sd, _VaeSpec(family="vae", variant="minimax_h3_audio"))

    def test_post_load_is_safe_noop(self):
        module = _build()
        module.post_load()  # must not raise

    def test_encode_rejects_non_mono(self):
        module = _build()
        with pytest.raises(NativeEngineUnsupportedError):
            module.encode(torch.randn(1, 2, 37))

    def test_encode_rejects_wrong_ndim(self):
        module = _build()
        with pytest.raises(NativeEngineUnsupportedError):
            module.encode(torch.randn(1, 37))

    def test_encode_pads_to_hop_length_multiple(self):
        module = _build()
        x = torch.randn(1, 1, 37)  # not a multiple of hop_length=4
        with torch.no_grad():
            z = module.encode(x)
        expected_frames = math.ceil(37 / module.hop_length)
        assert z.shape == (1, _TINY_CONFIG["latent_channels"], expected_frames)
        assert torch.isfinite(z).all()

    def test_decode_shape_is_latents_times_hop_length(self):
        module = _build()
        num_frames = 5
        latents = torch.randn(1, _TINY_CONFIG["latent_channels"], num_frames)
        with torch.no_grad():
            wave = module.decode(latents)
        assert wave.shape == (1, 1, num_frames * module.hop_length)
        assert torch.isfinite(wave).all()

    def test_decode_output_is_clamped_to_unit_range(self):
        module = _build()
        with torch.no_grad():
            for p in module.decoder.parameters():
                if p.is_floating_point():
                    p.normal_(std=5.0)  # deliberately large, to actually hit the clamp
        latents = torch.randn(1, _TINY_CONFIG["latent_channels"], 5) * 10
        with torch.no_grad():
            wave = module.decode(latents)
        assert wave.min() >= -1.0
        assert wave.max() <= 1.0

    def test_full_roundtrip_shape_and_finite(self):
        module = _build()
        x = torch.randn(1, 1, 40)
        with torch.no_grad():
            z = module.encode(x)
            wave = module.decode(z)
        assert wave.shape[0] == 1
        assert wave.shape[1] == 1
        assert torch.isfinite(wave).all()

    def test_logs_proj_exists_for_key_parity_but_is_never_invoked(self):
        """MiniMax-H3 always consumes the posterior mean -- logs_proj is a
        real checkpoint weight built for key parity, never called by
        encode()."""
        module = _build()
        sd = module.state_dict()
        assert "logs_proj.weight" in sd
        assert "logs_proj.bias" in sd

    def test_latents_mean_and_std_are_real_buffers(self):
        module = _build()
        sd = module.state_dict()
        assert sd["latents_mean"].shape == (_TINY_CONFIG["latent_channels"],)
        assert sd["latents_std"].shape == (_TINY_CONFIG["latent_channels"],)

    def test_manual_cast_ops_roundtrip_still_loads(self):
        """The real checkpoint is fp32 and selects manual_cast at engine load
        time (storage fp32 != compute bf16) -- confirm the module builds and
        loads under that ops namespace too, not just disable_weight_init."""
        module = _build(operations=manual_cast)
        sd = module.state_dict()
        module2 = MiniMaxH3AudioVAE.from_config(_TINY_CONFIG, manual_cast)
        load_into_module(module2, sd, _VaeSpec(family="vae", variant="minimax_h3_audio"))

    def test_fp32_forced_regardless_of_activation_dtype(self):
        """encode()/decode() force fp32 at their own entry point -- feeding a
        bf16 input must not corrupt/crash the forward, and the output stays
        finite (this is the "activation forces weight cast" contract
        documented in the module docstring)."""
        module = _build()
        x = torch.randn(1, 1, 40).to(torch.bfloat16)
        with torch.no_grad():
            z = module.encode(x)
        assert z.dtype == torch.float32
        with torch.no_grad():
            wave = module.decode(z.to(torch.bfloat16))
        assert wave.dtype == torch.float32
        assert torch.isfinite(wave).all()


class TestWeightNormKeySpelling:
    """The discrepancy documented in the module docstring: the real Comfy-Org
    repack has weight_norm FUSED (remove_weight_norm applied at export), so
    every conv is a plain weight/bias pair -- NOT the weight_g/weight_v
    spelling diffusers' own (unfused) reference would produce."""

    def test_no_weight_g_or_weight_v_keys(self):
        module = _build()
        sd = module.state_dict()
        offenders = [k for k in sd if "weight_g" in k or "weight_v" in k or "parametriz" in k]
        assert offenders == []

    def test_conv_pre_and_conv_post_are_plain_weight_bias(self):
        module = _build()
        sd = module.state_dict()
        assert "decoder.conv_pre.weight" in sd
        assert "decoder.conv_pre.bias" in sd
        # conv_post is bias=False in the real checkpoint (verified via header).
        assert "decoder.conv_post.weight" in sd
        assert "decoder.conv_post.bias" not in sd


class TestSnakeBeta:
    def test_log_space_alpha_behaves_as_exp(self):
        act = _SnakeBeta(1)
        with torch.no_grad():
            act.alpha.fill_(math.log(2.0))
            act.beta.fill_(0.0)
        x = torch.linspace(-3.0, 3.0, 7).view(1, 1, -1)
        with torch.no_grad():
            out = act(x)
        expected = x + (1.0 / (1.0 + 1e-9)) * torch.sin(x * 2.0) ** 2
        assert torch.allclose(out, expected, atol=1e-6)
