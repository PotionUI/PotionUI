from __future__ import annotations

import pytest

from src.platform.runtime.model_headers import classify_header
from src.platform.runtime.model_headers.signatures import TRANSFORMER_EXTRACTABLE_FAMILIES
from tests.fixtures.family_shape_fixtures import family_shapes
from tests.fixtures.model_header_fixtures import make_view

PREFIX = "model.diffusion_model."
VAE = {"vae.decoder.a": (4,), "vae.decoder.b": (4,)}
TE = {"text_encoders.clip_l.a": (4,), "text_encoders.clip_l.b": (4,)}
ALLOWED = ["flux1", "flux2", "krea2", "qwen_image", "qwen_image21", "z_image", "wan", "anima", "seedvr2_3b", "minimax_h3"]
NOT_ALLOWED = ["ltxv", "ltxav", "minimax_music3", "yue2", "trellis2"]


def all_in_one(name, prefix=PREFIX):
    return {**{prefix + key: shape for key, shape in family_shapes(name).items()}, **VAE, **TE}


def verdict(shapes, **kwargs):
    return classify_header(make_view(shapes, **kwargs))


@pytest.mark.parametrize("name", ALLOWED)
def test_allowlisted_families_are_extractable_from_an_all_in_one(name):
    result = verdict(all_in_one(name))
    assert result.model_type == "checkpoint"
    assert result.transformer_extractable is True
    assert result.family in TRANSFORMER_EXTRACTABLE_FAMILIES


@pytest.mark.parametrize("name", NOT_ALLOWED)
def test_families_outside_the_allowlist_are_not_extractable(name):
    assert verdict(all_in_one(name)).transformer_extractable is False


def test_the_allowlist_is_the_only_gate_on_family():
    assert {verdict(all_in_one(name)).family for name in ALLOWED} == set(TRANSFORMER_EXTRACTABLE_FAMILIES)


def test_a_bnb_nf4_all_in_one_stays_a_checkpoint_but_is_not_extractable():
    shapes = {
        **all_in_one("flux1"),
        PREFIX + "double_blocks.0.img_attn.qkv.weight.absmax": (4,),
        PREFIX + "double_blocks.0.img_attn.qkv.weight.quant_map": (16,),
        PREFIX + "double_blocks.0.img_attn.qkv.weight.quant_state.bitsandbytes__nf4": (32,),
    }
    result = verdict(shapes, dtype="U8")
    assert (result.model_type, result.family) == ("checkpoint", "flux")
    assert result.transformer_extractable is False


@pytest.mark.parametrize(
    "marker",
    [".absmax", ".quant_map", ".quant_state.bitsandbytes__fp4"],
)
def test_any_bnb_quant_state_key_blocks_extraction(marker):
    shapes = {**all_in_one("flux1"), PREFIX + "double_blocks.0.img_mlp.0.weight" + marker: (4,)}
    assert verdict(shapes).transformer_extractable is False


def test_a_gguf_all_in_one_is_not_extractable():
    result = verdict(all_in_one("flux1"), fmt="gguf")
    assert (result.model_type, result.family) == ("checkpoint", "flux")
    assert result.transformer_extractable is False


@pytest.mark.parametrize(
    "reject_key",
    [
        "vace_patch_embedding.weight",
        "control_adapter.conv.weight",
        "casual_audio_encoder.encoder.final_linear.weight",
        "audio_proj.audio_proj_glob_1.layer.bias",
        "face_adapter.fuser_blocks.0.k_norm.weight",
    ],
)
def test_wan_extension_checkpoints_the_loader_refuses_are_not_extractable(reject_key):
    shapes = {**all_in_one("wan"), PREFIX + reject_key: (4,)}
    result = verdict(shapes)
    assert result.family == "wan"
    assert result.transformer_extractable is False


def test_the_net_prefix_of_anima_counts_as_the_denoiser():
    shapes = {"net." + key: shape for key, shape in family_shapes("anima").items()}
    result = verdict(shapes)
    assert (result.family, result.model_type, result.transformer_extractable) == ("anima", "diffusion_model", True)


def test_net_is_only_the_denoiser_prefix_when_every_key_carries_it():
    shapes = {**family_shapes("flux1"), "net.stray.a": (4,), "net.stray.b": (4,)}
    view = make_view(shapes)
    assert view.denoiser_prefix is None
    assert view.denoiser_keys == view.keys
    assert classify_header(view).family == "flux"
