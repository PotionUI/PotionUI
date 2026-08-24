"""Fixtures for native text-encoder tests.

Key-parity fixtures are the *real* checkpoint key structures (captured from the
local file headers with a header-only parse — no full load, no 8GB dependency).
Each fixture is a small (non-block keys, block-0 key templates, block count) trio
that expands to the exact key set of the real checkpoint.

Also provides tiny-config synthetic-checkpoint builders so the full load path
(detect -> build -> integrity gate -> encode) can run on CPU in milliseconds.
"""

from __future__ import annotations

import torch

from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.text_encoders.clip_l import CLIPLModel
from src.platform.runtime.native.text_encoders.qwen3 import Qwen3Model
from src.platform.runtime.native.text_encoders.t5xxl import T5XXLModel

# --- Real checkpoint key structures (header-captured) ------------------------

QWEN3_4B = {
    "variant": "qwen3_4b",
    "num_blocks": 36,
    "nonblock": ["model.embed_tokens.weight", "model.norm.weight"],
    "block_tmpl": [
        "model.layers.{i}.input_layernorm.weight",
        "model.layers.{i}.mlp.down_proj.weight",
        "model.layers.{i}.mlp.gate_proj.weight",
        "model.layers.{i}.mlp.up_proj.weight",
        "model.layers.{i}.post_attention_layernorm.weight",
        "model.layers.{i}.self_attn.k_norm.weight",
        "model.layers.{i}.self_attn.k_proj.weight",
        "model.layers.{i}.self_attn.o_proj.weight",
        "model.layers.{i}.self_attn.q_norm.weight",
        "model.layers.{i}.self_attn.q_proj.weight",
        "model.layers.{i}.self_attn.v_proj.weight",
    ],
}

T5XXL = {
    "variant": "t5xxl",
    "num_blocks": 24,
    # scaled_fp8 marker + tied encoder.embed_tokens are tolerated-unexpected.
    "nonblock": [
        "encoder.embed_tokens.weight",
        "encoder.final_layer_norm.weight",
        "scaled_fp8",
        "shared.weight",
    ],
    "block_tmpl": [
        "encoder.block.{i}.layer.0.SelfAttention.k.scale_weight",
        "encoder.block.{i}.layer.0.SelfAttention.k.weight",
        "encoder.block.{i}.layer.0.SelfAttention.o.scale_weight",
        "encoder.block.{i}.layer.0.SelfAttention.o.weight",
        "encoder.block.{i}.layer.0.SelfAttention.q.scale_weight",
        "encoder.block.{i}.layer.0.SelfAttention.q.weight",
        "encoder.block.{i}.layer.0.SelfAttention.relative_attention_bias.weight",  # block 0 only
        "encoder.block.{i}.layer.0.SelfAttention.v.scale_weight",
        "encoder.block.{i}.layer.0.SelfAttention.v.weight",
        "encoder.block.{i}.layer.0.layer_norm.weight",
        "encoder.block.{i}.layer.1.DenseReluDense.wi_0.scale_weight",
        "encoder.block.{i}.layer.1.DenseReluDense.wi_0.weight",
        "encoder.block.{i}.layer.1.DenseReluDense.wi_1.scale_weight",
        "encoder.block.{i}.layer.1.DenseReluDense.wi_1.weight",
        "encoder.block.{i}.layer.1.DenseReluDense.wo.scale_weight",
        "encoder.block.{i}.layer.1.DenseReluDense.wo.weight",
        "encoder.block.{i}.layer.1.layer_norm.weight",
    ],
}

CLIP_L = {
    "variant": "clip_l",
    "num_blocks": 12,
    "nonblock": [
        "text_model.embeddings.position_embedding.weight",
        "text_model.embeddings.token_embedding.weight",
        "text_model.final_layer_norm.bias",
        "text_model.final_layer_norm.weight",
    ],
    "block_tmpl": [
        "text_model.encoder.layers.{i}.layer_norm1.bias",
        "text_model.encoder.layers.{i}.layer_norm1.weight",
        "text_model.encoder.layers.{i}.layer_norm2.bias",
        "text_model.encoder.layers.{i}.layer_norm2.weight",
        "text_model.encoder.layers.{i}.mlp.fc1.bias",
        "text_model.encoder.layers.{i}.mlp.fc1.weight",
        "text_model.encoder.layers.{i}.mlp.fc2.bias",
        "text_model.encoder.layers.{i}.mlp.fc2.weight",
        "text_model.encoder.layers.{i}.self_attn.k_proj.bias",
        "text_model.encoder.layers.{i}.self_attn.k_proj.weight",
        "text_model.encoder.layers.{i}.self_attn.out_proj.bias",
        "text_model.encoder.layers.{i}.self_attn.out_proj.weight",
        "text_model.encoder.layers.{i}.self_attn.q_proj.bias",
        "text_model.encoder.layers.{i}.self_attn.q_proj.weight",
        "text_model.encoder.layers.{i}.self_attn.v_proj.bias",
        "text_model.encoder.layers.{i}.self_attn.v_proj.weight",
    ],
}


def expand_keys(fixture: dict) -> set[str]:
    """Reconstruct the full checkpoint key set from a compact fixture."""
    keys = set(fixture["nonblock"])
    for i in range(fixture["num_blocks"]):
        for tmpl in fixture["block_tmpl"]:
            # relative_attention_bias exists on block 0 only (T5).
            if "relative_attention_bias" in tmpl and i != 0:
                continue
            keys.add(tmpl.format(i=i))
    return keys


# --- Tiny synthetic checkpoints ----------------------------------------------

_OPS = disable_weight_init


def _randomize(module) -> None:
    for p in module.parameters():
        torch.nn.init.normal_(p, std=0.02)


def tiny_qwen3_state_dict(num_layers: int = 28, hidden: int = 64, dtype=torch.bfloat16) -> dict:
    """A loadable Qwen3 checkpoint (default heads, so detection rebuilds it)."""
    cfg = {"hidden_size": hidden, "num_layers": num_layers, "vocab_size": 151936,
           "intermediate_size": 128, "te_type": "qwen3", "variant": "qwen3_8b"}
    m = Qwen3Model.from_config(cfg, _OPS)
    _randomize(m)
    return {k: v.detach().clone().to(dtype) for k, v in m.state_dict().items()}


def tiny_t5_state_dict(num_layers: int = 3, dtype=torch.float16) -> dict:
    cfg = {"hidden_size": 64, "num_layers": num_layers, "vocab_size": 32128,
           "d_kv": 16, "num_heads": 4, "d_ff": 128, "te_type": "t5xxl", "variant": "t5xxl"}
    m = T5XXLModel.from_config(cfg, _OPS)
    _randomize(m)
    return {k: v.detach().clone().to(dtype) for k, v in m.state_dict().items()}


def tiny_clip_state_dict(num_layers: int = 3, dtype=torch.float16) -> dict:
    # Default 12 heads (detection can't recover CLIP head count); hidden 24 keeps
    # it divisible by 12 so the loader rebuild matches.
    cfg = {"hidden_size": 24, "num_layers": num_layers, "vocab_size": 49408,
           "intermediate_size": 48, "te_type": "clip_l", "variant": "clip_l"}
    m = CLIPLModel.from_config(cfg, _OPS)
    _randomize(m)
    return {k: v.detach().clone().to(dtype) for k, v in m.state_dict().items()}
