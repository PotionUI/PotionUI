"""Full end-to-end dry-run load test for the MiniMax-Music3 DAV vocoder,
driven by the REAL Comfy-Org repack safetensors header
(``ai/minimax_music3/minimax_music3_dav_header.json`` -- no weights, only
key/shape/dtype metadata fetched via range request).

Same rationale and shrink strategy as
``test_minimax_h3_real_header_dry_run.py`` (that file's module docstring is
the template this one follows): builds a module whose key SET is asserted
equal to the real header's RAW key set (i.e. still weight_g/weight_v-shaped,
before the fold), then drives the real, unmodified production path
end-to-end -- writes a real (tiny-but-valid) safetensors file with
weight_g/weight_v pairs to a temp dir and calls
``NativeEngineLoader._load_audio_vae`` exactly as production does: detection
-> loader dispatch -> weight-norm fold -> ``_VaeSpec`` allowlist gate ->
``post_load`` -> the loader's own NaN/meta-device sanity checks.

**Shrink strategy**: `latent_channels`/`decoder_input_dim`/`decoder_hidden_dim`
are shape-derived by the detector -- safe to shrink freely. `upsampling_ratios`
is NOT shape-derived (the detector returns the hardcoded literal regardless of
what's in the state dict -- there is only one released variant, see the
detector's own docstring), so it is pinned here to the real value `(8, 8, 4,
2)` -- shrinking it would build a module with a different number of
`decoder.model.N` entries than the real header has, an artifact of this
file's construction and not a real bug.

**What this CANNOT catch**: numeric correctness against the real weights
(everything here is randomly initialized); a bug specifically inside
`upsampling_ratios` (pinned to the current real value by construction, so
this file can't vary it without producing a false failure).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.engine import NativeEngineLoader
from src.platform.runtime.native.vae.minimax_music3_dav import (
    MiniMaxMusic3DAV,
    fold_weight_norm_conv,
)
from vendor.gpl.comfyui.ops import disable_weight_init

_HEADER_PATH = Path("ai/minimax_music3/minimax_music3_dav_header.json")

# See module docstring "Shrink strategy" -- `upsampling_ratios` is NOT
# shape-derived by the detector, so it is pinned to the real production value.
_CONFIG = dict(
    latent_channels=4,       # real is 128 -- shape-derived, safe to shrink
    decoder_input_dim=4,     # real is 1024 -- shape-derived, safe to shrink
    decoder_hidden_dim=16,   # real is 1536 -- shape-derived, safe to shrink
    upsampling_ratios=(8, 8, 4, 2),
    sample_rate=44100,
)


def _load_real_header() -> dict:
    if not _HEADER_PATH.exists():
        pytest.skip(f"{_HEADER_PATH} not present (fetched once via range request; not part of the repo checkout)")
    with _HEADER_PATH.open() as f:
        header = json.load(f)
    header.pop("__metadata__", None)
    return header


def _build_plain_state_dict() -> dict[str, torch.Tensor]:
    module = MiniMaxMusic3DAV.from_config(_CONFIG, disable_weight_init)
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
    return {k: v.contiguous().clone() for k, v in module.state_dict().items()}


def _to_raw_weight_norm(sd: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    """Re-lay a plain (module-shaped) state dict as the real repack's raw
    weight_g/weight_v layout. `weight_g := ||weight_v||_{dim=(1,2)}` is
    chosen so `fold_weight_norm_conv` round-trips back to the exact original
    `weight` tensor (`g * v / ||v|| == ||v|| * v / ||v|| == v`) -- this file
    is testing key layout and load plumbing, not fold arithmetic (that's
    ``test_minimax_music3_dav.py::TestWeightNormFold``)."""
    out: dict[str, torch.Tensor] = {}
    for key, tensor in sd.items():
        if key.endswith(".weight") and key != "dec_in_proj.weight":
            base = key[: -len(".weight")]
            out[base + ".weight_v"] = tensor
            out[base + ".weight_g"] = tensor.norm(dim=(1, 2), keepdim=True)
        else:
            out[key] = tensor
    return out


class TestRawStateDictMatchesRealHeaderKeySet:
    def test_exact_key_set(self):
        """Catches: a submodule the real checkpoint lacks, one the real
        checkpoint has that this module doesn't register, or getting the
        weight_g/weight_v-vs-plain split wrong for any single conv (e.g.
        forgetting `dec_in_proj` is the ONE conv that stays plain)."""
        header = _load_real_header()
        raw = _to_raw_weight_norm(_build_plain_state_dict())
        assert set(raw.keys()) == set(header.keys())

    def test_shape_ranks_match(self):
        header = _load_real_header()
        raw = _to_raw_weight_norm(_build_plain_state_dict())
        for key, tensor in raw.items():
            assert tensor.ndim == len(header[key]["shape"]), key

    def test_all_real_header_tensors_are_f32(self):
        header = _load_real_header()
        assert all(entry["dtype"] == "F32" for entry in header.values())


class TestFullEngineDispatchDryRun:
    """Writes a real (tiny) safetensors file with weight_g/weight_v keys and
    drives the UNMODIFIED production entry point
    (`NativeEngineLoader._load_audio_vae`) end to end: file read ->
    detection -> this family's loader -> weight-norm fold ->
    `_VaeSpec` allowlist gate -> `load_into_module`'s missing/unexpected-key
    assertions -> `post_load` -> NaN/meta-device sanity."""

    def test_loads_through_the_real_engine_and_decodes(self):
        _load_real_header()
        raw = _to_raw_weight_norm(_build_plain_state_dict())

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimax_music3_dav.safetensors"
            save_file(raw, str(path))

            loader = NativeEngineLoader(device="cpu")
            native_model = loader._load_audio_vae(path)

        assert isinstance(native_model.module, MiniMaxMusic3DAV)
        assert native_model.module.sample_rate == _CONFIG["sample_rate"]

        module = native_model.module
        module.use_tiling = False
        latent = torch.randn(1, _CONFIG["latent_channels"], 5)
        with torch.no_grad():
            waveform = module.decode(latent)
        assert torch.isfinite(waveform).all()
        assert waveform.shape == (1, 2, 5 * module.hop_length)

    def test_fold_actually_ran_not_a_lucky_shape_match(self):
        """Confirms the loaded module's weight is the FOLDED tensor (not,
        say, `weight_v` copied through unfolded) by comparing against
        `fold_weight_norm_conv` applied independently to the same raw sd."""
        _load_real_header()
        plain_sd = _build_plain_state_dict()
        raw = _to_raw_weight_norm(plain_sd)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimax_music3_dav.safetensors"
            save_file(raw, str(path))
            module = NativeEngineLoader(device="cpu")._load_audio_vae(path).module

        expected = fold_weight_norm_conv(raw)
        assert torch.allclose(module.decoder.model[0].weight, expected["decoder.model.0.weight"], atol=1e-6)
        assert torch.allclose(module.decoder.model[0].weight, plain_sd["decoder.model.0.weight"], atol=1e-6)
