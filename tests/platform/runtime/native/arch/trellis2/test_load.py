"""Tests for the TRELLIS.2 Comfy-Org family loaders (``load.py``).

Coverage:
  * key-coverage parity against the real depot files: every parameter the
    production module declares is present in the file's slice and vice versa
    (skipped unless ``POTIONUI_MODEL_TESTS=1`` and the files are on disk)
  * the prefixed read: correct slice, prefix stripped, dtype override, and a
    hard error rather than ``load_torch_file_prefixed``'s whole-file fallback
  * the unfilled-parameter guard that stops a wrong-slot file yielding a
    randomly-initialised model
  * tier selection for the two SLat flows

The parity tests build on the ``meta`` device, so a 4B-parameter module costs
nothing and no tensor is ever read out of the checkpoint — only its header.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch
import torch.nn as nn
from safetensors.torch import save_file

from src.platform.runtime.native.arch.trellis2 import load as trellis2_load
from src.platform.runtime.native.arch.trellis2.config import (
    OCTREE_VAE_DECODER_TORSO_PRODUCTION,
    OctreeVaeDecoderConfig,
    SLatFlowConfig,
    SSFlowConfig,
    SSVAEDecoderConfig,
    SHAPE_SLAT_FLOW_512,
    SHAPE_SLAT_FLOW_1024,
    SS_FLOW_PRODUCTION,
    SS_VAE_DECODER_PRODUCTION,
    TEX_SLAT_FLOW_512,
    TEX_SLAT_FLOW_1024,
)
from src.platform.runtime.native.arch.trellis2.detect import (
    FLOW_PREFIXES,
    SHAPE_DECODER_PREFIX,
    STRUCTURE_DECODER_PREFIX,
    TEXTURE_DECODER_PREFIX,
)
from src.platform.runtime.native.arch.trellis2.load import (
    SHAPE_DECODER_RESOLUTION,
    _fill,
    _read,
    load_shape_slat_flow,
    load_tex_slat_flow,
)
from src.platform.runtime.native.arch.trellis2.octree_vae import (
    FlexiDualGridVaeDecoder,
    SparseUnetVaeDecoder,
)
from src.platform.runtime.native.arch.trellis2.slat_flow import SLatFlowModel
from src.platform.runtime.native.arch.trellis2.ss_flow import SSFlowDiT
from src.platform.runtime.native.arch.trellis2.ss_vae import SSVAEDecoder

_REPO_ROOT = Path(__file__).resolve().parents[6]
_MODELS = _REPO_ROOT / "models"
_BUNDLE_PATH = _MODELS / "diffusion_models" / "trellis_2_bf16.safetensors"
_SHAPE_VAE_PATH = _MODELS / "vae" / "trellis_2_shape_vae_bf16.safetensors"
_TEXTURE_VAE_PATH = _MODELS / "vae" / "trellis_2_texture_vae_bf16.safetensors"


def _production_parameter_names(build) -> set[str]:
    from vendor.gpl.comfyui.ops import disable_weight_init

    with torch.device("meta"):
        module = build(disable_weight_init)
    return {name for name, _ in module.named_parameters()}


_PRODUCTION_COMPONENTS = {
    "structure_flow": (
        _BUNDLE_PATH,
        FLOW_PREFIXES["structure"],
        lambda ops: SSFlowDiT(SS_FLOW_PRODUCTION, ops),
    ),
    "shape_flow_512": (
        _BUNDLE_PATH,
        FLOW_PREFIXES["shape_512"],
        lambda ops: SLatFlowModel(**SHAPE_SLAT_FLOW_512.as_kwargs()),
    ),
    "shape_flow_1024": (
        _BUNDLE_PATH,
        FLOW_PREFIXES["shape_1024"],
        lambda ops: SLatFlowModel(**SHAPE_SLAT_FLOW_1024.as_kwargs()),
    ),
    "texture_flow": (
        _BUNDLE_PATH,
        FLOW_PREFIXES["texture"],
        lambda ops: SLatFlowModel(**TEX_SLAT_FLOW_1024.as_kwargs()),
    ),
    "structure_decoder": (
        _SHAPE_VAE_PATH,
        STRUCTURE_DECODER_PREFIX,
        lambda ops: SSVAEDecoder(SS_VAE_DECODER_PRODUCTION, ops),
    ),
    "shape_decoder": (
        _SHAPE_VAE_PATH,
        SHAPE_DECODER_PREFIX,
        lambda ops: FlexiDualGridVaeDecoder(
            OCTREE_VAE_DECODER_TORSO_PRODUCTION, resolution=SHAPE_DECODER_RESOLUTION
        ),
    ),
    "texture_decoder": (
        _TEXTURE_VAE_PATH,
        TEXTURE_DECODER_PREFIX,
        lambda ops: SparseUnetVaeDecoder(
            OCTREE_VAE_DECODER_TORSO_PRODUCTION, out_channels=6, pred_subdiv=False
        ),
    ),
}


# ---------------------------------------------------------------------------
# Real-checkpoint key coverage
# ---------------------------------------------------------------------------


@pytest.mark.requires_models
@pytest.mark.parametrize("component", sorted(_PRODUCTION_COMPONENTS))
def test_every_production_parameter_is_covered_by_its_checkpoint_slice(component):
    """Both directions. A parameter with no tensor loads as random noise; a
    tensor with no parameter means the config is wrong for these weights."""
    from safetensors import safe_open

    path, prefix, build = _PRODUCTION_COMPONENTS[component]
    if not path.exists():
        pytest.skip(f"needs {path.name} on disk")

    with safe_open(str(path), framework="pt") as f:
        in_file = {k[len(prefix):] for k in f.keys() if k.startswith(prefix)}
    assert in_file, f"no keys under {prefix!r} in {path.name}"

    assert _production_parameter_names(build) == in_file


@pytest.mark.requires_models
@pytest.mark.skipif(not _BUNDLE_PATH.exists(), reason="needs the trellis2 flow bundle on disk")
def test_the_bundle_prefixes_partition_the_whole_file():
    """Nothing in the bundle sits outside the four flow prefixes — a leftover
    key would be a fifth sub-model this port does not know about."""
    from safetensors import safe_open

    with safe_open(str(_BUNDLE_PATH), framework="pt") as f:
        keys = list(f.keys())

    unclaimed = [k for k in keys if not any(k.startswith(p) for p in FLOW_PREFIXES.values())]
    assert unclaimed == []


@pytest.mark.requires_models
@pytest.mark.skipif(not _SHAPE_VAE_PATH.exists(), reason="needs the trellis2 shape VAE on disk")
def test_the_shape_vae_holds_exactly_the_two_decoders():
    from safetensors import safe_open

    with safe_open(str(_SHAPE_VAE_PATH), framework="pt") as f:
        keys = list(f.keys())

    prefixes = (STRUCTURE_DECODER_PREFIX, SHAPE_DECODER_PREFIX)
    assert [k for k in keys if not k.startswith(prefixes)] == []


# ---------------------------------------------------------------------------
# The prefixed read
# ---------------------------------------------------------------------------


@pytest.fixture
def two_component_file(tmp_path) -> Path:
    path = tmp_path / "bundle.safetensors"
    save_file(
        {
            "alpha.w": torch.ones(2, 2, dtype=torch.float32),
            "alpha.b": torch.ones(2, dtype=torch.float32),
            "beta.w": torch.zeros(3, 3, dtype=torch.float32),
        },
        str(path),
    )
    return path


def test_read_returns_only_its_slice_with_the_prefix_stripped(two_component_file):
    assert set(_read(two_component_file, "alpha.", None)) == {"w", "b"}
    assert set(_read(two_component_file, "beta.", None)) == {"w"}


def test_read_applies_a_dtype_override_and_otherwise_keeps_the_files_dtype(two_component_file):
    assert _read(two_component_file, "alpha.", None)["w"].dtype == torch.float32
    assert _read(two_component_file, "alpha.", torch.bfloat16)["w"].dtype == torch.bfloat16


def test_read_refuses_a_prefix_that_matches_nothing_rather_than_reading_it_all(two_component_file):
    """``load_torch_file_prefixed`` falls back to the WHOLE file when no key
    matches, which here would silently feed one component another's weights."""
    with pytest.raises(ValueError, match="no weights under 'gamma.'"):
        _read(two_component_file, "gamma.", None)


def test_read_names_the_top_level_keys_it_did_find(two_component_file):
    with pytest.raises(ValueError, match=r"\['alpha', 'beta'\]"):
        _read(two_component_file, "gamma.", None)


# ---------------------------------------------------------------------------
# The unfilled-parameter guard
# ---------------------------------------------------------------------------


def _tiny_module() -> nn.Module:
    return nn.Sequential(nn.Linear(2, 2), nn.Linear(2, 2))


def test_fill_accepts_a_complete_state_dict_and_returns_it_in_eval_mode():
    module = _tiny_module()
    filled = _fill(_tiny_module(), module.state_dict(), "tiny")

    assert not filled.training
    assert torch.equal(filled[0].weight, module[0].weight)


def test_fill_refuses_a_state_dict_that_leaves_parameters_unfilled():
    module = _tiny_module()
    partial = {k: v for k, v in module.state_dict().items() if k.startswith("0.")}

    with pytest.raises(ValueError, match="2 weights left unfilled"):
        _fill(_tiny_module(), partial, "tiny")


def test_fill_tolerates_a_checkpoint_that_omits_a_non_persistent_buffer():
    class WithBuffer(nn.Module):
        def __init__(self):
            super().__init__()
            self.lin = nn.Linear(2, 2)
            self.register_buffer("cache", torch.zeros(2), persistent=False)

    state = {k: v for k, v in WithBuffer().state_dict().items()}
    assert "cache" not in state
    assert _fill(WithBuffer(), state, "buffered") is not None


def test_fill_calls_post_load_so_derived_buffers_are_rebuilt():
    class NeedsPostLoad(nn.Module):
        def __init__(self):
            super().__init__()
            self.lin = nn.Linear(2, 2)
            self.rebuilt = False

        def post_load(self):
            self.rebuilt = True

    module = _fill(NeedsPostLoad(), NeedsPostLoad().state_dict(), "post-load")
    assert module.rebuilt


# ---------------------------------------------------------------------------
# Tier selection
# ---------------------------------------------------------------------------


def test_the_shape_flow_tiers_read_different_prefixes_and_different_resolutions():
    assert FLOW_PREFIXES["shape_512"] != FLOW_PREFIXES["shape_1024"]
    assert SHAPE_SLAT_FLOW_512.resolution == 32
    assert SHAPE_SLAT_FLOW_1024.resolution == 64


def test_the_texture_flow_tiers_share_one_prefix_and_differ_only_in_resolution():
    assert TEX_SLAT_FLOW_512.resolution == 32
    assert TEX_SLAT_FLOW_1024.resolution == 64
    assert TEX_SLAT_FLOW_512.as_kwargs() | {"resolution": 64} == TEX_SLAT_FLOW_1024.as_kwargs()


def test_the_texture_flow_takes_the_shape_latent_concatenated_onto_its_input():
    assert TEX_SLAT_FLOW_1024.in_channels == SHAPE_SLAT_FLOW_1024.out_channels * 2
    assert TEX_SLAT_FLOW_1024.out_channels == 32


@pytest.mark.parametrize("loader", [load_shape_slat_flow, load_tex_slat_flow])
def test_an_unknown_tier_is_refused_before_any_file_is_opened(loader, tmp_path):
    with pytest.raises(ValueError, match=r"unknown .* flow tier '1536'"):
        loader(tmp_path / "does-not-exist.safetensors", "1536")


# ---------------------------------------------------------------------------
# Round trip: save a tiny checkpoint at each prefix, load it back
# ---------------------------------------------------------------------------

TINY_SS_FLOW = SSFlowConfig(
    resolution=2, in_channels=4, model_channels=8, cond_channels=8,
    out_channels=4, num_blocks=1, num_heads=2, share_mod=True,
    qk_rms_norm=True, qk_rms_norm_cross=True,
)
TINY_SS_VAE = SSVAEDecoderConfig(
    out_channels=1, latent_channels=4, num_res_blocks=1, channels=(8, 4),
)
TINY_SLAT_FLOW = SLatFlowConfig(
    resolution=4, in_channels=4, out_channels=4, model_channels=8,
    cond_channels=8, num_blocks=1, num_heads=2,
)
TINY_OCTREE = OctreeVaeDecoderConfig(model_channels=(16, 8), latent_channels=4, num_blocks=(1, 0))


def _write_at(path: Path, prefix: str, module: nn.Module) -> dict[str, torch.Tensor]:
    """Save ``module``'s tensors under ``prefix``, with distinct finite values.

    ``disable_weight_init`` leaves uninitialised memory, which can hold NaN — and
    a NaN never compares equal to itself, so a round trip over the raw tensors
    would fail even when every byte matched.
    """
    state = {}
    for index, (key, tensor) in enumerate(module.state_dict().items()):
        filled = torch.full_like(tensor, float(index + 1), dtype=torch.float32).to(tensor.dtype)
        state[key] = filled.contiguous()
    save_file({prefix + k: v for k, v in state.items()}, str(path))
    return state


def _assert_round_trip(loaded: nn.Module, saved: dict[str, torch.Tensor]) -> None:
    assert not loaded.training
    got = loaded.state_dict()
    for key, value in saved.items():
        assert torch.equal(got[key], value), key


def test_load_ss_flow_round_trips_through_the_structure_prefix(tmp_path):
    from vendor.gpl.comfyui.ops import disable_weight_init

    path = tmp_path / "bundle.safetensors"
    saved = _write_at(path, FLOW_PREFIXES["structure"], SSFlowDiT(TINY_SS_FLOW, disable_weight_init))

    loaded = trellis2_load.load_ss_flow(path, TINY_SS_FLOW)
    _assert_round_trip(loaded, saved)
    assert not any(t.is_meta for t in loaded.state_dict().values())


def test_load_ss_vae_decoder_round_trips_through_the_struct_dec_prefix(tmp_path):
    from vendor.gpl.comfyui.ops import disable_weight_init

    path = tmp_path / "shape_vae.safetensors"
    saved = _write_at(path, STRUCTURE_DECODER_PREFIX, SSVAEDecoder(TINY_SS_VAE, disable_weight_init))

    _assert_round_trip(trellis2_load.load_ss_vae_decoder(path, TINY_SS_VAE), saved)


@pytest.mark.parametrize("tier", ["512", "1024"])
def test_load_shape_slat_flow_round_trips_through_its_tier_prefix(tmp_path, tier):
    path = tmp_path / "bundle.safetensors"
    saved = _write_at(path, FLOW_PREFIXES[f"shape_{tier}"], SLatFlowModel(**TINY_SLAT_FLOW.as_kwargs()))

    _assert_round_trip(load_shape_slat_flow(path, tier, TINY_SLAT_FLOW), saved)


@pytest.mark.parametrize("tier", ["512", "1024"])
def test_both_texture_tiers_read_the_one_shape2txt_slice(tmp_path, tier):
    path = tmp_path / "bundle.safetensors"
    saved = _write_at(path, FLOW_PREFIXES["texture"], SLatFlowModel(**TINY_SLAT_FLOW.as_kwargs()))

    _assert_round_trip(load_tex_slat_flow(path, tier, TINY_SLAT_FLOW), saved)


def test_load_shape_slat_decoder_round_trips_through_the_shape_dec_prefix(tmp_path):
    path = tmp_path / "shape_vae.safetensors"
    saved = _write_at(
        path, SHAPE_DECODER_PREFIX, FlexiDualGridVaeDecoder(TINY_OCTREE, resolution=8)
    )

    loaded = trellis2_load.load_shape_slat_decoder(path, TINY_OCTREE, resolution=8)
    _assert_round_trip(loaded, saved)


def test_load_tex_slat_decoder_round_trips_through_the_txt_dec_prefix(tmp_path):
    path = tmp_path / "texture_vae.safetensors"
    saved = _write_at(
        path, TEXTURE_DECODER_PREFIX,
        SparseUnetVaeDecoder(TINY_OCTREE, out_channels=6, pred_subdiv=False),
    )

    _assert_round_trip(trellis2_load.load_tex_slat_decoder(path, TINY_OCTREE), saved)


def test_a_bundle_slice_is_refused_by_the_wrong_component(tmp_path):
    """The failure this whole loader is built to catch: pointing a component at
    a prefix carrying some other sub-model's weights."""
    path = tmp_path / "bundle.safetensors"
    from vendor.gpl.comfyui.ops import disable_weight_init

    _write_at(path, FLOW_PREFIXES["structure"], SSFlowDiT(TINY_SS_FLOW, disable_weight_init))

    with pytest.raises(ValueError, match="no weights under 'model.img2shape.'"):
        load_shape_slat_flow(path, "1024", TINY_SLAT_FLOW)


def test_a_dtype_override_reaches_the_loaded_weights(tmp_path):
    path = tmp_path / "texture_vae.safetensors"
    _write_at(
        path, TEXTURE_DECODER_PREFIX,
        SparseUnetVaeDecoder(TINY_OCTREE, out_channels=6, pred_subdiv=False),
    )

    loaded = trellis2_load.load_tex_slat_decoder(path, TINY_OCTREE, dtype=torch.bfloat16)
    assert {t.dtype for t in loaded.state_dict().values()} == {torch.bfloat16}


def test_the_loaders_the_pipe_stage_calls_are_all_exported():
    for name in (
        "load_ss_flow", "load_shape_slat_flow", "load_tex_slat_flow",
        "load_ss_vae_decoder", "load_shape_slat_decoder", "load_tex_slat_decoder",
        "load_dino_conditioner",
    ):
        assert callable(getattr(trellis2_load, name)), name
        assert name in trellis2_load.__all__
