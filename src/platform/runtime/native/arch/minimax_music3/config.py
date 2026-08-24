# Derived from: diffusers `src/diffusers/pipelines/minimax_music3/modular_pipeline.py`
# / `encoders.py` (Apache-2.0, "Copyright 2026 The MiniMax Team and The HuggingFace
# Team") for the Qwen3 global-LLM + RVQ-depth-decoder hyperparameters, and ComfyUI's
# (GPL-3.0) `comfy/ldm/minimax_music/minimax_music.py` `detect_merged_config` /
# `MODEL_CONFIG` for the repack-specific layout booleans and the constants diffusers
# never states as a flat dict (``head_dim``, ``rope_theta``, ``max_position_embeddings``,
# the depth decoder's head count/width). Consulted for shape, not copied.

"""``MiniMaxMusic3TextEncoderConfig`` — construction config for the fused text-encoder
file (global Qwen3-8B LLM + RVQ depth decoder + embedded tokenizer).

The Comfy-Org single-file repack fuses two independently-varying checkpoint shapes
into one file, and the two shapes do not always move together (see the module's
``from_detect_config`` docstring): the released **pruned** file always ships all five
layout booleans flipped ``True`` (fused qkv/mlp on both stacks, pruned embeddings/head),
and the **full** file always ships all five flipped ``False`` — but nothing in the
checkpoint format *requires* that correlation, so every boolean is detected and stored
independently rather than assumed from one signature key (this is the class of bug the
H3 pruned/full divergence produced once already).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MINIMAX_MUSIC3 = "minimax_music3"

# Fixed by the reference inference recipe (diffusers `encoders.py` / ComfyUI
# `minimax_music.py`); not sampling-loop tunables a checkpoint varies.
AUDIO_VOCAB_SIZE = 1024
NUM_CODEBOOKS = 8                 # 1 semantic (LLM) + 7 residual (depth decoder)
DECODER_NUM_HEADS = 16
DECODER_HEAD_DIM = 256
ROPE_THETA = 1_000_000.0
RMS_NORM_EPS = 1e-6
MAX_POSITION_EMBEDDINGS = 10_240
NUM_ATTENTION_HEADS = 32
NUM_KEY_VALUE_HEADS = 8


@dataclass(frozen=True)
class MiniMaxMusic3TextEncoderConfig:
    """Fully-resolved text-encoder hyperparameters, for either checkpoint layout.

    ``hidden_size``/``intermediate_size``/``num_layers``/``head_dim``/
    ``decoder_intermediate_size``/``decoder_num_layers`` are shape-derived by
    :mod:`..detect.te_detect`; ``num_attention_heads``/``num_key_value_heads``/
    ``decoder_num_heads``/``decoder_head_dim``/``rope_theta``/``rms_norm_eps``/
    ``max_position_embeddings`` are architecture constants no released checkpoint
    varies (no single tensor shape recovers a head *count* when every head shares
    one width — the same class of fact ``MiniMaxH3Config.patch_size`` records).
    """

    hidden_size: int = 4096
    intermediate_size: int = 12288
    num_layers: int = 36
    head_dim: int = 128
    num_attention_heads: int = NUM_ATTENTION_HEADS
    num_key_value_heads: int = NUM_KEY_VALUE_HEADS
    rope_theta: float = ROPE_THETA
    rms_norm_eps: float = RMS_NORM_EPS
    max_position_embeddings: int = MAX_POSITION_EMBEDDINGS

    decoder_intermediate_size: int = 6144
    decoder_num_layers: int = 4
    decoder_num_heads: int = DECODER_NUM_HEADS
    decoder_head_dim: int = DECODER_HEAD_DIM
    audio_vocab_size: int = AUDIO_VOCAB_SIZE
    num_codebooks: int = NUM_CODEBOOKS

    # Layout booleans — see the module docstring. Each is read from the presence
    # of one checkpoint key; none is inferred from another.
    merged_qkv: bool = False
    merged_mlp: bool = False
    decoder_merged_qkv: bool = False
    decoder_merged_mlp: bool = False
    pruned_embeddings: bool = False   # ``model.embed_tokens_prefill`` present
    pruned_lm_head: bool = False      # ``model.lm_head_pruned`` present

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "MiniMaxMusic3TextEncoderConfig":
        if config.get("te_type") != MINIMAX_MUSIC3:
            raise ValueError(
                f"MiniMaxMusic3TextEncoderConfig: unsupported te_type {config.get('te_type')!r}"
            )
        return cls(
            hidden_size=int(config["hidden_size"]),
            intermediate_size=int(config["intermediate_size"]),
            num_layers=int(config["num_layers"]),
            head_dim=int(config["head_dim"]),
            num_attention_heads=int(config.get("num_attention_heads", NUM_ATTENTION_HEADS)),
            num_key_value_heads=int(config.get("num_key_value_heads", NUM_KEY_VALUE_HEADS)),
            rope_theta=float(config.get("rope_theta", ROPE_THETA)),
            rms_norm_eps=float(config.get("rms_norm_eps", RMS_NORM_EPS)),
            max_position_embeddings=int(config.get("max_position_embeddings", MAX_POSITION_EMBEDDINGS)),
            decoder_intermediate_size=int(config["decoder_intermediate_size"]),
            decoder_num_layers=int(config["decoder_num_layers"]),
            decoder_num_heads=int(config.get("decoder_num_heads", DECODER_NUM_HEADS)),
            decoder_head_dim=int(config.get("decoder_head_dim", DECODER_HEAD_DIM)),
            audio_vocab_size=int(config.get("audio_vocab_size", AUDIO_VOCAB_SIZE)),
            num_codebooks=int(config.get("num_codebooks", NUM_CODEBOOKS)),
            merged_qkv=bool(config["merged_qkv"]),
            merged_mlp=bool(config["merged_mlp"]),
            decoder_merged_qkv=bool(config["decoder_merged_qkv"]),
            decoder_merged_mlp=bool(config["decoder_merged_mlp"]),
            pruned_embeddings=bool(config["pruned_embeddings"]),
            pruned_lm_head=bool(config["pruned_lm_head"]),
        )
