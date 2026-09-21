import numpy  # noqa: F401
import numpy._core.multiarray  # noqa: F401

import pytest
import torch
from diffusers.loaders import StableDiffusionXLLoraLoaderMixin
from transformers import CLIPTextConfig, CLIPTextModel, CLIPTextModelWithProjection

from src.pipelines.pipes.generator.sdxl.pipeline import StableDiffusionXLKDiffusionPipeline

RANK = 4
HIDDEN = 32
INTER = 64
LAYERS = 2

MODULES = [
    ("self_attn_q_proj", HIDDEN, HIDDEN),
    ("self_attn_k_proj", HIDDEN, HIDDEN),
    ("self_attn_v_proj", HIDDEN, HIDDEN),
    ("self_attn_out_proj", HIDDEN, HIDDEN),
    ("mlp_fc1", HIDDEN, INTER),
    ("mlp_fc2", INTER, HIDDEN),
]


def _kohya_state_dict():
    sd = {}
    for te in ("lora_te1_", "lora_te2_"):
        for layer in range(LAYERS):
            for module, fan_in, fan_out in MODULES:
                key = f"{te}text_model_encoder_layers_{layer}_{module}"
                sd[f"{key}.lora_down.weight"] = torch.randn(RANK, fan_in)
                sd[f"{key}.lora_up.weight"] = torch.randn(fan_out, RANK)
                sd[f"{key}.alpha"] = torch.tensor(float(RANK) * 2)
    unet = "lora_unet_input_blocks_1_1_transformer_blocks_0_attn1_to_q"
    sd[f"{unet}.lora_down.weight"] = torch.randn(RANK, HIDDEN)
    sd[f"{unet}.lora_up.weight"] = torch.randn(HIDDEN, RANK)
    sd[f"{unet}.alpha"] = torch.tensor(float(RANK))
    return sd


def _config():
    return CLIPTextConfig(
        hidden_size=HIDDEN,
        intermediate_size=INTER,
        num_hidden_layers=LAYERS,
        num_attention_heads=4,
        vocab_size=100,
        projection_dim=HIDDEN,
        bos_token_id=0,
        eos_token_id=1,
    )


@pytest.fixture
def converted():
    return StableDiffusionXLLoraLoaderMixin.lora_state_dict(_kohya_state_dict())


def _assert_adapter_loaded(text_encoder, prefix, state_dict, adapter_name):
    assert adapter_name in text_encoder.peft_config
    cfg = text_encoder.peft_config[adapter_name]
    assert cfg.r == RANK
    assert cfg.lora_alpha == RANK * 2
    lora_modules = [n for n, m in text_encoder.named_modules() if hasattr(m, "lora_B")]
    assert len(lora_modules) == LAYERS * len(MODULES)
    up_key = f"{prefix}.text_model.encoder.layers.0.self_attn.to_q_lora.up.weight"
    q_proj = next(m for n, m in text_encoder.named_modules() if n.endswith("layers.0.self_attn.q_proj"))
    assert q_proj.lora_B[adapter_name].weight.shape == state_dict[up_key].shape
    assert torch.equal(q_proj.lora_B[adapter_name].weight.float(), state_dict[up_key].float())


def test_flat_clip_text_model_receives_text_encoder_lora(converted):
    state_dict, network_alphas = converted
    te1 = CLIPTextModel(_config())
    assert not hasattr(te1, "text_model")

    StableDiffusionXLKDiffusionPipeline.load_lora_into_text_encoder(
        state_dict, network_alphas, te1, prefix="text_encoder", adapter_name="woodcut"
    )

    _assert_adapter_loaded(te1, "text_encoder", state_dict, "woodcut")


def test_nested_projection_text_model_still_receives_text_encoder_2_lora(converted):
    state_dict, network_alphas = converted
    te2 = CLIPTextModelWithProjection(_config())
    assert hasattr(te2, "text_model")

    StableDiffusionXLKDiffusionPipeline.load_lora_into_text_encoder(
        state_dict, network_alphas, te2, prefix="text_encoder_2", adapter_name="woodcut"
    )

    _assert_adapter_loaded(te2, "text_encoder_2", state_dict, "woodcut")


def test_override_leaves_original_dicts_untouched(converted):
    state_dict, network_alphas = converted
    before = set(state_dict), set(network_alphas)

    StableDiffusionXLKDiffusionPipeline.load_lora_into_text_encoder(
        state_dict, network_alphas, CLIPTextModel(_config()), prefix="text_encoder", adapter_name="a"
    )

    assert (set(state_dict), set(network_alphas)) == before
