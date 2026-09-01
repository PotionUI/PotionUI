"""``detect_unet_config`` on the Comfy-Org TRELLIS.2 unified flow file.

The bundle is the one file in the native depot that holds four DiTs, so the
detector must (a) recognise it, (b) report each sub-model's shape-derived
config, and (c) not claim a partial file. The single-model load path must then
refuse it by name rather than building one of the four and filling it with the
other three's weights.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.trellis2.config import (
    SHAPE_SLAT_FLOW_512,
    SHAPE_SLAT_FLOW_1024,
    SS_FLOW_PRODUCTION,
    TEX_SLAT_FLOW_1024,
)
from src.platform.runtime.native.arch.trellis2.detect import FLOW_PREFIXES
from src.platform.runtime.native.detect.unet_detect import detect_unet_config

_PRODUCTION = {
    "structure": SS_FLOW_PRODUCTION,
    "shape_512": SHAPE_SLAT_FLOW_512,
    "shape_1024": SHAPE_SLAT_FLOW_1024,
    "texture": TEX_SLAT_FLOW_1024,
}


def _sub_model_keys(prefix: str, config) -> dict[str, torch.Tensor]:
    """The tensors ``_detect_trellis2`` reads, at their production shapes.

    Shapes only — every value is empty. Enough to prove the detector reads the
    right axis of the right tensor, which is the whole job.
    """
    mc, heads = config.model_channels, config.num_heads
    head_dim = mc // heads
    return {
        f"{prefix}input_layer.weight": torch.empty(mc, config.in_channels),
        f"{prefix}out_layer.weight": torch.empty(config.out_channels, mc),
        f"{prefix}blocks.0.cross_attn.to_kv.weight": torch.empty(2 * mc, config.cond_channels),
        f"{prefix}blocks.0.self_attn.q_rms_norm.gamma": torch.empty(heads, head_dim),
        **{
            f"{prefix}blocks.{i}.modulation": torch.empty(6 * mc)
            for i in range(config.num_blocks)
        },
    }


@pytest.fixture
def bundle() -> dict[str, torch.Tensor]:
    sd: dict[str, torch.Tensor] = {}
    for name, prefix in FLOW_PREFIXES.items():
        sd.update(_sub_model_keys(prefix, _PRODUCTION[name]))
    return sd


def test_the_bundle_is_detected_as_trellis2(bundle):
    assert detect_unet_config(bundle)["image_model"] == "trellis2"


@pytest.mark.parametrize("name", sorted(_PRODUCTION))
def test_each_sub_models_config_is_read_off_its_own_prefix(bundle, name):
    expected = _PRODUCTION[name]
    got = detect_unet_config(bundle)[name]

    assert got["model_channels"] == expected.model_channels
    assert got["in_channels"] == expected.in_channels
    assert got["out_channels"] == expected.out_channels
    assert got["cond_channels"] == expected.cond_channels
    assert got["num_blocks"] == expected.num_blocks
    assert got["num_heads"] == expected.num_heads


def test_the_texture_flow_is_distinguished_by_its_wider_input(bundle):
    config = detect_unet_config(bundle)
    assert config["texture"]["in_channels"] == 64
    assert config["shape_1024"]["in_channels"] == 32


@pytest.mark.parametrize("dropped", sorted(FLOW_PREFIXES))
def test_a_bundle_missing_any_flow_is_not_claimed(bundle, dropped):
    prefix = FLOW_PREFIXES[dropped]
    partial = {k: v for k, v in bundle.items() if not k.startswith(prefix)}
    assert detect_unet_config(partial) is None


def test_another_familys_checkpoint_is_untouched_by_the_new_branch():
    """The trellis2 check runs first, so a false positive there would shadow
    every other family."""
    flux = {
        "double_blocks.0.img_attn.norm.key_norm.scale": torch.empty(128),
        "img_in.weight": torch.empty(3072, 64),
    }
    detected = detect_unet_config(flux)
    assert detected is None or detected["image_model"] != "trellis2"


def test_the_single_model_load_path_refuses_the_bundle_by_name(bundle, tmp_path, monkeypatch):
    from safetensors.torch import save_file

    from src.platform.runtime.native import engine
    from src.platform.runtime.native.errors import NativeEngineUnsupportedError

    path = tmp_path / "trellis_2_bf16.safetensors"
    save_file({k: torch.zeros_like(v) for k, v in bundle.items()}, str(path))

    with pytest.raises(NativeEngineUnsupportedError, match="TRELLIS.2 flow bundle"):
        engine.NativeEngineLoader()._load_dit(path)
