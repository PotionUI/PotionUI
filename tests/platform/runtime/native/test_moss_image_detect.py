from __future__ import annotations

from pathlib import Path

import pytest
import torch

from src.platform.runtime.model_headers.signatures import TRANSFORMER_EXTRACTABLE_FAMILIES, family_from_keys
from src.platform.runtime.native.arch.moss_image.model import MossImage, convert_moss_image_state_dict
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.kernels import pick_operations

_FIXTURES = Path(__file__).parent / "arch" / "fixtures"
_DTYPES = {"BF16": torch.bfloat16, "F32": torch.float32, "I8": torch.int8, "U8": torch.uint8}
_BF16 = "moss_image_sft_bf16.keys.txt"
_INT8 = "moss_image_sft_int8_convrot_attn.keys.txt"
_SIDECAR_SUFFIXES = (".weight_scale", ".comfy_quant", ".comfy_attention.config")


def _header(name):
    rows = {}
    for line in (_FIXTURES / name).read_text().splitlines():
        key, dtype, shape = line.split("\t")
        rows[key] = (dtype, tuple(int(d) for d in shape.split(",")) if shape else ())
    return rows


def _meta_state_dict(name):
    return {k: torch.empty(shape, dtype=_DTYPES[dtype], device="meta") for k, (dtype, shape) in _header(name).items()}


def _build_meta(config):
    with torch.device("meta"):
        return MossImage.from_config(config, pick_operations(torch.bfloat16, torch.bfloat16))


def test_fixtures_are_the_real_headers():
    bf16, int8 = _header(_BF16), _header(_INT8)
    assert len(bf16) == 379
    assert len(int8) == 757
    assert bf16["rope.bands"] == ("F32", (16,))
    assert bf16["layers.0.proj.qkv.weight"] == ("BF16", (9216, 3072))
    assert int8["layers.0.proj.qkv.weight"] == ("I8", (9216, 3072))
    assert int8["layers.0.proj.qkv.weight_scale"] == ("F32", (9216, 1))
    assert "layers.41.comfy_attention.config" in int8
    assert sum(k.endswith(".comfy_attention.config") for k in int8) == 42
    assert {k for k in int8 if not k.endswith(_SIDECAR_SUFFIXES)} == set(bf16)


@pytest.mark.parametrize("name", [_BF16, _INT8])
def test_both_checkpoints_are_detected_with_the_real_shapes(name):
    config = detect_unet_config(_meta_state_dict(name))
    assert config == {
        "image_model": "moss_image", "hidden_size": 3072, "head_dim": 128, "num_heads": 24,
        "ffn_dim": 8192, "depth": 42, "context_in_dim": 4096, "in_channels": 128,
        "freq_dim": 256, "rope_base": 10000.0,
    }


def test_the_spec_matches_and_carries_the_reference_sampling_defaults():
    spec = match_model_spec(detect_unet_config(_meta_state_dict(_BF16)))
    assert (spec.family, spec.variant) == ("moss_image", "moss_image")
    assert spec.sampling_settings == {
        "prediction": "const", "shift": 5.0, "guidance": "cfg", "default_steps": 50,
        "cfg_zero_star": False, "schedule": "moss_grid", "uncond_skip_layers": [22],
    }
    assert spec.latent_format == {"latent_channels": 32, "scale_factor": 1.0, "shift_factor": 0.0}
    assert spec.clip_targets == ["qwen35_9b"]
    assert spec.vae_target == "flux2_ae"
    assert spec.memory_cost_gb == 10.5
    assert spec.resolve_model_class() is MossImage
    assert spec.resolve_state_dict_map() is convert_moss_image_state_dict


@pytest.mark.parametrize("name", [_BF16, _INT8])
def test_meta_module_keys_and_shapes_equal_the_checkpoint_minus_dropped_keys(name):
    sd = _meta_state_dict(name)
    config = detect_unet_config(sd)
    converted = convert_moss_image_state_dict(sd)
    expected = {k: tuple(v.shape) for k, v in converted.items() if not k.endswith((".weight_scale", ".comfy_quant"))}
    built = {k: tuple(v.shape) for k, v in _build_meta(config).state_dict().items()}
    assert built == expected
    assert len(built) == 379 - 1


def test_conversion_drops_exactly_rope_bands_and_the_attention_hints():
    sd = _meta_state_dict(_INT8)
    dropped = set(sd) - set(convert_moss_image_state_dict(sd))
    assert dropped == {"rope.bands"} | {f"layers.{i}.comfy_attention.config" for i in range(42)}


@pytest.mark.parametrize("name", [_BF16, _INT8])
def test_torch_free_dispatch_agrees_with_native_detection(name):
    sd = _meta_state_dict(name)
    match = family_from_keys(set(sd), lambda key: tuple(sd[key].shape))
    assert match.family == "moss_image"
    assert match_model_spec(detect_unet_config(sd)).family == match.family
    assert "moss_image" in TRANSFORMER_EXTRACTABLE_FAMILIES


def test_no_other_family_claims_the_checkpoint():
    sd = _meta_state_dict(_BF16)
    assert detect_unet_config(sd)["image_model"] == "moss_image"
    without = {k: v for k, v in sd.items() if k != "shared_block_modulation.weight"}
    config = detect_unet_config(without)
    assert config is None or config["image_model"] != "moss_image"



@pytest.mark.parametrize("name", [
    "flux2_klein_9b.keys.txt",
    "flux3_image_klein_bf16.keys.txt",
    "krea2_turbo_bf16.keys.txt",
    "qwen_image21.keys.txt",
    "qwen_image_2512.keys.txt",
])
def test_other_families_real_key_lists_are_not_claimed_as_moss_image(name):
    keys = set((_FIXTURES / name).read_text().split())
    assert family_from_keys(keys, lambda key: None).family != "moss_image"
