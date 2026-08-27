"""Loader entry point for native text encoders.

``load_text_encoder`` takes one checkpoint path (Klein/Qwen3, or a single T5/CLIP)
or a pair of paths (Flux1 = T5-XXL + CLIP-L), detects each, builds the arch module
through the right ops namespace, loads it under the integrity gate, and returns a
ready ``NativeTextEncoder``.

Ops selection is automatic from the checkpoint's quantisation (fp8-scaled ->
``fp8_ops``) unless an ``operations`` override is passed.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch

from ..base import load_into_module
from ..detect.te_detect import detect_te_config
from ..errors import NativeEngineUnsupportedError
from ..io.safetensors_loader import load_torch_file
from ..io.state_dict_utils import count_blocks, weight_dtype
from vendor.gpl.comfyui.ops import detect_quant_format, pick_operations
from .base import NativeTextEncoder
from .clip_l import CLIPLModel, CLIPLTextEncoder
from .gemma3 import Gemma3Model, Gemma3TextEncoder
from .gemma4 import Gemma4Model, Gemma4TextEncoder, is_global_layer
from .qwen3 import (
    MiniMaxH3TextEncoder, Qwen3Model, Qwen3TextEncoder, Qwen3VLTextEncoder, ZImageTextEncoder,
)
from .qwen25_vl import Qwen25VLModel, Qwen25VLTextEncoder
from .t5xxl import T5XXLModel, T5XXLTextEncoder, UMT5TextEncoder
from .tokenization import (
    CLIPLTokenizerWrap,
    Gemma3Tokenizer,
    Gemma4Tokenizer,
    MiniMaxH3Tokenizer,
    Qwen3Tokenizer,
    Qwen3VLTokenizer,
    Qwen25VLTokenizer,
    T5XXLTokenizerWrap,
    UMT5Tokenizer,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TESpec:
    """Load-integrity allowlist for one text-encoder variant (duck-types ModelSpec)."""

    family: str
    variant: str
    model_class: type
    expected_missing_keys: set[str] = field(default_factory=set)
    expected_unexpected_keys: set[str] = field(default_factory=set)

    def key_is_expected_missing(self, key: str) -> bool:
        return any(fnmatch.fnmatch(key, pat) for pat in self.expected_missing_keys)

    def key_is_expected_unexpected(self, key: str) -> bool:
        return any(fnmatch.fnmatch(key, pat) for pat in self.expected_unexpected_keys)


# Quant sidecars consumed/ignored during load — tolerated as unexpected so a
# non-fp8 path never trips the integrity assert on them. ``pre_quant_scale`` (AWQ
# per-input-channel activation smoothing) is genuinely consumed by
# Fp8ScaledLinear/Nvfp4Linear._load_from_state_dict — it is applied to the
# activation at forward time, not folded into the weight, so it is popped
# unconditionally (see vendor/gpl/comfyui/ops.py) rather than silently dropped.
_QUANT_SIDECAR = {
    "*.weight_scale", "*.input_scale", "*.scale_weight", "*.scale_input", "*.weight_scale_2",
    "*.pre_quant_scale", "*.comfy_quant", "scaled_fp8",
}

_SPECS: dict[str, TESpec] = {
    "qwen3": TESpec(
        family="qwen3",
        variant="qwen3",
        model_class=Qwen3Model,
        expected_unexpected_keys=set(_QUANT_SIDECAR),
    ),
    "qwen3vl": TESpec(
        family="qwen3vl",
        variant="qwen3vl",
        # Same Qwen3 language model, at two widths (4B Krea-2, 32B MiniMax-H3
        # — see te_detect.py's width branch). By default the vision tower is
        # stripped before load so only the LM weights remain; `vision=True`
        # keeps+loads it into a `Qwen3VLVisionTower` instead (see `_load_one`).
        # Allowlisted at BOTH prefixes the two checkpoints use — nested
        # `model.visual.*` (4B) and top-level `visual.*` (32B, `vision_top_level`)
        # — defensively either way: a stray/unmatched visual key never trips
        # the integrity assert.
        model_class=Qwen3Model,
        expected_unexpected_keys={"model.visual.*", "visual.*", *_QUANT_SIDECAR},
    ),
    "qwen25_vl": TESpec(
        family="qwen25_vl",
        variant="qwen25_vl_7b",
        # Qwen-Image LM; the top-level ``visual.*`` tower and ``lm_head`` are
        # stripped before load, so only the LM weights remain.
        model_class=Qwen25VLModel,
        expected_unexpected_keys={"visual.*", "lm_head.weight", *_QUANT_SIDECAR},
    ),
    "gemma3": TESpec(
        family="gemma3",
        variant="gemma3_12b",
        # LTX-2 uses only the language model; the SigLIP vision tower + multimodal
        # projector + embedded spiece blob are stripped before load.
        model_class=Gemma3Model,
        expected_unexpected_keys={
            "vision_model.*", "multi_modal_projector.*", "spiece_model", *_QUANT_SIDECAR,
        },
    ),
    "gemma4": TESpec(
        family="gemma4",
        variant="gemma4_12b",
        # LTX-2.5 uses only the language model; the vision/audio embedders
        # (Comfy names `vision_model.*`/`multi_modal_projector.*`/
        # `audio_projector.*`, or legacy HF names `model.vision_embedder.*`/
        # `model.embed_vision.*`/`model.embed_audio.*`), the relocated
        # `text_embedding_projection.*` (read separately by
        # `model_loader/ltx/projection.py`), and any embedded tokenizer blob
        # are stripped before load.
        model_class=Gemma4Model,
        expected_unexpected_keys={
            "vision_model.*", "multi_modal_projector.*", "audio_projector.*",
            "model.vision_embedder.*", "model.embed_vision.*", "model.embed_audio.*",
            "text_embedding_projection.*", "spiece_model", "tokenizer_json",
            # Comfy gemma4 repacks also embed the HF repo's small config/template
            # files as raw-byte tensors (hf_asset__tokenizer_config.json etc.).
            "hf_asset__*",
            *_QUANT_SIDECAR,
        },
    ),
    "t5xxl": TESpec(
        family="t5xxl",
        variant="t5xxl",
        model_class=T5XXLModel,
        # `encoder.embed_tokens.weight` is a tied duplicate of `shared.weight`.
        expected_unexpected_keys={"encoder.embed_tokens.weight", *_QUANT_SIDECAR},
    ),
    "umt5": TESpec(
        family="umt5",
        variant="umt5_xxl",
        # Same T5 arch (built per_layer_bias=True); Wan-native keys are remapped and
        # the embedded `spiece_model` tokenizer blob is stripped before load.
        model_class=T5XXLModel,
        expected_unexpected_keys={"encoder.embed_tokens.weight", "spiece_model", *_QUANT_SIDECAR},
    ),
    "clip_l": TESpec(
        family="clip_l",
        variant="clip_l",
        model_class=CLIPLModel,
        # text_projection unused (pooled is pre-projection); older CLIP dumps carry
        # logit_scale / an int position_ids buffer.
        expected_unexpected_keys={
            "text_projection.weight",
            "logit_scale",
            "text_model.embeddings.position_ids",
            *_QUANT_SIDECAR,
        },
    ),
}


def _build_config(te_config: dict[str, Any], sd: dict[str, torch.Tensor]) -> dict[str, Any]:
    """Augment the detected config with dims recovered from the checkpoint shapes.

    Detection only yields hidden/layers/vocab; the arch needs a few more sizes.
    We read them from the weights rather than trusting hardcoded per-model defaults
    so a variant with different MLP/head sizes still loads correctly.
    """
    config = dict(te_config)
    te_type = te_config["te_type"]

    if te_type in ("qwen3", "qwen3vl"):
        gate = sd.get("model.layers.0.mlp.gate_proj.weight")
        if gate is not None:
            config["intermediate_size"] = int(gate.shape[0])
        # Recover head geometry from shapes — the Qwen3-0.6B (Anima) TE has 16
        # heads / head_dim 128 (inner 2048 != hidden 1024), not the 32-head
        # default the 4B/8B variants happen to match.
        qn = sd.get("model.layers.0.self_attn.q_norm.weight")
        q = sd.get("model.layers.0.self_attn.q_proj.weight")
        k = sd.get("model.layers.0.self_attn.k_proj.weight")
        if qn is not None:
            head_dim = int(qn.shape[0])
            config["head_dim"] = head_dim
            if q is not None:
                config["num_attention_heads"] = int(q.shape[0]) // head_dim
            if k is not None:
                config["num_key_value_heads"] = int(k.shape[0]) // head_dim
        if te_type == "qwen3vl" and config.get("vision"):
            # Vision-tower dims recoverable from shapes; the rest (patch/temporal/
            # merge sizes, num_position_embeddings, deepstack_visual_indexes) are
            # architectural constants not derivable from tensor shapes alone —
            # qwen3_vl_vision.py's module constants (HF config-sourced) supply
            # those, same convention as qwen_vl_vision.py's Qwen2.5-VL constants.
            # num_heads/patch/temporal_patch/merge/num_position_embeddings are
            # IDENTICAL between the 4B and 32B (MiniMax-H3) towers (verified
            # against both HF configs) so only `deepstack_indexes` needs a
            # per-variant default; the rest reuse the shared constants as-is.
            from .qwen3_vl_vision import (
                H3_VISION_DEEPSTACK_INDEXES, VISION_DEEPSTACK_INDEXES, VISION_NUM_HEADS,
                VISION_NUM_POSITION_EMBEDDINGS, VISION_PATCH_SIZE, VISION_SPATIAL_MERGE_SIZE,
                VISION_TEMPORAL_PATCH_SIZE,
            )
            is_h3 = config.get("variant") == "qwen3vl_32b"
            # The two checkpoints carry the tower at different prefixes — see
            # te_detect.py's `vision_top_level` (nested `model.visual.*` for the
            # 4B, top-level `visual.*` for the 32B).
            vision_prefix = "visual." if config.get("vision_top_level") else "model.visual."
            v_hidden = sd.get(f"{vision_prefix}blocks.0.norm1.weight")
            if v_hidden is not None:
                config["vision_hidden_size"] = int(v_hidden.shape[0])
            v_gate = sd.get(f"{vision_prefix}blocks.0.mlp.linear_fc1.weight")
            if v_gate is not None:
                config["vision_intermediate_size"] = int(v_gate.shape[0])
            config["vision_num_layers"] = count_blocks(sd, f"{vision_prefix}blocks.{{}}.")
            pos_w = sd.get(f"{vision_prefix}pos_embed.weight")
            config["vision_num_position_embeddings"] = (
                int(pos_w.shape[0]) if pos_w is not None else VISION_NUM_POSITION_EMBEDDINGS
            )
            config["vision_num_heads"] = VISION_NUM_HEADS
            config["vision_patch_size"] = VISION_PATCH_SIZE
            config["vision_temporal_patch_size"] = VISION_TEMPORAL_PATCH_SIZE
            config["vision_spatial_merge_size"] = VISION_SPATIAL_MERGE_SIZE
            config["vision_deepstack_indexes"] = H3_VISION_DEEPSTACK_INDEXES if is_h3 else VISION_DEEPSTACK_INDEXES

    elif te_type == "qwen25_vl":
        gate = sd.get("model.layers.0.mlp.gate_proj.weight")
        if gate is not None:
            config["intermediate_size"] = int(gate.shape[0])
        # Recover GQA head counts from the projection shapes (head_dim 128).
        q = sd.get("model.layers.0.self_attn.q_proj.weight")
        k = sd.get("model.layers.0.self_attn.k_proj.weight")
        if q is not None:
            config["num_attention_heads"] = int(q.shape[0]) // 128
        if k is not None:
            config["num_key_value_heads"] = int(k.shape[0]) // 128
        if config.get("vision"):
            # Vision-tower dims recoverable from shapes (num_heads=16/patch=14/
            # temporal=2/merge=2/window=112 are architectural constants, not
            # derivable from tensor shapes — qwen_vl_vision.py's module
            # constants supply those; Qwen25VLConfig.from_dict's own defaults
            # cover a config dict that omits these keys entirely).
            v_hidden = sd.get("visual.blocks.0.norm1.weight")
            if v_hidden is not None:
                config["vision_hidden_size"] = int(v_hidden.shape[0])
            v_gate = sd.get("visual.blocks.0.mlp.gate_proj.weight")
            if v_gate is not None:
                config["vision_intermediate_size"] = int(v_gate.shape[0])
            config["vision_num_layers"] = count_blocks(sd, "visual.blocks.{}.")

    elif te_type == "gemma3":
        gate = sd.get("model.layers.0.mlp.gate_proj.weight")
        if gate is not None:
            config["intermediate_size"] = int(gate.shape[0])
        # gemma3 head_dim is 256; recover GQA head counts from the projections.
        q = sd.get("model.layers.0.self_attn.q_proj.weight")
        k = sd.get("model.layers.0.self_attn.k_proj.weight")
        if q is not None:
            config["num_attention_heads"] = int(q.shape[0]) // 256
        if k is not None:
            config["num_key_value_heads"] = int(k.shape[0]) // 256

    elif te_type == "gemma4":
        gate = sd.get("model.layers.0.mlp.gate_proj.weight")
        if gate is not None:
            config["intermediate_size"] = int(gate.shape[0])
        # head_dim (sliding layers) recovered from layer 0's q_norm -- unlike
        # gemma3, never hardcoded, because the full_attention layer below can
        # use a DIFFERENT (wider) head_dim than the sliding layers.
        q = sd.get("model.layers.0.self_attn.q_proj.weight")
        k = sd.get("model.layers.0.self_attn.k_proj.weight")
        qn = sd.get("model.layers.0.self_attn.q_norm.weight")
        if qn is not None:
            head_dim = int(qn.shape[0])
            config["head_dim"] = head_dim
            if q is not None:
                config["num_attention_heads"] = int(q.shape[0]) // head_dim
            if k is not None:
                config["num_key_value_heads"] = int(k.shape[0]) // head_dim
        # global_head_dim recovered from the first full_attention layer's own
        # q_norm (see gemma4.is_global_layer — same modulo-6-or-last pattern
        # the sliding-window mask uses); falls back to head_dim (via
        # Gemma4Config.from_dict) if this checkpoint has too few layers for
        # one to exist.
        num_layers = config.get("num_layers", 0)
        global_idx = next(
            (i for i in range(num_layers) if is_global_layer(i, num_layers)), None
        )
        if global_idx is not None:
            qn_g = sd.get(f"model.layers.{global_idx}.self_attn.q_norm.weight")
            if qn_g is not None:
                global_head_dim = int(qn_g.shape[0])
                config["global_head_dim"] = global_head_dim
                # The global layers carry their OWN kv head count
                # (`num_global_key_value_heads`, 1 in the LTX-2.5 TE, vs 8 for
                # the sliding layers), and `attention_k_eq_v` is visible as the
                # ABSENCE of a v_proj on them (K doubles as V). Both are
                # recovered from shapes so a repack without the embedded
                # `gemma_config` still builds the right projections.
                k_g = sd.get(f"model.layers.{global_idx}.self_attn.k_proj.weight")
                if k_g is not None:
                    config["num_global_key_value_heads"] = int(k_g.shape[0]) // global_head_dim
                config["attention_k_eq_v"] = (
                    f"model.layers.{global_idx}.self_attn.v_proj.weight" not in sd
                )

    elif te_type == "clip_l":
        fc1 = sd.get("text_model.encoder.layers.0.mlp.fc1.weight")
        if fc1 is not None:
            config["intermediate_size"] = int(fc1.shape[0])

    # T5-XXL and UMT5 share the ComfyUI T5 layout at this point (Wan-native UMT5 is
    # already remapped before build); recover head/ff sizes from the weights.
    if te_type in ("t5xxl", "umt5"):
        rab = sd.get("encoder.block.0.layer.0.SelfAttention.relative_attention_bias.weight")
        q = sd.get("encoder.block.0.layer.0.SelfAttention.q.weight")
        wi = sd.get("encoder.block.0.layer.1.DenseReluDense.wi_0.weight")
        if rab is not None:
            config["num_heads"] = int(rab.shape[1])           # [num_buckets, num_heads]
        if wi is not None:
            config["d_ff"] = int(wi.shape[0])
        if q is not None and config.get("num_heads"):
            config["d_kv"] = int(q.shape[0]) // config["num_heads"]
        if te_type == "umt5":
            config["per_layer_bias"] = True

    return config


# Wan-native UMT5 (blocks.N.*) -> ComfyUI T5 (encoder.block.N.layer.*) key map.
_WAN_UMT5_SUFFIX_MAP = {
    "attn.q.weight": "0.SelfAttention.q.weight",
    "attn.k.weight": "0.SelfAttention.k.weight",
    "attn.v.weight": "0.SelfAttention.v.weight",
    "attn.o.weight": "0.SelfAttention.o.weight",
    "pos_embedding.embedding.weight": "0.SelfAttention.relative_attention_bias.weight",
    "norm1.weight": "0.layer_norm.weight",
    "norm2.weight": "1.layer_norm.weight",
    "ffn.gate.0.weight": "1.DenseReluDense.wi_0.weight",   # gate = Linear+GELU -> activated (wi_0)
    "ffn.fc1.weight": "1.DenseReluDense.wi_1.weight",      # fc1 = plain Linear     -> linear   (wi_1)
    "ffn.fc2.weight": "1.DenseReluDense.wo.weight",
}


def _convert_wan_umt5(sd: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    """Remap a Wan-native UMT5 state dict to the ComfyUI T5 layout."""
    out: dict[str, torch.Tensor] = {}
    for k, v in sd.items():
        if k == "token_embedding.weight":
            out["shared.weight"] = v
        elif k == "norm.weight":
            out["encoder.final_layer_norm.weight"] = v
        elif k.startswith("blocks."):
            idx, _, suffix = k[len("blocks."):].partition(".")
            mapped = _WAN_UMT5_SUFFIX_MAP.get(suffix)
            out[f"encoder.block.{idx}.layer.{mapped}" if mapped else k] = v
        else:
            out[k] = v
    return out


def _load_one(
    path: str | Path,
    *,
    operations: Any | None,
    device: str | torch.device,
    compute_dtype: torch.dtype,
    vision: bool = False,
) -> tuple[str, str, Any, dict, dict[str, str]]:
    """Detect + build + integrity-load a single checkpoint into its arch module.

    ``vision`` affects two text-encoder types:

      * ``te_type == "qwen3vl"`` (Krea-2 edit mode's 4B, or MiniMax-H3's TE at
        32B — the two share a te_type, distinguished by width): keep+load the
        checkpoint's Qwen3-VL vision tower instead of stripping it (a few
        extra GB — see ``qwen3_vl_vision.py``; the tower's prefix differs by
        width, ``vision_top_level``). Default False: dropped, exactly as
        before this flag existed, so ordinary (text-only) Krea-2/t2va
        generation's memory footprint and encode path are unaffected either
        way.
      * ``te_type == "qwen25_vl"``: keep+load the checkpoint's ``visual.*``
        tower instead of stripping it (memory cost scales with the checkpoint
        — ~1.3B extra params for the 7B).

    Ignored (no effect, no error) for every other text-encoder type.

    Returns ``(te_type, variant, module, config, metadata)``.
    """
    sd, metadata = load_torch_file(path, device="cpu")
    te_config = detect_te_config(sd)
    if te_config is None:
        raise NativeEngineUnsupportedError(f"unrecognised text encoder: {Path(path).name}")

    te_type = te_config["te_type"]
    spec = _SPECS.get(te_type)
    if spec is None:  # pragma: no cover - detection and specs are kept in lockstep
        raise NativeEngineUnsupportedError(f"no text-encoder spec for '{te_type}'")

    if te_type == "qwen3vl":
        te_config["vision"] = vision
        # Drop the vision tower unless explicitly requested — holding the ~315
        # fp8 visual tensors would waste memory for nothing on a text-only load.
        # Prefix depends on which checkpoint this is (see te_detect.py's
        # `vision_top_level`) — using the wrong one here would silently leave
        # the OTHER checkpoint's tower keys in `sd` (or strip nothing at all
        # for the 32B, whose tower is top-level `visual.*`, not `model.visual.*`).
        vision_prefix = "visual." if te_config.get("vision_top_level") else "model.visual."
        sd = {k: v for k, v in sd.items() if vision or not k.startswith(vision_prefix)}
    elif te_type == "qwen25_vl":
        te_config["vision"] = vision
        # lm_head is always dropped (never used at inference, vision or not).
        # The (top-level) vision tower is kept only when explicitly requested —
        # txt2img's memory footprint is unaffected either way.
        sd = {
            k: v for k, v in sd.items()
            if (vision or not k.startswith("visual.")) and k != "lm_head.weight"
        }
    elif te_type == "umt5":
        # Drop the embedded SentencePiece blob (loaded from the bundled asset), then
        # remap Wan-native keys to the ComfyUI T5 layout the arch expects.
        sd = {k: v for k, v in sd.items() if k != "spiece_model"}
        if te_config.get("format") == "wan_native":
            sd = _convert_wan_umt5(sd)
    elif te_type == "gemma3":
        # LTX-2 uses only the LM: drop the SigLIP vision tower, the multimodal
        # projector, and the embedded spiece blob (loaded from the bundled asset).
        sd = {
            k: v for k, v in sd.items()
            if not k.startswith("vision_model.")
            and not k.startswith("multi_modal_projector.")
            and k != "spiece_model"
        }
    elif te_type == "gemma4":
        # LTX-2.5 uses only the LM: drop the vision/audio embedders (either the
        # Comfy tower names or the legacy HF names — see the TESpec comment),
        # the relocated `text_embedding_projection.*` (read separately by
        # `model_loader/ltx/projection.py`), and the embedded tokenizer blob —
        # captured first, since (unlike Gemma3's spiece asset) it is the ONLY
        # source `Gemma4Tokenizer` has: Gemma4-Unified ships an HF fast
        # tokenizer (BPE), not a SentencePiece model, so no bundled asset can
        # substitute for it.
        tok_tensor = sd.get("tokenizer_json")
        if tok_tensor is None:
            raise NativeEngineUnsupportedError(
                f"gemma4 checkpoint missing embedded 'tokenizer_json' tensor "
                f"(required — no bundled fallback exists): {Path(path).name}"
            )
        te_config["_tokenizer_json_bytes"] = bytes(tok_tensor.numpy().tobytes())
        _GEMMA4_STRIP_PREFIXES = (
            "vision_model.", "multi_modal_projector.", "audio_projector.",
            "model.vision_embedder.", "model.embed_vision.", "model.embed_audio.",
            "text_embedding_projection.",
        )
        sd = {
            k: v for k, v in sd.items()
            if not k.startswith(_GEMMA4_STRIP_PREFIXES)
            and k not in ("spiece_model", "tokenizer_json")
        }

    ops = operations
    if ops is None:
        storage = weight_dtype(sd) or compute_dtype
        quant = detect_quant_format(metadata, sd)
        ops = pick_operations(storage, compute_dtype, quant)

    config = _build_config(te_config, sd)
    module = spec.model_class.from_config(config, ops)
    load_into_module(module, sd, spec)
    module.to(device)
    logger.info("loaded native text encoder %s/%s from %s", te_type, te_config["variant"], Path(path).name)
    return te_type, te_config["variant"], module, config, metadata


def _make_encoder(te_type: str, variant: str, module: Any, device: str | torch.device,
                  te_variant: str | None = None, config: dict | None = None) -> NativeTextEncoder:
    if te_type == "qwen3":
        if variant == "qwen3_06b":
            # Anima's TE: same Qwen3 arch, but last-hidden extraction + a T5
            # tokenizer for the DiT's in-model LLMAdapter target ids.
            from .anima import AnimaTextEncoder
            from .tokenization import AnimaQwen3Tokenizer, AnimaT5Tokenizer
            return AnimaTextEncoder(module, AnimaQwen3Tokenizer(), AnimaT5Tokenizer(), variant=variant, device=device)
        if te_variant == "zimage":
            # Z-Image reuses the qwen3_4b arch but a different encode contract
            # (penultimate layer, Z-Image template). Structurally identical to
            # Klein's TE, so the caller MUST pass this hint to disambiguate.
            from .tokenization import ZImageTokenizer
            return ZImageTextEncoder(module, ZImageTokenizer(), variant="z_image_qwen3", device=device)
        return Qwen3TextEncoder(module, Qwen3Tokenizer(), variant=variant, device=device)
    if te_type == "qwen3vl":
        if variant == "qwen3vl_32b":
            # MiniMax-H3's TE: same te_type (ordinary Qwen3 LM + Qwen3-VL vision
            # tower) but a different width, a top-level tower attachment, and an
            # entirely different encode contract (no chat template, hidden_states[50]
            # tap) — see qwen3.py's MiniMaxH3TextEncoder docstring.
            return MiniMaxH3TextEncoder(module, MiniMaxH3Tokenizer(), variant=variant, device=device)
        return Qwen3VLTextEncoder(module, Qwen3VLTokenizer(), variant=variant, device=device)
    if te_type == "qwen25_vl":
        return Qwen25VLTextEncoder(module, Qwen25VLTokenizer(), variant=variant, device=device)
    if te_type == "t5xxl":
        return T5XXLTextEncoder(module, T5XXLTokenizerWrap(), device=device)
    if te_type == "umt5":
        return UMT5TextEncoder(module, UMT5Tokenizer(), device=device)
    if te_type == "gemma3":
        return Gemma3TextEncoder(module, Gemma3Tokenizer(), variant=variant, device=device)
    if te_type == "gemma4":
        # The tokenizer comes from the checkpoint's own `tokenizer_json` blob,
        # captured by `_load_one` before that tensor was stripped from `sd` —
        # there is no bundled asset to fall back to (see Gemma4Tokenizer).
        tok_bytes = (config or {}).get("_tokenizer_json_bytes")
        if tok_bytes is None:
            raise NativeEngineUnsupportedError(
                "gemma4 encoder build is missing its tokenizer_json bytes "
                "(internal plumbing: _make_encoder must be reached via _load_one)"
            )
        return Gemma4TextEncoder(module, Gemma4Tokenizer(tok_bytes), variant=variant, device=device)
    if te_type == "clip_l":
        return CLIPLTextEncoder(module, CLIPLTokenizerWrap(), device=device)
    raise NativeEngineUnsupportedError(f"no encoder wrapper for '{te_type}'")  # pragma: no cover


class FluxTextEncoder(NativeTextEncoder):
    """Flux1 composite: T5-XXL context + CLIP-L pooled.

    encode -> {"context": [B, S, 4096], "pooled": [B, 768]}.
    """

    role = "flux1"

    def __init__(self, t5: T5XXLTextEncoder, clip_l: CLIPLTextEncoder) -> None:
        self.t5 = t5
        self.clip_l = clip_l

    def to(self, device: str | torch.device) -> "FluxTextEncoder":
        self.t5.to(device)
        self.clip_l.to(device)
        return self

    def unload(self) -> None:
        self.t5.unload()
        self.clip_l.unload()

    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        out = self.t5.encode(texts)          # {"context": ...}
        out.update(self.clip_l.encode(texts))  # {"pooled": ...}
        return out


def load_text_encoder(
    path_or_paths: str | Path | list[str | Path],
    *,
    operations: Any | None = None,
    device: str | torch.device = "cpu",
    compute_dtype: torch.dtype = torch.float32,
    te_variant: str | None = None,
    vision: bool = False,
) -> NativeTextEncoder:
    """Load one text encoder (single path) or a Flux1 composite (two paths).

    ``te_variant`` is an optional caller hint to disambiguate two encoders that
    share a checkpoint structure but differ in their encode contract (Z-Image vs
    Klein, both Qwen3-4B) — detection alone cannot tell them apart.

    ``vision`` (``qwen25_vl`` and ``qwen3vl``): keep+load the checkpoint's vision
    tower so the returned encoder can accept ``images=`` on ``encode()``
    (Qwen-Image-Edit's :class:`~.qwen25_vl.Qwen25VLTextEncoder`, or Krea-2 edit
    mode's :class:`~.qwen3.Qwen3VLTextEncoder`). Default False (text-
    only, unchanged memory footprint). **Caller responsibility:** a text-only
    and a vision-enabled load of the SAME checkpoint path build DIFFERENT
    modules (``self.visual``/``self.model.visual`` present or absent) — a
    caller that keys a model-lifecycle cache by fingerprint (e.g.
    ``ModelLifecycle.acquire(key=..., fingerprint=...)``) MUST fold this
    flag into that fingerprint string, or a stale text-only module can be handed
    back for a vision request (or vice versa) because the cache key didn't
    change. This loader has no such cache itself, so there is nothing here to
    get wrong — the hazard is entirely at the caller (a future model-loader pipe
    wiring up Qwen-Image-Edit).

    A pair of paths is resolved by role (T5-XXL + CLIP-L) regardless of order.

    ``compute_dtype`` defaults to fp32 to match ComfyUI's text-encoder activations
    (weights stay in their stored dtype and are cast per forward). This also
    guarantees the cast (``manual_cast``/``fp8_ops``) namespace is selected for the
    typical bf16/fp16/fp8 checkpoint, so the arch's fp32 activations never meet a
    lower-precision weight in a plain ``F.linear``.
    """
    if isinstance(path_or_paths, (str, Path)):
        te_type, variant, module, cfg, _meta = _load_one(
            path_or_paths, operations=operations, device=device, compute_dtype=compute_dtype, vision=vision
        )
        return _make_encoder(te_type, variant, module, device, te_variant=te_variant, config=cfg)

    paths = list(path_or_paths)
    if len(paths) != 2:
        raise NativeEngineUnsupportedError(
            f"expected 1 path, or 2 (T5-XXL + CLIP-L) for Flux1; got {len(paths)}"
        )

    encoders: dict[str, NativeTextEncoder] = {}
    for p in paths:
        te_type, variant, module, _cfg, _meta = _load_one(
            p, operations=operations, device=device, compute_dtype=compute_dtype
        )
        encoders[te_type] = _make_encoder(te_type, variant, module, device)

    if "t5xxl" in encoders and "clip_l" in encoders:
        return FluxTextEncoder(encoders["t5xxl"], encoders["clip_l"])  # type: ignore[arg-type]
    raise NativeEngineUnsupportedError(
        f"two-file text encoder must be T5-XXL + CLIP-L; got {sorted(encoders)}"
    )
