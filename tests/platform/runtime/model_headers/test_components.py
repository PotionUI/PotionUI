from __future__ import annotations

import pytest

from src.platform.runtime.model_headers import (
    FamilyMatch,
    ModelClassifierDefinition,
    ModelClassifierRegistry,
    classify_header,
)
from tests.fixtures.family_shape_fixtures import family_shapes
from tests.fixtures.model_header_fixtures import make_view

VAE = {"vae.decoder.conv_in.weight": (4, 4), "vae.decoder.conv_out.weight": (4, 4)}
TE = {"text_encoders.clip_l.a": (4,), "text_encoders.clip_l.b": (4,)}
PREFIX = "model.diffusion_model."


def prefixed(shapes, prefix=PREFIX):
    return {prefix + key: shape for key, shape in shapes.items()}


def verdict(shapes, **kwargs):
    return classify_header(make_view(shapes, **kwargs))


def test_known_family_alone_is_diffusion_model():
    result = verdict(family_shapes("flux1"))
    assert (result.decided, result.model_type, result.family) == (True, "diffusion_model", "flux")


def test_known_family_with_vae_is_checkpoint():
    assert verdict({**family_shapes("flux1"), **VAE}).model_type == "checkpoint"


def test_known_family_with_text_encoder_is_checkpoint():
    assert verdict({**family_shapes("flux1"), **TE}).model_type == "checkpoint"


def test_known_family_with_both_is_checkpoint():
    result = verdict({**prefixed(family_shapes("flux1")), **VAE, **TE})
    assert (result.model_type, result.family) == ("checkpoint", "flux")
    assert result.components.denoiser and result.components.vae and result.components.text_encoder


def test_ltx_style_dit_with_vae_is_checkpoint():
    shapes = {
        **prefixed(family_shapes("ltxav")),
        "vae.decoder.a": (4,),
        "vae.decoder.b": (4,),
        "audio_vae.decoder.a": (4,),
        "vocoder.a": (4,),
        "vocoder.b": (4,),
    }
    result = verdict(shapes)
    assert (result.model_type, result.family, result.variant) == ("checkpoint", "ltx", "ltxav")


def test_single_stray_vae_key_does_not_count():
    shapes = {**family_shapes("flux1"), "vae.stray": (4,)}
    result = verdict(shapes)
    assert result.model_type == "diffusion_model"
    assert not result.components.vae


def test_single_stray_text_encoder_key_does_not_count():
    shapes = {**family_shapes("flux1"), "text_encoders.stray": (4,)}
    assert verdict(shapes).model_type == "diffusion_model"


@pytest.mark.parametrize("prefix", ["first_stage_model.", "vae.", "audio_vae.", "vocoder."])
def test_every_vae_prefix_counts_as_a_bundled_vae(prefix):
    shapes = {**family_shapes("flux1"), prefix + "a": (4,), prefix + "b": (4,)}
    result = verdict(shapes)
    assert result.model_type == "checkpoint"
    assert result.components.vae


@pytest.mark.parametrize(
    "prefix",
    [
        "conditioner.embedders.",
        "cond_stage_model.",
        "text_encoders.",
        "text_encoder.",
        "text_encoder_2.",
        "text_encoder_3.",
    ],
)
def test_every_text_encoder_prefix_counts_as_a_bundled_encoder(prefix):
    shapes = {**family_shapes("flux1"), prefix + "a": (4,), prefix + "b": (4,)}
    result = verdict(shapes)
    assert result.model_type == "checkpoint"
    assert result.components.text_encoder


def test_prefix_only_unknown_family_with_bundled_components_is_checkpoint():
    shapes = {PREFIX + "input_blocks.0.0.weight": (4, 4), **VAE}
    result = verdict(shapes)
    assert (result.decided, result.model_type, result.family) == (True, "checkpoint", "unknown")
    assert result.classifier is None


def test_prefix_only_unknown_family_with_text_encoder_is_checkpoint():
    shapes = {PREFIX + "something.weight": (4, 4), **TE}
    assert verdict(shapes).model_type == "checkpoint"


def test_prefix_only_unknown_family_alone_is_undecided():
    result = verdict({PREFIX + "something.weight": (4, 4), PREFIX + "other.weight": (4, 4)})
    assert result.decided is False
    assert result.model_type is None


def test_vae_only_file_is_undecided():
    result = verdict({"decoder.conv_in.weight": (4, 4), "encoder.conv_in.weight": (4, 4)})
    assert result.decided is False


def test_bundled_components_without_denoiser_are_undecided():
    result = verdict({**VAE, **TE})
    assert result.decided is False
    assert result.components.vae and result.components.text_encoder and not result.components.denoiser


def test_text_encoder_with_model_prefix_is_undecided():
    shapes = {
        "model.embed_tokens.weight": (1000, 64),
        "model.layers.0.self_attn.q_proj.weight": (64, 64),
        "model.layers.0.input_layernorm.weight": (64,),
    }
    assert verdict(shapes).decided is False


def test_flux_lora_is_undecided():
    shapes = {
        "double_blocks.0.processor.proj_lora1.down.weight": (16, 128),
        "double_blocks.0.processor.proj_lora1.up.weight": (128, 16),
    }
    assert verdict(shapes).decided is False


def test_net_prefixed_anima_is_a_diffusion_model():
    result = verdict(prefixed(family_shapes("anima"), "net."))
    assert (result.decided, result.model_type, result.family) == (True, "diffusion_model", "anima")


def test_net_prefix_without_a_family_is_undecided():
    assert verdict({"net.a.weight": (4, 4), "net.b.weight": (4, 4)}).decided is False


def test_sd_all_in_one_is_checkpoint_with_family():
    shapes = {
        PREFIX + "input_blocks.0.0.weight": (320, 4, 3, 3),
        PREFIX + "input_blocks.4.1.transformer_blocks.0.attn2.to_k.weight": (640, 768),
        "first_stage_model.decoder.a": (4,),
        "first_stage_model.decoder.b": (4,),
        "cond_stage_model.transformer.a": (4,),
        "cond_stage_model.transformer.b": (4,),
    }
    result = verdict(shapes)
    assert (result.model_type, result.family, result.variant) == ("checkpoint", "sd1", "base")
    assert result.classifier == "core.sd_unet"


def test_gguf_diffusion_file_is_diffusion_model():
    result = verdict(family_shapes("flux1"), fmt="gguf")
    assert result.model_type == "diffusion_model"


def test_gguf_architecture_tag_is_used_when_tensor_names_match_nothing():
    result = verdict(
        {"blk.0.attn.weight": (4, 4), PREFIX + "blk.0.ffn.weight": (4, 4)},
        fmt="gguf",
        metadata={"general.architecture": "flux"},
    )
    assert (result.decided, result.model_type, result.family) == (True, "diffusion_model", "flux")
    assert result.classifier == "core.gguf_arch"


def test_gguf_text_encoder_architecture_is_undecided():
    result = verdict({"blk.0.attn.weight": (4, 4)}, fmt="gguf", metadata={"general.architecture": "llama"})
    assert result.decided is False


def test_transformer_extractable_follows_the_family():
    assert verdict(family_shapes("flux1")).transformer_extractable is True
    assert verdict(family_shapes("trellis2")).transformer_extractable is False
    assert verdict(prefixed({"input_blocks.0.0.weight": (4, 4, 3, 3)}, PREFIX)).transformer_extractable is False


def test_family_can_override_the_content_rule():
    registry = ModelClassifierRegistry()
    registry.register(
        ModelClassifierDefinition(
            "test.forced",
            lambda view: FamilyMatch("forced", model_type="checkpoint"),
            "Forced",
            1,
            ("safetensors",),
            "test",
        )
    )
    assert classify_header(make_view({"a": (4,)}), registry).model_type == "checkpoint"


def test_verdict_carries_the_registry_fingerprint():
    result = verdict(family_shapes("flux1"))
    assert result.fingerprint
    assert result.fingerprint == verdict({"x": (1,)}).fingerprint
