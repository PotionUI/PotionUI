"""Tests for the MiniMax-Music3 DAV vocoder (decode-only DAC decoder).

No real weights -- CPU-only, tiny configs. Real checkpoint header
(``ai/minimax_music3/minimax_music3_dav_header.json``) was used to derive the
module's key layout and the weight_g/weight_v fold (see
``vae/minimax_music3_dav.py`` module docstring), not downloaded here.
"""

from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.vae_detect import detect_minimax_music3_dav_config
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.vae.loader import _VaeSpec
from src.platform.runtime.native.vae.minimax_music3_dav import MiniMaxMusic3DAV, fold_weight_norm_conv
from vendor.gpl.comfyui.ops import disable_weight_init, manual_cast

_TINY_CONFIG = dict(
    latent_channels=4,
    decoder_input_dim=4,
    decoder_hidden_dim=16,
    # 4 blocks -- matches the real repack's fixed geometry (only one released
    # variant), which the detection signature below relies on
    # (`decoder.model.6` only exists with exactly 4 upsample blocks).
    upsampling_ratios=(2, 2, 2, 2),
    sample_rate=44100,
    tile_latents=6,
    tile_overlap_latents=2,
)


def _build(*, operations=disable_weight_init, config: dict = _TINY_CONFIG) -> MiniMaxMusic3DAV:
    module = MiniMaxMusic3DAV.from_config(config, operations)
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
    return module


class TestMiniMaxMusic3DAVTiny:
    def test_hop_length_is_product_of_upsampling_ratios(self):
        module = _build()
        assert module.hop_length == math.prod(_TINY_CONFIG["upsampling_ratios"])

    def test_self_consistent_state_dict_passes_load_integrity(self):
        module = _build()
        sd = module.state_dict()
        module2 = MiniMaxMusic3DAV.from_config(_TINY_CONFIG, disable_weight_init)
        load_into_module(module2, sd, _VaeSpec(family="vae", variant="minimax_music3_dav"))

    def test_post_load_is_safe_noop(self):
        module = _build()
        module.post_load()  # must not raise

    def test_decode_rejects_wrong_channel_count(self):
        module = _build()
        with pytest.raises(NativeEngineUnsupportedError):
            module.decode(torch.randn(1, _TINY_CONFIG["latent_channels"] + 1, 5))

    def test_decode_rejects_wrong_ndim(self):
        module = _build()
        with pytest.raises(NativeEngineUnsupportedError):
            module.decode(torch.randn(_TINY_CONFIG["latent_channels"], 5))

    def test_odd_latent_channels_rejected_at_construction(self):
        with pytest.raises(NativeEngineUnsupportedError):
            MiniMaxMusic3DAV.from_config({**_TINY_CONFIG, "latent_channels": 5}, disable_weight_init)

    def test_decode_shape_is_stereo_times_hop_length(self):
        module = _build()
        module.use_tiling = False
        num_frames = 5
        latents = torch.randn(2, _TINY_CONFIG["latent_channels"], num_frames)
        with torch.no_grad():
            wave = module.decode(latents)
        assert wave.shape == (2, 2, num_frames * module.hop_length)
        assert torch.isfinite(wave).all()

    def test_decode_output_is_clamped_by_tanh(self):
        module = _build()
        module.use_tiling = False
        with torch.no_grad():
            for p in module.decoder.parameters():
                if p.is_floating_point():
                    p.normal_(std=5.0)  # deliberately large, so tanh's own saturation is exercised
        latents = torch.randn(1, _TINY_CONFIG["latent_channels"], 5) * 10
        with torch.no_grad():
            wave = module.decode(latents)
        assert wave.min() >= -1.0
        assert wave.max() <= 1.0

    def test_manual_cast_ops_roundtrip_still_loads(self):
        module = _build(operations=manual_cast)
        sd = module.state_dict()
        module2 = MiniMaxMusic3DAV.from_config(_TINY_CONFIG, manual_cast)
        load_into_module(module2, sd, _VaeSpec(family="vae", variant="minimax_music3_dav"))

    def test_fp32_forced_regardless_of_activation_dtype(self):
        module = _build()
        module.use_tiling = False
        latents = torch.randn(1, _TINY_CONFIG["latent_channels"], 5).to(torch.bfloat16)
        with torch.no_grad():
            wave = module.decode(latents)
        assert wave.dtype == torch.float32
        assert torch.isfinite(wave).all()


class TestNoWeightNormInTheModuleItself:
    """The module is built plain-conv (fold happens in the loader, not here --
    see module docstring). This is the inverse discrepancy from the H3 audio
    sibling: THAT repack ships plain weights already; THIS repack ships
    weight_g/weight_v and gets folded at load."""

    def test_no_weight_g_or_weight_v_keys(self):
        module = _build()
        sd = module.state_dict()
        offenders = [k for k in sd if "weight_g" in k or "weight_v" in k or "parametriz" in k]
        assert offenders == []

    def test_dec_in_proj_and_conv_out_are_plain_weight_bias(self):
        module = _build()
        sd = module.state_dict()
        assert "dec_in_proj.weight" in sd
        assert "dec_in_proj.bias" in sd
        assert "decoder.model.0.weight" in sd  # conv_in
        assert "decoder.model.0.bias" in sd


class TestWeightNormFold:
    """`fold_weight_norm_conv` must compute exactly what
    `torch.nn.utils.parametrize.remove_weight_norm` computes:
    `weight = weight_g * weight_v / ||weight_v||_{dim=(1,2)}`."""

    def test_folds_to_the_reference_formula(self):
        torch.manual_seed(0)
        v = torch.randn(3, 4, 5)  # deliberately non-unit-norm, distinct per out-channel
        g = torch.rand(3, 1, 1) * 2.0 + 0.1
        sd = {
            "some.module.weight_v": v,
            "some.module.weight_g": g,
            "some.module.bias": torch.zeros(3),
            "dec_in_proj.weight": torch.randn(4, 2, 1),  # no _g/_v pair -- must pass through
            "dec_in_proj.bias": torch.zeros(4),
        }
        folded = fold_weight_norm_conv(sd)

        expected = g * v / v.norm(dim=(1, 2), keepdim=True)
        assert torch.allclose(folded["some.module.weight"], expected, atol=1e-7)
        assert "some.module.weight_v" not in folded
        assert "some.module.weight_g" not in folded
        assert torch.equal(folded["some.module.bias"], sd["some.module.bias"])
        assert torch.equal(folded["dec_in_proj.weight"], sd["dec_in_proj.weight"])

    def test_unit_norm_weight_v_makes_folded_weight_equal_g_times_v(self):
        """Bite-check anchor: with weight_v already unit-norm along
        dim=(1,2), dividing by the norm is a no-op, so folded == g * v
        exactly -- reducing over the WRONG dims would break this equality
        (the norm would no longer be 1) and the test fails."""
        v_raw = torch.randn(3, 4, 5)
        v = v_raw / v_raw.norm(dim=(1, 2), keepdim=True)
        g = torch.tensor([1.5, 2.0, 0.5]).view(3, 1, 1)
        sd = {"m.weight_v": v, "m.weight_g": g, "m.bias": torch.zeros(3)}
        folded = fold_weight_norm_conv(sd)
        assert torch.allclose(folded["m.weight"], g * v, atol=1e-6)


class TestDetectionOnRealShapedStateDict:
    def test_detects_config_matching_the_built_module(self):
        module = _build()
        sd = module.state_dict()
        # Detection reads the RAW (un-folded) layout -- convert this tiny
        # module's plain weights into the weight_g/weight_v spelling first.
        raw = {}
        for key, tensor in sd.items():
            if key.endswith(".weight") and key != "dec_in_proj.weight":
                base = key[: -len(".weight")]
                raw[base + ".weight_v"] = tensor
                raw[base + ".weight_g"] = tensor.norm(dim=(1, 2), keepdim=True)
            else:
                raw[key] = tensor
        config = detect_minimax_music3_dav_config(raw)
        assert config is not None
        assert config["latent_channels"] == _TINY_CONFIG["latent_channels"]
        assert config["decoder_input_dim"] == _TINY_CONFIG["decoder_input_dim"]
        assert config["decoder_hidden_dim"] == _TINY_CONFIG["decoder_hidden_dim"]

    def test_returns_none_without_the_signature_keys(self):
        assert detect_minimax_music3_dav_config({"unrelated.weight": torch.zeros(1)}) is None

    def test_returns_none_for_a_different_audio_vae_signature(self):
        """Bite-check: a sibling family's real key shape (H3 audio's
        attribute-named decoder) must NOT match this detector."""
        sd = {
            "dec_in_proj.weight": torch.zeros(4, 2, 1),
            "decoder.conv_pre.weight": torch.zeros(4, 4, 7),  # H3-shaped, not decoder.model.N
        }
        assert detect_minimax_music3_dav_config(sd) is None


class TestTiledDecodeMatchesWhole:
    """Latent-domain chunking with overlap-context-then-discard (see module
    docstring "Chunked decode") must reproduce the whole-decode output away
    from the internal chunk boundaries, at fp32 tolerance."""

    def test_tiled_and_whole_decode_agree(self):
        """With the overlap context kept (not just the kept span), tiled and
        whole decode agree to float32 precision everywhere -- no boundary
        masking needed. See the bite-check below for what breaks this."""
        torch.manual_seed(0)
        module = _build()
        torch.manual_seed(1)
        latents = torch.randn(1, _TINY_CONFIG["latent_channels"], 20)

        module.use_tiling = False
        with torch.no_grad():
            whole = module.decode(latents)

        module.use_tiling = True
        module.tile_latents = 6
        module.tile_overlap_latents = 2
        with torch.no_grad():
            tiled = module.decode(latents)

        assert tiled.shape == whole.shape
        assert torch.allclose(tiled, whole, atol=1e-5)

    def test_bite_zero_overlap_measurably_diverges_from_whole(self):
        """Confirms the equality above is actually exercising the overlap
        context, not trivially true for any tiling: dropping the context
        (`tile_overlap_latents=0`, keeping the exact tile boundaries) must
        widen the gap from `whole` by orders of magnitude."""
        torch.manual_seed(0)
        module = _build()
        torch.manual_seed(1)
        latents = torch.randn(1, _TINY_CONFIG["latent_channels"], 20)

        module.use_tiling = False
        with torch.no_grad():
            whole = module.decode(latents)

        module.use_tiling = True
        module.tile_latents = 6
        module.tile_overlap_latents = 0
        with torch.no_grad():
            tiled = module.decode(latents)

        assert (tiled - whole).abs().max().item() > 1e-3

    def test_tiling_is_default_on_above_the_threshold(self):
        module = _build()
        assert module.use_tiling is True
        assert module.tile_latents == _TINY_CONFIG["tile_latents"]

    def test_bite_disabling_tiling_changes_nothing_below_threshold(self):
        """Below `tile_latents`, tiled and whole decode take the exact same
        code path (`total <= tile_latents` short-circuits to `_decode_core`
        either way) -- so toggling `use_tiling` must be a no-op here."""
        module = _build()
        latents = torch.randn(1, _TINY_CONFIG["latent_channels"], 4)  # < tile_latents=6
        module.use_tiling = True
        with torch.no_grad():
            a = module.decode(latents)
        module.use_tiling = False
        with torch.no_grad():
            b = module.decode(latents)
        assert torch.equal(a, b)
