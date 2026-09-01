"""Tests for TRELLIS.2 depot-file role classification (``detect.py``)."""

from __future__ import annotations

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.arch.trellis2.detect import (
    FLOW_BUNDLE,
    FLOW_PREFIXES,
    IMAGE_ENCODER,
    SHAPE_VAE,
    TEXTURE_VAE,
    detect_trellis2_role,
    detect_trellis2_role_from_filename,
    trellis2_role_of_file,
)

BUNDLE_KEYS = [prefix + "out_layer.weight" for prefix in FLOW_PREFIXES.values()]
SHAPE_VAE_KEYS = ["struct_dec.input_layer.weight", "shape_dec.from_latent.weight"]
TEXTURE_VAE_KEYS = ["txt_dec.from_latent.weight", "txt_dec.output_layer.bias"]
ENCODER_KEYS = ["embeddings.patch_embeddings.weight", "layer.0.norm1.weight", "norm.bias"]


# ---------------------------------------------------------------------------
# Key-space classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("keys,role", [
    (BUNDLE_KEYS, FLOW_BUNDLE),
    (SHAPE_VAE_KEYS, SHAPE_VAE),
    (TEXTURE_VAE_KEYS, TEXTURE_VAE),
    (ENCODER_KEYS, IMAGE_ENCODER),
])
def test_each_depot_file_is_classified_from_its_key_space(keys, role):
    assert detect_trellis2_role(keys) == role


@pytest.mark.parametrize("keys", [
    [],
    ["double_blocks.0.img_attn.norm.key_norm.scale", "img_in.weight"],   # flux
    ["transformer_blocks.0.attn.add_q_proj.weight", "txt_norm.weight"],  # qwen-image
    ["head.modulation"],                                                 # wan
    ["encoder.conv_in.weight", "decoder.conv_out.bias"],                 # a plain 2D AE
])
def test_a_foreign_checkpoint_is_not_claimed(keys):
    assert detect_trellis2_role(keys) is None


def test_the_bundle_needs_all_four_flows():
    """Three of four is a truncated or repacked file, not something a loader can
    read — the cascade would lose a stage with no error."""
    for dropped in FLOW_PREFIXES.values():
        partial = [k for k in BUNDLE_KEYS if not k.startswith(dropped)]
        assert detect_trellis2_role(partial) is None, dropped


def test_the_two_shape_flows_do_not_prefix_match_each_other():
    """``model.img2shape.`` must not be satisfied by ``model.img2shape_512.``
    keys — if it were, a 512-only file would classify as a full bundle."""
    only_512 = [
        FLOW_PREFIXES["structure"] + "out_layer.weight",
        FLOW_PREFIXES["shape_512"] + "out_layer.weight",
        FLOW_PREFIXES["texture"] + "out_layer.weight",
    ]
    assert detect_trellis2_role(only_512) is None


def test_the_shape_vae_is_not_mistaken_for_the_texture_vae():
    assert detect_trellis2_role(SHAPE_VAE_KEYS + TEXTURE_VAE_KEYS) == SHAPE_VAE


def test_a_layer_keyed_stack_without_patch_embeddings_is_not_the_encoder():
    assert detect_trellis2_role(["layer.0.norm1.weight", "norm.bias"]) is None


# ---------------------------------------------------------------------------
# Filename fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,role", [
    ("trellis_2_bf16.safetensors", FLOW_BUNDLE),
    ("diffusion_models/trellis_2_bf16.safetensors", FLOW_BUNDLE),
    ("trellis_2_shape_vae_bf16.safetensors", SHAPE_VAE),
    ("trellis_2_texture_vae_bf16.safetensors", TEXTURE_VAE),
    ("clip_vision/dino_v3_vit_l.safetensors", IMAGE_ENCODER),
    ("DINOv3-ViT-L.safetensors", IMAGE_ENCODER),
])
def test_the_comfy_org_filenames_classify(name, role):
    assert detect_trellis2_role_from_filename(name) == role


@pytest.mark.parametrize("name", ["flux1-dev.safetensors", "ae.safetensors", "", "notes.txt"])
def test_an_unrelated_filename_classifies_as_nothing(name):
    assert detect_trellis2_role_from_filename(name) is None


def test_the_vae_names_beat_the_bundle_name_they_also_contain():
    """Both VAE files are named ``trellis_2_..._vae_...`` — matching the bundle
    marker first would call every VAE a flow bundle."""
    assert detect_trellis2_role_from_filename("trellis_2_shape_vae_bf16.safetensors") == SHAPE_VAE
    assert detect_trellis2_role_from_filename("trellis_2_texture_vae_bf16.safetensors") == TEXTURE_VAE


# ---------------------------------------------------------------------------
# Whole-file classification (header only)
# ---------------------------------------------------------------------------


def test_a_real_file_is_classified_from_its_header_not_its_name(tmp_path):
    path = tmp_path / "something-else-entirely.safetensors"
    save_file({k: torch.zeros(2) for k in TEXTURE_VAE_KEYS}, str(path))
    assert trellis2_role_of_file(path) == TEXTURE_VAE


def test_a_misnamed_file_is_classified_by_its_keys(tmp_path):
    path = tmp_path / "trellis_2_bf16.safetensors"
    save_file({k: torch.zeros(2) for k in SHAPE_VAE_KEYS}, str(path))
    assert trellis2_role_of_file(path) == SHAPE_VAE


@pytest.mark.parametrize("name", ["model.ckpt", "model.gguf", "notes.txt"])
def test_a_non_safetensors_path_is_not_opened(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"not a checkpoint")
    assert trellis2_role_of_file(path) is None


def test_a_missing_or_corrupt_file_classifies_as_nothing(tmp_path):
    assert trellis2_role_of_file(tmp_path / "absent.safetensors") is None
    corrupt = tmp_path / "corrupt.safetensors"
    corrupt.write_bytes(b"\x00" * 32)
    assert trellis2_role_of_file(corrupt) is None
