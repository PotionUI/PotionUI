"""Tests for YuE2 depot-file role classification (``detect.py``)."""

from __future__ import annotations

import pytest

from src.platform.runtime.native.arch.yue2.detect import (
    LM,
    LM_COMFY_REPACK,
    VAE,
    detect_yue2_role,
    detect_yue2_role_from_filename,
)

LM_KEYS = ["vae2llm.weight", "model.layers.0.nar_self_attn.q_proj.weight", "lm_head.weight"]
LM_COMFY_REPACK_KEYS = ["vae2llm.weight", "llm2vae.weight", "model.layers.0.self_attn.qkv_proj.weight"]
VAE_KEYS = ["decoder.layers.0.weight_v", "decoder.layers.8.weight_v", "decoder.layers.7.alpha"]


@pytest.mark.parametrize("keys,role", [(LM_KEYS, LM), (LM_COMFY_REPACK_KEYS, LM_COMFY_REPACK), (VAE_KEYS, VAE)])
def test_each_file_is_classified_from_its_key_space(keys, role):
    assert detect_yue2_role(keys) == role


def test_the_raw_checkpoint_is_not_mistaken_for_the_comfy_repack():
    """The raw checkpoint carries BOTH `nar_self_attn.q_proj` and, per its
    separate un-fused q/k/v Linears, no `self_attn.qkv_proj` at all -- the
    real discriminator is the ABSENCE of `nar_self_attn` in the repack, which
    a signature keyed only on presence can't test directly; this instead
    proves the raw layout's own keys never satisfy the repack's signature."""
    assert "model.layers.0.self_attn.qkv_proj.weight" not in LM_KEYS
    assert detect_yue2_role(LM_KEYS) == LM


@pytest.mark.parametrize("keys", [
    [],
    ["double_blocks.0.img_attn.norm.key_norm.scale", "img_in.weight"],           # flux
    ["cond_layer_logits", "latent_conditioners.0.weight"],                       # minimax_music3 DiT
    ["dec_in_proj.weight", "decoder.model.0.weight_v", "decoder.model.6.weight_v"],  # minimax_music3 DAV
    ["model.layers.0.self_attn.q_proj.weight"],                                  # a plain Qwen3 TE (no NAR half)
])
def test_a_foreign_checkpoint_is_not_claimed(keys):
    assert detect_yue2_role(keys) is None


def test_only_one_of_the_two_lm_signature_keys_is_not_enough():
    assert detect_yue2_role(["vae2llm.weight"]) is None
    assert detect_yue2_role(["model.layers.0.nar_self_attn.q_proj.weight"]) is None


def test_only_one_of_the_two_vae_signature_keys_is_not_enough():
    assert detect_yue2_role(["decoder.layers.0.weight_v"]) is None
    assert detect_yue2_role(["decoder.layers.8.weight_v"]) is None


@pytest.mark.parametrize("name,role", [
    ("yue2-3b.safetensors", LM),
    ("YuE2_3B_fp16.safetensors", LM),
    ("yue2-vae.safetensors", VAE),
    ("yue2_vae_standard.safetensors", VAE),
])
def test_filename_fallback(name, role):
    assert detect_yue2_role_from_filename(name) == role


@pytest.mark.parametrize("name", ["flux1-dev.safetensors", "", "notes.txt", "model.safetensors"])
def test_an_unrelated_or_the_real_non_distinguishing_filename_classifies_as_nothing(name):
    """Both upstream repos ship their file as plain ``model.safetensors`` -- the real filename carries no signal at all, which is exactly why :func:`detect_yue2_role` (key-space) is what a loader trusts."""
    assert detect_yue2_role_from_filename(name) is None


def test_the_vae_marker_beats_the_bare_family_name_it_also_contains():
    assert detect_yue2_role_from_filename("yue2-vae-standard.safetensors") == VAE
