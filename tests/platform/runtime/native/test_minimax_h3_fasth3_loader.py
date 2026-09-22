"""Full-load dry run for FastVideo FastH3 (8-step DMD2-distilled MiniMax-H3)
through the REAL public loader path, plus meta-device key-set parity against
the real FastH3 safetensors headers.

Mirrors ``test_minimax_h3_loader.py``'s shrink strategy (attention_head_dim
kept at the REAL 128 -- the rotary floor -- block/refiner counts kept at the
real 50/2 so the KEY SET is exact) but adds the one structural difference
this checkpoint family introduces: ``blocks.*.attn.to_gate_compress``, VSA's
coarse-attention gate (see ``arch/minimax_h3/model.py``'s ``MiniMaxH3Attention``
docstring). ``hidden_size``/``ffn_dim`` are shrunk to 256 (not the generic
192) so every quantised Linear's in-features stays a whole multiple of the
real checkpoint's own ConvRot group size (256) -- shrinking further would
require a group size the real file never uses.

Two checkpoint shapes are driven, both released for FastH3: the int8_tensorwise
+ ConvRot 8-bit repack (``comfy_quant`` descriptors built byte-exact to the
canonical 72-byte form, same construction ``test_int8_convrot.py`` uses) and
the bf16 repack. Both are `pruned`-shape (``adaln_t_table``, no
``time_embedder``) like the base fl2va/ref2va pruned checkpoint.

No forward pass is run here (all-zero placeholder weights, same as
``test_minimax_h3_loader.py`` -- this file proves load-plumbing integrity,
not numerics; that's ``test_minimax_h3_gate_compress.py``'s job for the gate
module itself, and ``test_int8_convrot.py``'s for ConvRot dequantisation).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel
from vendor.gpl.comfyui.ops import QUANT_FP8_SCALED, pick_operations

from ._quant_layouts import convrot_descriptor, descriptor_blob

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HEADER_DIR = _REPO_ROOT / "ai" / "minimax_h3"

HIDDEN = 256             # multiple of the real ConvRot group size (256)
HEADS = 2
HEAD_DIM = 128            # real value -- rotary floor (2*3*16=96 <= head_dim)
INNER = HEADS * HEAD_DIM  # 256 -- also a whole group
FFN = 256                 # multiple of 256, unlike the generic loader test's 384
IN_CHANNELS = 24
VIDEO_PATCH_DIM = IN_CHANNELS * 4
AUDIO_IN_CHANNELS = 32
TEXT_DIM = 5120
ROPE_FREQ_DIM = 16
NUM_LAYERS = 50           # real value -- key set must be exact
NUM_REFINER_LAYERS = 2    # real value
PRUNED_TIME_EMBED_DIM = 8       # real value
PRUNED_ADALN_GRID = 1025        # real value
CONVROT_GROUPSIZE = 256         # real value -- see video VAE's own int8 repack

REAL_CONFIG_FASTH3 = {
    "image_model": "minimax_h3", "hidden_size": 5376, "num_layers": 50,
    "num_refiner_layers": 2, "num_attention_heads": 56, "attention_head_dim": 128,
    "ffn_dim": 14336, "in_channels": 24, "audio_in_channels": 32,
    "patch_size": (1, 2, 2), "text_dim": 5120, "rope_freq_dim": 16,
    "pruned": True, "time_embed_dim": 8, "adaln_curve_grid": 1025,
    "gate_compress": True,
}


def _load_real_header(name: str) -> dict:
    path = _HEADER_DIR / name
    if not path.exists():
        pytest.skip(f"{path} not present (fetched once via range request; not part of the repo checkout)")
    with path.open() as f:
        header = json.load(f)
    header.pop("__metadata__", None)
    return header


# --- state-dict builders ------------------------------------------------------

def _int8_linear(sd: dict, prefix: str, out_f: int, in_f: int) -> None:
    sd[f"{prefix}.weight"] = torch.zeros(out_f, in_f, dtype=torch.int8)
    sd[f"{prefix}.weight_scale"] = torch.ones(out_f, 1, dtype=torch.float32)
    sd[f"{prefix}.comfy_quant"] = descriptor_blob(convrot_descriptor(CONVROT_GROUPSIZE))


def _int8_gated_block(sd: dict, prefix: str) -> None:
    _int8_linear(sd, f"{prefix}attn.qkv_proj", 3 * INNER, HIDDEN)
    sd[f"{prefix}attn.q_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
    sd[f"{prefix}attn.k_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
    _int8_linear(sd, f"{prefix}attn.out_proj", HIDDEN, INNER)
    _int8_linear(sd, f"{prefix}attn.to_gate_compress", INNER, HIDDEN)
    _int8_linear(sd, f"{prefix}mlp.fc1", 2 * FFN, HIDDEN)
    _int8_linear(sd, f"{prefix}mlp.fc2", HIDDEN, FFN)
    sd[f"{prefix}norm1.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}norm2.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)


def _bf16_gated_block(sd: dict, prefix: str) -> None:
    sd[f"{prefix}attn.qkv_proj.weight"] = torch.zeros(3 * INNER, HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}attn.q_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
    sd[f"{prefix}attn.k_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
    sd[f"{prefix}attn.out_proj.weight"] = torch.zeros(HIDDEN, INNER, dtype=torch.bfloat16)
    sd[f"{prefix}attn.to_gate_compress.weight"] = torch.zeros(INNER, HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}mlp.fc1.weight"] = torch.zeros(2 * FFN, HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}mlp.fc2.weight"] = torch.zeros(HIDDEN, FFN, dtype=torch.bfloat16)
    sd[f"{prefix}norm1.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}norm2.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)


def _refiner_block(sd: dict, prefix: str) -> None:
    """token_refiner blocks: REAL headers keep these bf16 with no
    to_gate_compress, whichever variant the main blocks carry -- verified
    directly against ``fasth3_int8_convrot_header.json``/``fasth3_bf16_header.json``."""
    sd[f"{prefix}attn.qkv_proj.weight"] = torch.zeros(3 * INNER, HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}attn.q_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
    sd[f"{prefix}attn.k_norm.weight"] = torch.zeros(HEAD_DIM, dtype=torch.bfloat16)
    sd[f"{prefix}attn.out_proj.weight"] = torch.zeros(HIDDEN, INNER, dtype=torch.bfloat16)
    sd[f"{prefix}mlp.fc1.weight"] = torch.zeros(2 * FFN, HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}mlp.fc2.weight"] = torch.zeros(HIDDEN, FFN, dtype=torch.bfloat16)
    sd[f"{prefix}norm1.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)
    sd[f"{prefix}norm2.weight"] = torch.zeros(HIDDEN, dtype=torch.bfloat16)


def _common_sd() -> dict[str, torch.Tensor]:
    sd: dict[str, torch.Tensor] = {
        "video_patch_proj.weight": torch.zeros(HIDDEN, VIDEO_PATCH_DIM, dtype=torch.float32),
        "video_patch_proj.bias": torch.zeros(HIDDEN, dtype=torch.float32),
        "audio_patch_proj.weight": torch.zeros(HIDDEN, AUDIO_IN_CHANNELS, dtype=torch.float32),
        "audio_patch_proj.bias": torch.zeros(HIDDEN, dtype=torch.float32),
        "condition_proj.weight": torch.zeros(HIDDEN, TEXT_DIM, dtype=torch.bfloat16),
        "condition_proj.bias": torch.zeros(HIDDEN, dtype=torch.bfloat16),
        "rope.inv_freq": torch.zeros(ROPE_FREQ_DIM, dtype=torch.float32),
        "final_layer.norm.weight": torch.zeros(HIDDEN, dtype=torch.bfloat16),
        "final_layer.video_out.weight": torch.zeros(VIDEO_PATCH_DIM, HIDDEN, dtype=torch.float32),
        "final_layer.video_out.bias": torch.zeros(VIDEO_PATCH_DIM, dtype=torch.float32),
        "final_layer.audio_out.weight": torch.zeros(AUDIO_IN_CHANNELS, HIDDEN, dtype=torch.float32),
        "final_layer.audio_out.bias": torch.zeros(AUDIO_IN_CHANNELS, dtype=torch.float32),
        "token_refiner.final_norm.weight": torch.zeros(HIDDEN, dtype=torch.bfloat16),
        "adaln_t_table": torch.zeros(PRUNED_ADALN_GRID, PRUNED_TIME_EMBED_DIM, dtype=torch.float32),
        "final_layer.adaln_proj.linear.weight": torch.zeros(2 * HIDDEN, PRUNED_TIME_EMBED_DIM, dtype=torch.float16),
        "final_layer.adaln_proj.linear.bias": torch.zeros(2 * HIDDEN, dtype=torch.float16),
    }
    for i in range(NUM_REFINER_LAYERS):
        _refiner_block(sd, f"token_refiner.blocks.{i}.")
    return sd


def _int8_dit_sd() -> dict[str, torch.Tensor]:
    sd = _common_sd()
    for i in range(NUM_LAYERS):
        p = f"blocks.{i}."
        _int8_gated_block(sd, p)
        sd[f"{p}adaln_proj.linear.weight"] = torch.zeros(6 * HIDDEN * 3, PRUNED_TIME_EMBED_DIM, dtype=torch.float16)
        sd[f"{p}adaln_proj.linear.bias"] = torch.zeros(6 * HIDDEN * 3, dtype=torch.float16)
    return sd


def _bf16_dit_sd() -> dict[str, torch.Tensor]:
    sd = _common_sd()
    for i in range(NUM_LAYERS):
        p = f"blocks.{i}."
        _bf16_gated_block(sd, p)
        sd[f"{p}adaln_proj.linear.weight"] = torch.zeros(6 * HIDDEN * 3, PRUNED_TIME_EMBED_DIM, dtype=torch.float16)
        sd[f"{p}adaln_proj.linear.bias"] = torch.zeros(6 * HIDDEN * 3, dtype=torch.float16)
    return sd


@pytest.fixture(scope="module")
def int8_dit_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("fasth3") / "fasth3_8step_v2_pruned_int8_convrot.safetensors"
    # Real file has no file-level __metadata__ (same as the base pruned-fp8
    # checkpoint) -- detect_quant_format must fire from the per-layer
    # comfy_quant/weight_scale markers alone.
    save_file(_int8_dit_sd(), str(path))
    return path


@pytest.fixture(scope="module")
def bf16_dit_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("fasth3") / "fasth3_8step_v2_pruned_bf16.safetensors"
    save_file(_bf16_dit_sd(), str(path))
    return path


# --- full-load dry run through NativeEngineLoader.load -----------------------

def test_int8_convrot_checkpoint_loads_through_the_real_public_path(int8_dit_path):
    loader = NativeEngineLoader(device="cpu")
    model = loader.load(int8_dit_path, "diffusion_model")

    assert isinstance(model, NativeModel)
    assert model.kind == "diffusion_model"
    assert model.spec.family == "minimax_h3"
    assert model.spec.variant == "h3"
    assert model.quant_format == QUANT_FP8_SCALED

    module = model.module
    assert isinstance(module, MiniMaxH3Model)
    assert module.config.pruned is True
    assert module.config.gate_compress is True

    block0 = module.blocks[0]
    assert hasattr(block0.attn, "to_gate_compress")
    assert block0.attn.to_gate_compress.weight.dtype == torch.int8
    assert block0.attn.to_gate_compress.weight_scale is not None
    assert block0.attn.qkv_proj.weight.dtype == torch.int8
    assert block0.attn.qkv_proj.weight_scale is not None

    # token_refiner is bf16 with no gate -- real header layout, not this
    # engine's assumption.
    assert not hasattr(module.token_refiner.blocks[0].attn, "to_gate_compress")
    assert module.token_refiner.blocks[0].attn.qkv_proj.weight_scale is None
    assert module.token_refiner.blocks[0].attn.qkv_proj.weight.dtype == torch.bfloat16

    assert torch.isfinite(module.rope.inv_freq).all()


def test_bf16_checkpoint_loads_through_the_real_public_path(bf16_dit_path):
    loader = NativeEngineLoader(device="cpu")
    model = loader.load(bf16_dit_path, "diffusion_model")

    assert isinstance(model, NativeModel)
    assert model.spec.family == "minimax_h3"
    assert model.spec.variant == "h3"
    assert model.quant_format is None  # unquantised bf16 checkpoint

    module = model.module
    assert isinstance(module, MiniMaxH3Model)
    assert module.config.pruned is True
    assert module.config.gate_compress is True

    block0 = module.blocks[0]
    assert hasattr(block0.attn, "to_gate_compress")
    assert block0.attn.to_gate_compress.weight.dtype == torch.bfloat16
    assert not hasattr(module.token_refiner.blocks[0].attn, "to_gate_compress")

    assert torch.isfinite(module.rope.inv_freq).all()


# --- meta key-set parity against the REAL headers ----------------------------

def test_bf16_meta_keyset_parity_against_real_header():
    header = _load_real_header("fasth3_bf16_header.json")
    real_keys = set(header.keys())
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(REAL_CONFIG_FASTH3, pick_operations(torch.bfloat16, torch.bfloat16))
    built = set(m.state_dict().keys())
    assert built == real_keys, (
        f"missing={sorted(real_keys - built)[:20]} extra={sorted(built - real_keys)[:20]}")


_QUANT_SIDECAR_SUFFIXES = (".weight_scale", ".input_scale", ".comfy_quant")


def test_int8_convrot_meta_keyset_parity_against_real_header_minus_quant_sidecars():
    # weight_scale/comfy_quant are consumed by Fp8ScaledLinear's own
    # _load_from_state_dict (popped before the strict key check) and never
    # appear in this module's own state_dict() -- same exclusion
    # test_minimax_h3_model.py's pruned-fp8 parity test uses.
    header = _load_real_header("fasth3_int8_convrot_header.json")
    real_keys = {k for k in header if not k.endswith(_QUANT_SIDECAR_SUFFIXES)}
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(REAL_CONFIG_FASTH3, pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    built = set(m.state_dict().keys())
    assert built == real_keys, (
        f"missing={sorted(real_keys - built)[:20]} extra={sorted(built - real_keys)[:20]}")


def test_detect_matches_real_fasth3_config_exactly():
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(REAL_CONFIG_FASTH3, pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    sd = {k: torch.empty(tuple(v.shape), device="meta") for k, v in m.state_dict().items()}
    assert detect_unet_config(sd) == REAL_CONFIG_FASTH3
