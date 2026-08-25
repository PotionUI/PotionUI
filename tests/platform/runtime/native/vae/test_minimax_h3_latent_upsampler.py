"""Tests for the MiniMax-H3 3D latent upsampler.

Key parity is checked against the REAL Comfy-Org-style repack header
(``ai/minimax_h3/latent_upscaler_3d_bf16_header.json``) at meta-device scale
(no real weight data needed for a key-set check -- see
``test_minimax_music3_model.py`` for the same pattern in this repo). The
header isn't part of the repo checkout (``ai/`` is gitignored), so these
tests skip rather than fail when it's absent -- construction/detection/
forward coverage below stays tiny-config-only and does not depend on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.vae_detect import (
    detect_minimax_h3_latent_upsampler_config,
)
from src.platform.runtime.native.vae.loader import load_minimax_h3_latent_upsampler
from src.platform.runtime.native.vae.minimax_h3_latent_upsampler import (
    MEAN,
    STD,
    MiniMaxH3LatentUpsampler,
    denormalize_h3_latent,
    normalize_h3_latent,
)
from vendor.gpl.comfyui.ops import disable_weight_init

_REPO_ROOT = Path(__file__).resolve().parents[5]
_HEADER_PATH = _REPO_ROOT / "ai" / "minimax_h3" / "latent_upscaler_3d_bf16_header.json"

# num_groups=32 in GroupNorm -- channels must stay a multiple of 32.
_TINY_CONFIG = {
    "in_channels": 4,
    "channels": 64,
    "num_res_blocks": 2,
    "temporal_every": 2,
    "temporal_kernel": 3,
    "embed_dim": 8,
    "dropout": 0.0,
}


class _Spec:
    def key_is_expected_missing(self, key: str) -> bool:
        return False

    def key_is_expected_unexpected(self, key: str) -> bool:
        return False


def _randomize(module: torch.nn.Module) -> None:
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)


def _load_real_header() -> dict:
    if not _HEADER_PATH.exists():
        pytest.skip(f"{_HEADER_PATH} not present (not part of the repo checkout)")
    with _HEADER_PATH.open() as f:
        header = json.load(f)
    header.pop("__metadata__", None)
    return header


class TestStateDictKeySetMatchesRealHeader:
    """322/322 keys, meta device only -- the default config IS the real
    checkpoint's shape, so no shrinking is needed for a key-set check."""

    def test_exact_key_set(self):
        header = _load_real_header()
        with torch.device("meta"):
            m = MiniMaxH3LatentUpsampler.from_config({}, disable_weight_init)
        assert set(m.state_dict().keys()) == set(header.keys())

    def test_shapes_match_exactly(self):
        header = _load_real_header()
        with torch.device("meta"):
            m = MiniMaxH3LatentUpsampler.from_config({}, disable_weight_init)
        sd = m.state_dict()
        for key, tensor in sd.items():
            assert list(tensor.shape) == header[key]["shape"], key

    def test_bite_check_wrong_temporal_placement_breaks_key_parity(self):
        """Sentinel: if `temporal_every` block insertion used the wrong index
        parity (e.g. odd instead of even), the TemporalBlock/ResBlock split
        across the 18-module stack would land at different indices than the
        real header's -- this must NOT accidentally still match."""
        header = _load_real_header()
        with torch.device("meta"):
            m = MiniMaxH3LatentUpsampler.from_config({"temporal_every": 3}, disable_weight_init)
        assert set(m.state_dict().keys()) != set(header.keys())


class TestConstructionAndLoadIntegrity:
    def test_default_config_builds(self):
        MiniMaxH3LatentUpsampler.from_config({}, disable_weight_init)

    def test_self_consistent_state_dict_passes_load_integrity(self):
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        _randomize(module)
        sd = module.state_dict()

        module2 = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        load_into_module(module2, sd, _Spec())

    def test_post_load_is_safe_noop(self):
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        module.post_load()


class TestTinyForward:
    def _latents(self, t: int = 3, h: int = 8, w: int = 8) -> torch.Tensor:
        return torch.randn(1, _TINY_CONFIG["in_channels"], t, h, w)

    def test_resizes_to_target_size(self):
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        _randomize(module)
        with torch.no_grad():
            out = module(self._latents(), scale=1.5, target_size=(3, 12, 16))
        assert out.shape == (1, _TINY_CONFIG["in_channels"], 3, 12, 16)
        assert torch.isfinite(out).all()

    def test_temporal_extent_can_change_too(self):
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        _randomize(module)
        with torch.no_grad():
            out = module(self._latents(t=3), scale=2.0, target_size=(5, 8, 8))
        assert out.shape == (1, _TINY_CONFIG["in_channels"], 5, 8, 8)

    def test_scale_none_path(self):
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        _randomize(module)
        with torch.no_grad():
            out = module(self._latents(), scale=None, target_size=(3, 16, 16))
        assert out.shape == (1, _TINY_CONFIG["in_channels"], 3, 16, 16)
        assert torch.isfinite(out).all()

    def test_matching_target_size_is_a_cheap_noop(self):
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        _randomize(module)
        latent = self._latents(t=3, h=8, w=8)
        with torch.no_grad():
            out = module(latent, scale=1.0, target_size=(3, 8, 8))
        assert out is latent


class TestAdaGNScaleShiftOrder:
    def test_scale_and_shift_are_not_swapped(self):
        """`emb_out.chunk(2, dim=1)` must land as `(scale, shift)`, not the
        reverse -- both orderings produce the SAME shapes and both pass every
        other test here, so this compares the real forward's output against
        an independently swapped computation rather than re-deriving the
        same formula forward() itself uses."""
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        _randomize(module)
        block = module.in_blocks[0]
        x = torch.randn(1, _TINY_CONFIG["channels"], 2, 3, 3)
        emb = torch.randn(1, _TINY_CONFIG["embed_dim"])

        with torch.no_grad():
            actual = block(x, emb)

            h = block.in_layers(x)
            emb_out = block.emb_layers(emb).to(h.dtype)[:, :, None, None, None]
            scale, shift = emb_out.chunk(2, dim=1)
            correct = x + block.out_layers(block.out_norm(h) * (1 + scale) + shift)
            swapped = x + block.out_layers(block.out_norm(h) * (1 + shift) + scale)

        assert torch.allclose(actual, correct, atol=1e-6)
        assert not torch.allclose(actual, swapped, atol=1e-4)


class TestDetectMinimaxH3LatentUpsamplerConfig:
    def test_round_trips_against_a_built_module(self):
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        sd = module.state_dict()
        config = detect_minimax_h3_latent_upsampler_config(sd)
        assert config is not None
        assert config["in_channels"] == _TINY_CONFIG["in_channels"]
        assert config["channels"] == _TINY_CONFIG["channels"]
        assert config["num_res_blocks"] == _TINY_CONFIG["num_res_blocks"]
        assert config["embed_dim"] == _TINY_CONFIG["embed_dim"]
        assert config["temporal_kernel"] == _TINY_CONFIG["temporal_kernel"]

    def test_missing_signature_keys_return_none(self):
        assert detect_minimax_h3_latent_upsampler_config({}) is None

    def test_4d_conv_in_is_not_mistaken_for_this_family(self):
        """A Flux-style 2D AE's `conv_in` sits at a DIFFERENT key too, but this
        guards the shape branch specifically: a 4D `conv_in.weight` under this
        family's own key name must not false-positive."""
        sd = {
            "conv_in.weight": torch.zeros(8, 4, 3, 3),
            "embed.0.weight": torch.zeros(8, 1),
            "norm_out.weight": torch.zeros(8),
        }
        assert detect_minimax_h3_latent_upsampler_config(sd) is None


class TestLoadMinimaxH3LatentUpsampler:
    def test_round_trip(self):
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        _randomize(module)
        sd = module.state_dict()

        loaded = load_minimax_h3_latent_upsampler(
            "fake-minimax-h3-latent-upscaler.safetensors",
            disable_weight_init, sd=sd, metadata={},
        )
        assert loaded.channels == _TINY_CONFIG["channels"]
        assert loaded.num_res_blocks == _TINY_CONFIG["num_res_blocks"]

        latent = torch.randn(1, _TINY_CONFIG["in_channels"], 3, 4, 4)
        with torch.no_grad():
            out = loaded(latent, scale=1.5, target_size=(3, 6, 8))
        assert out.shape == (1, _TINY_CONFIG["in_channels"], 3, 6, 8)
        assert torch.isfinite(out).all()

    def test_not_this_family_raises(self):
        from src.platform.runtime.native.errors import NativeEngineUnsupportedError

        with pytest.raises(NativeEngineUnsupportedError, match="MiniMax-H3 latent upsampler"):
            load_minimax_h3_latent_upsampler("fake.safetensors", disable_weight_init, sd={}, metadata={})

    def test_reads_file_when_sd_not_preloaded(self, tmp_path, monkeypatch):
        module = MiniMaxH3LatentUpsampler.from_config(_TINY_CONFIG, disable_weight_init)
        sd = module.state_dict()

        def _fake_load_torch_file(path, device="cpu"):
            return sd, {}

        monkeypatch.setattr(
            "src.platform.runtime.native.vae.loader.load_torch_file", _fake_load_torch_file,
        )
        loaded = load_minimax_h3_latent_upsampler(tmp_path / "fake.safetensors", disable_weight_init)
        assert loaded.channels == _TINY_CONFIG["channels"]


class TestNormalizeDenormalizeRoundTrip:
    def test_round_trip(self):
        x = torch.randn(1, len(MEAN), 2, 3, 3)
        normalized = normalize_h3_latent(x)
        recovered = denormalize_h3_latent(normalized)
        assert torch.allclose(recovered, x, atol=1e-5)

    def test_normalize_actually_changes_the_values(self):
        """A no-op normalize (e.g. mean/std applied to the wrong axis) would
        pass the round-trip test above too -- this pins that it's not one."""
        x = torch.randn(1, len(MEAN), 2, 3, 3)
        normalized = normalize_h3_latent(x)
        assert not torch.allclose(normalized, x)

    def test_mean_and_std_have_24_channels(self):
        assert len(MEAN) == 24
        assert len(STD) == 24
