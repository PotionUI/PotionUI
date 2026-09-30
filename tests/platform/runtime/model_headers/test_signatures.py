from __future__ import annotations

import pytest

from src.platform.runtime.model_headers import signatures as sig
from src.platform.runtime.model_headers.signatures import (
    detect_prefix,
    family_from_gguf_architecture,
    family_from_keys,
    sd_family_from_keys,
    strip_prefix,
)
from tests.fixtures.family_shape_fixtures import EXPECTED_FAMILY, family_shapes


def match(shapes):
    return family_from_keys(set(shapes), shapes.get)


def sd_match(shapes):
    return sd_family_from_keys(set(shapes), shapes.get)


@pytest.mark.parametrize("name", sorted(EXPECTED_FAMILY))
def test_minimal_key_set_maps_to_family_and_variant(name):
    found = match(family_shapes(name))
    assert (found.family, found.variant) == EXPECTED_FAMILY[name]


def test_flux2_signature_upgrades_the_variant():
    assert match(family_shapes("flux2")).variant == "flux2"
    assert match(family_shapes("flux1")).variant == "flux1"


def test_chroma_without_img_in_is_not_flux():
    shapes = family_shapes("flux1")
    del shapes["img_in.weight"]
    assert match(shapes) is None


def test_qwen_image_needs_txt_norm_so_diffusers_flux_add_q_proj_is_not_qwen():
    shapes = family_shapes("qwen_image")
    del shapes["txt_norm.weight"]
    assert match(shapes) is None


def test_flux_lora_keys_do_not_match_flux():
    shapes = {
        "double_blocks.0.processor.proj_lora1.down.weight": (16, 128),
        "double_blocks.0.processor.proj_lora1.up.weight": (128, 16),
        "double_blocks.0.processor.qkv_lora1.down.weight": (16, 128),
        "single_blocks.0.processor.proj_lora.down.weight": (16, 128),
    }
    assert match(shapes) is None
    assert sd_match(shapes) is None


def test_text_encoder_model_prefix_alone_matches_nothing():
    shapes = {
        "model.embed_tokens.weight": (1000, 64),
        "model.layers.0.self_attn.q_proj.weight": (64, 64),
        "model.layers.0.input_layernorm.weight": (64,),
    }
    assert match(shapes) is None


def test_lumina2_width_decides_z_image_or_lumina2():
    assert match(family_shapes("z_image", **{sig.LUMINA2_SIG: (3840, 2560)})).family == "z_image"
    assert match(family_shapes("z_image", **{sig.LUMINA2_SIG: (2304, 2560)})).family == "lumina2"


@pytest.mark.parametrize(
    ("in_dim", "extra", "variant"),
    [(16, {}, "t2v"), (36, {}, "i2v"), (48, {}, "ti2v"), (16, {"img_emb.proj.0.bias": (4,)}, "i2v")],
)
def test_wan_variant_comes_from_patch_embedding_width(in_dim, extra, variant):
    shapes = family_shapes("wan", **{"patch_embedding.weight": (128, in_dim, 1, 2, 2)}, **extra)
    assert match(shapes).variant == variant


@pytest.mark.parametrize("key", sorted(sig.WAN_REJECT))
def test_wan_reject_keys_name_the_extension_variant(key):
    shapes = family_shapes("wan", **{key: (4, 4)})
    found = match(shapes)
    assert found.family == "wan"
    assert found.variant == sig.WAN_REJECT[key]


def test_ltx_audio_key_makes_ltxav():
    assert match(family_shapes("ltxv")).variant == "ltxv"
    assert match(family_shapes("ltxav")).variant == "ltxav"


def test_seedvr2_variant_follows_the_gate_projection():
    assert match(family_shapes("seedvr2_3b")).variant == "3b"
    assert match(family_shapes("seedvr2_7b")).variant == "7b"


def test_seedvr2_needs_both_signature_keys():
    shapes = family_shapes("seedvr2_7b")
    del shapes[sig.SEEDVR2_SIG2]
    assert match(shapes) is None


def test_trellis_needs_all_four_prefixes():
    shapes = family_shapes("trellis2")
    assert match(shapes).family == "trellis2"
    truncated = {k: v for k, v in shapes.items() if not k.startswith("model.shape2txt.")}
    assert match(truncated) is None


def test_dispatch_order_krea2_before_flux_and_wan_before_ltx():
    shapes = {**family_shapes("flux1"), **family_shapes("krea2")}
    assert match(shapes).family == "krea2"
    shapes = {**family_shapes("ltxv"), **family_shapes("wan")}
    assert match(shapes).family == "wan"


def test_dispatch_order_qwen21_before_qwen():
    shapes = {**family_shapes("qwen_image"), **family_shapes("qwen_image21")}
    assert match(shapes).family == "qwen_image21"


def sd_shapes(*, context, label_emb, in_channels=4):
    shapes = {
        "input_blocks.0.0.weight": (320, in_channels, 3, 3),
        "input_blocks.4.1.transformer_blocks.0.attn2.to_k.weight": (640, context),
    }
    if label_emb:
        shapes["label_emb.0.0.weight"] = (1280, 2816)
    return shapes


@pytest.mark.parametrize(
    ("context", "label_emb", "in_channels", "expected"),
    [
        (2048, True, 4, ("sdxl", "base")),
        (2048, True, 9, ("sdxl", "inpaint")),
        (1280, True, 4, ("sdxl", "refiner")),
        (768, False, 4, ("sd1", "base")),
        (768, False, 9, ("sd1", "inpaint")),
        (1024, False, 4, ("sd2", "base")),
    ],
)
def test_sd_unet_family_from_declared_shapes(context, label_emb, in_channels, expected):
    found = sd_match(sd_shapes(context=context, label_emb=label_emb, in_channels=in_channels))
    assert (found.family, found.variant) == expected


def test_sd_unet_unknown_context_width_is_not_guessed():
    assert sd_match(sd_shapes(context=512, label_emb=False)) is None
    assert sd_match(sd_shapes(context=512, label_emb=True)) is None


def test_sd_unet_falls_back_to_the_first_attention_block():
    shapes = {
        "input_blocks.0.0.weight": (320, 4, 3, 3),
        "input_blocks.1.1.transformer_blocks.0.attn2.to_k.weight": (320, 768),
    }
    assert sd_match(shapes).family == "sd1"


def test_sd3_and_chroma_are_recognised_as_families_only():
    assert sd_match({"joint_blocks.0.x_block.attn.qkv.weight": (4, 4)}).family == "sd3"
    chroma = {sig.FLUX_SIG: (128,), "distilled_guidance_layer.in_proj.weight": (4, 4)}
    assert sd_match(chroma).family == "chroma"
    assert match(chroma) is None


def test_sd_unet_does_not_claim_dit_files():
    assert sd_match(family_shapes("flux1")) is None


def test_gguf_architecture_map():
    assert family_from_gguf_architecture("flux").family == "flux"
    assert family_from_gguf_architecture("LTXV").family == "ltx"
    assert family_from_gguf_architecture("llama") is None
    assert family_from_gguf_architecture(None) is None


def test_detect_prefix_is_count_based_and_none_without_candidates():
    keys = ["a.x", "a.y", "b.z"]
    assert detect_prefix(keys, ["a.", "b."]) == "a."
    assert detect_prefix(keys, ["c."]) is None


def test_strip_prefix_drops_foreign_keys():
    assert strip_prefix({"m.a": 1, "b": 2}, "m.") == {"a": 1}


def test_net_is_a_denoiser_prefix_and_model_alone_is_not():
    assert sig.detect_denoiser_prefix(["net.a", "net.b"]) == "net."
    assert sig.detect_denoiser_prefix(["model.a", "model.b"]) is None
    assert "model." not in sig.DENOISER_PREFIXES
