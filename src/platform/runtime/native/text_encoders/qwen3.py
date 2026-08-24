"""Qwen3 text encoder for Klein / Flux2 (4B and 8B variants).

Vendored from ComfyUI ``comfy/text_encoders/llama.py`` (the Qwen3 config path)
and trimmed to inference-only: no KV cache, no multimodal projector, no logits
head. Built entirely through the ``operations`` namespace so the fp8 cast/scale
path applies uniformly.

Klein conditioning mechanism (the load-bearing detail):
    The DiT's ``txt_in`` expects ``context_in_dim`` = 3 * hidden_size
    (12288 for the 8B, 7680 for the 4B). That vector is produced by stacking the
    residual-stream hidden states captured *before* layers 9, 18 and 27 and
    concatenating them along the feature axis. The three states are NOT passed
    through the model's final RMS norm (ComfyUI ``layer_norm_hidden_state=False``).
    Flux2's DiT has no ``vector_in``, so there is no pooled/``y`` output.

RoPE ``inv_freq`` is a non-persistent buffer recomputed in ``post_load`` — the
exact buffer that, left as meta/garbage, killed the previous Qwen attempt.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn

from ..base import NativeArchModule
from ..errors import NativeEngineUnsupportedError
from ._functional import optimized_attention, rms_norm
from .base import NativeTextEncoder, _module_to, _module_unload
from .qwen_vl_vision import qwen25vl_mrope_position_ids
from .qwen3_vl_vision import (
    H3_VISION_MAX_PIXELS,
    H3_VISION_MIN_PIXELS,
    IMAGE_PAD_TOKEN as _KREA2VL_IMAGE_PAD_TOKEN,
    VISION_DEEPSTACK_INDEXES,
    VISION_HIDDEN_SIZE,
    VISION_INTERMEDIATE_SIZE,
    VISION_NUM_HEADS,
    VISION_NUM_LAYERS,
    VISION_NUM_POSITION_EMBEDDINGS,
    VISION_PATCH_SIZE,
    VISION_SPATIAL_MERGE_SIZE,
    VISION_TEMPORAL_PATCH_SIZE,
    Qwen3VLVisionTower,
    preprocess_qwen3_vl_image,
    preprocess_qwen3_vl_video,
)

logger = logging.getLogger(__name__)

# Activation dtype for this Qwen3 TE stack (Klein/Flux2/Z-Image/Anima/Krea-2/
# MiniMax-H3 — every family built through Qwen3Model, see loader.py's
# `_SPECS`/`_make_encoder`). ``off`` (default) matches ComfyUI's fp32 TE
# activations (see `_Qwen3Transformer.forward`'s entry cast). ``on`` runs the
# whole forward in bf16 instead, which is what makes an nvfp4-quantised layer
# eligible for `vendor/gpl/comfyui/ops.py`'s native `_scaled_mm_v2` fast path
# (`_nvfp4_scaled_mm_fast_path_ok` requires `input_dtype in (float16,
# bfloat16)`) rather than always falling through to LUT dequant. No `auto`:
# unlike `$NATIVE_NVFP4_MATMUL`/`$NATIVE_FP8_MATMUL` there is no hardware probe
# behind this flag, so `auto` would just be a synonym for `on` — this is a
# numerics choice (nvfp4 activation quantisation is lossier than fp32), not a
# capability gate, and needs its own A/B (wall-time AND output quality) before
# any default changes. An unknown value is treated as `off`.
NATIVE_QWEN3_TE_BF16_ENV = "NATIVE_QWEN3_TE_BF16"


def _qwen3_te_bf16_enabled() -> bool:
    """Whether the Qwen3 TE stack should run its activations in bf16, per
    ``$NATIVE_QWEN3_TE_BF16`` (see the flag's module-level comment)."""
    policy = os.environ.get(NATIVE_QWEN3_TE_BF16_ENV, "off").strip().lower()
    if policy == "on":
        return True
    if policy != "off":
        logger.warning("qwen3 te bf16: unknown %s=%r; treating as 'off'", NATIVE_QWEN3_TE_BF16_ENV, policy)
    return False


def _qwen3_te_activation_dtype() -> torch.dtype:
    return torch.bfloat16 if _qwen3_te_bf16_enabled() else torch.float32


# Which residual-stream hidden states Klein stacks into the DiT context.
# Klein captures the state *before* (the input to) these layers.
KLEIN_LAYERS = (9, 18, 27)

# Krea-2 (Qwen3-VL-4B) fuses the *outputs* of these 12 layers (0-indexed).
# diffusers' Krea2Pipeline (Apache-2.0) spells the same set as
# `text_encoder_select_layers = (2,5,...,35)` indexing HF's `hidden_states` tuple,
# whose element 0 is the embedding output — so its index i is the output of
# decoder layer i-1, i.e. this tuple with `capture="output"`.
KREA2_LAYERS = (1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34)

# Qwen3-VL's interleaved m-RoPE T/H/W split (HF Qwen/Qwen3-VL-4B-Instruct
# config.json `text_config.rope_scaling.mrope_section`; sums to head_dim//2=64
# for head_dim=128). Distinct from Qwen2.5-VL's ROPE_DIMS (qwen_vl_vision.py) —
# same total, different split AND a different (interleaved, not chunked)
# combination scheme; see `_interleave_mrope_freqs`. MiniMax-H3's Qwen3-VL-32B
# TE uses the SAME split (`text_encoder/config.json` `rope_scaling.mrope_section
# [24,20,20]`, fetched 2026-08-10) — `_mrope` below applies this one constant
# to every Qwen3-VL width, so no H3-specific override is needed here; only its
# `rope_theta` differs (see `Qwen3Config.from_dict`).
KREA2_VL_MROPE_SECTION = (24, 20, 20)

# MiniMax-H3 reads `hidden_states[50]` of its Qwen3-VL-32B conditioner (HF's
# embedding-inclusive numbering: hidden_states[0] is the embedding output,
# hidden_states[k] for k>=1 is the output AFTER decoder layer k-1) — i.e. the
# OUTPUT of 0-indexed layer 49, not passed through the final norm. Verified
# against the real Comfy-Org trimmed repack's safetensors header metadata
# (ai/minimax_h3/te_bf16_header.json: `{"num_hidden_layers": 50, "output":
# "unnormalized_hidden_after_layer_50"}`, no `model.norm.weight`, no
# `lm_head.*`) — the trim keeps EXACTLY layers 0..49 (50 layers), which is
# already everything `hidden_states[50]` needs; a full (64-layer) checkpoint
# simply runs 14 extra layers whose output this tap discards. Fixed regardless
# of which checkpoint (trimmed or full) is loaded — NOT derived from
# `num_hidden_layers` (unlike Z-Image's penultimate-layer tap).
MINIMAX_H3_TEXT_ENCODER_LAYER_INDEX = 49

# MiniMax-H3's per-row modality tags (dossier §A.4, `MINIMAX_H3_MODALITY_NUM`):
# 0 = video, 1 = text, 2 = audio (`encode_request`'s `token_tags` output only
# ever uses the first two). A keyframe's `"<Picture i>: "` LABEL is text (1);
# only its vision block (`<|vision_start|>` + image_pad*N + `<|vision_end|>`)
# is video (0) — NOT "everything about a keyframe is video". Verified two
# ways: `encoders.py`'s `MiniMaxH3FL2VATextEncoderStep` builds `token_tags`
# with exactly this split (`[text_tag]*len(label_ids) + [video_tag]*len(vision_ids)`),
# and `before_denoise.py`'s `build_packed_sequence` docstring says the same
# ("Text is tagged 1, except for the rows of a keyframe's vision block, which
# MiniMax-H3 tags 0") — and, critically, the layout builder consumes
# `text_token_tags` VERBATIM (`token_tags[text_indices] = text_token_tags`),
# never re-deriving it, so whatever this encoder emits IS what the DiT sees.
MINIMAX_H3_TEXT_TAG = 1
MINIMAX_H3_VIDEO_TAG = 0

# The rate MiniMax-H3's conditioner READS a `ref2va` video reference at
# (`MiniMaxH3Ref2VATextEncoderStep.__init__`'s `video_sample_fps`), and the
# rate a normalized reference carries (`components.fps`). The conditioner sees
# every `MINIMAX_H3_VIDEO_FPS / MINIMAX_H3_VIDEO_SAMPLE_FPS`-th frame — two
# frames a second out of twenty-four — which is a far coarser view than the
# video VAE gets of the same reference; the two are independent and must not
# be conflated.
MINIMAX_H3_VIDEO_SAMPLE_FPS = 2.0
MINIMAX_H3_VIDEO_FPS = 24.0


@dataclass
class Qwen3Config:
    hidden_size: int
    intermediate_size: int
    num_hidden_layers: int
    vocab_size: int
    num_attention_heads: int = 32
    num_key_value_heads: int = 8
    head_dim: int = 128
    rms_norm_eps: float = 1e-6
    rope_theta: float = 1000000.0
    # True (default): construct + load `norm` (every checkpoint has it today).
    # False: MiniMax-H3's trimmed repack, which has no `model.norm.weight` key
    # at all — see `_Qwen3Transformer.__init__`'s comment. Presence-derived by
    # detection (te_detect.py), not set by hand.
    has_final_norm: bool = True
    # Krea-2's Qwen3-VL-4B vision tower — OFF by default (text-only
    # construction is unaffected: no `self.model.visual` attribute at all, so
    # the state-dict key set and every forward path are exactly as before this
    # field existed; Klein/Z-Image/Anima never set it). See qwen3_vl_vision.py.
    vision: bool = False
    # False (default): the tower nests under `model.visual.*` (Krea-2's 4B,
    # matching the checkpoint's own layout). True: the tower is a SIBLING of
    # `model.*`, top-level `visual.*` (MiniMax-H3's 32B checkpoint layout,
    # like Qwen2.5-VL's) — attachment point is chosen accordingly in
    # `Qwen3Model.__init__`/`_Qwen3Transformer.__init__` so the module's own
    # state-dict keys match the checkpoint being loaded.
    vision_top_level: bool = False
    vision_hidden_size: int = VISION_HIDDEN_SIZE
    vision_intermediate_size: int = VISION_INTERMEDIATE_SIZE
    vision_num_layers: int = VISION_NUM_LAYERS
    vision_num_heads: int = VISION_NUM_HEADS
    vision_patch_size: int = VISION_PATCH_SIZE
    vision_temporal_patch_size: int = VISION_TEMPORAL_PATCH_SIZE
    vision_spatial_merge_size: int = VISION_SPATIAL_MERGE_SIZE
    vision_num_position_embeddings: int = VISION_NUM_POSITION_EMBEDDINGS
    vision_deepstack_indexes: tuple[int, ...] = VISION_DEEPSTACK_INDEXES

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "Qwen3Config":
        hidden = int(config["hidden_size"])
        # 4B: inter 9728, 8B: inter 12288. Prefer an explicit value (loader reads
        # it from the checkpoint) and fall back to the known per-variant sizes.
        inter = config.get("intermediate_size")
        if inter is None:
            inter = 9728 if hidden <= 2560 else 12288
        # rope_theta is a non-persistent buffer (recomputed in post_load), so
        # it is never checkpoint-derivable — a hardcoded per-width default,
        # like intermediate_size's. 1e6 for every existing Qwen3-family TE
        # (Klein/Z-Image/Krea-2/Anima); MiniMax-H3's Qwen3-VL-32B TE is the
        # sole hidden>=5120 case and uses 5e6 (`text_encoder/config.json`
        # `rope_theta: 5000000`, fetched 2026-08-10). An explicit `rope_theta`
        # always wins (tests build tiny configs with their own value).
        rope_theta = config.get("rope_theta")
        if rope_theta is None:
            rope_theta = 5000000.0 if hidden >= 5120 else 1000000.0
        # Optional overrides let tests build genuinely tiny models; detection
        # never supplies them, so production uses the Qwen3 defaults.
        return cls(
            hidden_size=hidden,
            intermediate_size=int(inter),
            num_hidden_layers=int(config["num_layers"]),
            vocab_size=int(config["vocab_size"]),
            num_attention_heads=int(config.get("num_attention_heads", 32)),
            num_key_value_heads=int(config.get("num_key_value_heads", 8)),
            head_dim=int(config.get("head_dim", 128)),
            rope_theta=float(rope_theta),
            has_final_norm=bool(config.get("has_final_norm", True)),
            vision=bool(config.get("vision", False)),
            vision_top_level=bool(config.get("vision_top_level", False)),
            vision_hidden_size=int(config.get("vision_hidden_size", VISION_HIDDEN_SIZE)),
            vision_intermediate_size=int(config.get("vision_intermediate_size", VISION_INTERMEDIATE_SIZE)),
            vision_num_layers=int(config.get("vision_num_layers", VISION_NUM_LAYERS)),
            vision_num_heads=int(config.get("vision_num_heads", VISION_NUM_HEADS)),
            vision_patch_size=int(config.get("vision_patch_size", VISION_PATCH_SIZE)),
            vision_temporal_patch_size=int(config.get("vision_temporal_patch_size", VISION_TEMPORAL_PATCH_SIZE)),
            vision_spatial_merge_size=int(config.get("vision_spatial_merge_size", VISION_SPATIAL_MERGE_SIZE)),
            vision_num_position_embeddings=int(
                config.get("vision_num_position_embeddings", VISION_NUM_POSITION_EMBEDDINGS)
            ),
            vision_deepstack_indexes=tuple(config.get("vision_deepstack_indexes", VISION_DEEPSTACK_INDEXES)),
        )


def _rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2 :]
    return torch.cat((-x2, x1), dim=-1)


def _apply_rope(xq: torch.Tensor, xk: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor):
    org = xq.dtype
    q = (xq * cos) + (_rotate_half(xq) * sin)
    k = (xk * cos) + (_rotate_half(xk) * sin)
    return q.to(org), k.to(org)


def _interleave_mrope_freqs(freqs: torch.Tensor, mrope_section: tuple[int, ...] = KREA2_VL_MROPE_SECTION) -> torch.Tensor:
    """``freqs`` ``[3, ..., head_dim//2]`` (T/H/W axes) -> ``[..., head_dim//2]``.

    Port of HF Qwen3-VL's ``apply_interleaved_mrope``: unlike Qwen2.5-VL's
    chunked ``mrope_section`` split (three contiguous blocks, this repo's
    ``qwen25_vl.py::_mrope``), H and W each occupy a stride-3 subset of a
    table initialized to T's frequencies — ``freqs_t[..., offset:length:3] =
    freqs[axis, ..., offset:length:3]`` for ``offset=1`` (H) and ``offset=2``
    (W), ``length = mrope_section[axis] * 3``. For text-only position ids
    (T==H==W at every position) this is a no-op: every axis already agrees.
    """
    freqs_t = freqs[0].clone()
    for axis, offset in enumerate((1, 2), start=1):
        length = mrope_section[axis] * 3
        idx = slice(offset, length, 3)
        freqs_t[..., idx] = freqs[axis, ..., idx]
    return freqs_t


def _build_vision_tower(cfg: "Qwen3Config", operations, device=None, dtype=None) -> "Qwen3VLVisionTower":
    """Build the vision tower from ``cfg``'s ``vision_*`` fields.

    Shared by both attachment points (`_Qwen3Transformer` for the nested-4B
    layout, `Qwen3Model` for the top-level-32B layout — see
    `Qwen3Config.vision_top_level`) so the parameter list lives in one place.
    """
    return Qwen3VLVisionTower(
        hidden_size=cfg.vision_hidden_size,
        out_hidden_size=cfg.hidden_size,
        intermediate_size=cfg.vision_intermediate_size,
        num_heads=cfg.vision_num_heads,
        num_layers=cfg.vision_num_layers,
        patch_size=cfg.vision_patch_size,
        temporal_patch_size=cfg.vision_temporal_patch_size,
        spatial_merge_size=cfg.vision_spatial_merge_size,
        num_position_embeddings=cfg.vision_num_position_embeddings,
        deepstack_indexes=cfg.vision_deepstack_indexes,
        operations=operations, device=device, dtype=dtype,
    )


def _deepstack_inject(x: torch.Tensor, visual_pos_mask: torch.Tensor, visual_embeds: torch.Tensor) -> torch.Tensor:
    """Additive DeepStack injection at the image-token positions.

    ``x``: ``[B, S, H]``. ``visual_pos_mask``: ``[B, S]`` bool, True at every
    image-token position (across all images, in sequence order). ``visual_embeds``:
    ``[N_total_image_tokens, H]``, concatenated across images in that same
    sequence order. Port of HF's ``Qwen3VLTextModel._deepstack_process``
    (``hidden_states[mask] = hidden_states[mask] + visual_embeds``). Restricted
    to batch size 1 by the caller (:meth:`Qwen3VLTextEncoder._encode_with_images`),
    matching the m-RoPE position-id construction's own restriction.
    """
    x = x.clone()
    x[visual_pos_mask] = x[visual_pos_mask] + visual_embeds.to(x.dtype)
    return x


class _RMSNorm(nn.Module):
    """RMS norm with an owned weight (Qwen3 layernorms / q_norm / k_norm)."""

    def __init__(self, dim: int, eps: float, device=None, dtype=None) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.empty(dim, device=device, dtype=dtype))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return rms_norm(x, self.weight, self.eps)


class _Attention(nn.Module):
    def __init__(self, cfg: Qwen3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.num_heads = cfg.num_attention_heads
        self.num_kv_heads = cfg.num_key_value_heads
        self.head_dim = cfg.head_dim
        inner = self.num_heads * self.head_dim
        self.q_proj = operations.Linear(cfg.hidden_size, inner, bias=False, device=device, dtype=dtype)
        self.k_proj = operations.Linear(cfg.hidden_size, self.num_kv_heads * self.head_dim, bias=False, device=device, dtype=dtype)
        self.v_proj = operations.Linear(cfg.hidden_size, self.num_kv_heads * self.head_dim, bias=False, device=device, dtype=dtype)
        self.o_proj = operations.Linear(inner, cfg.hidden_size, bias=False, device=device, dtype=dtype)
        self.q_norm = _RMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.k_norm = _RMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)

    def forward(self, x, cos, sin, mask):
        b, s, _ = x.shape
        xq = self.q_proj(x).view(b, s, self.num_heads, self.head_dim).transpose(1, 2)
        xk = self.k_proj(x).view(b, s, self.num_kv_heads, self.head_dim).transpose(1, 2)
        xv = self.v_proj(x).view(b, s, self.num_kv_heads, self.head_dim).transpose(1, 2)
        xq = self.q_norm(xq)
        xk = self.k_norm(xk)
        xq, xk = _apply_rope(xq, xk, cos, sin)
        xk = xk.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
        xv = xv.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
        out = optimized_attention(xq, xk, xv, self.num_heads, mask=mask, skip_reshape=True)
        return self.o_proj(out)


class _MLP(nn.Module):
    def __init__(self, cfg: Qwen3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.gate_proj = operations.Linear(cfg.hidden_size, cfg.intermediate_size, bias=False, device=device, dtype=dtype)
        self.up_proj = operations.Linear(cfg.hidden_size, cfg.intermediate_size, bias=False, device=device, dtype=dtype)
        self.down_proj = operations.Linear(cfg.intermediate_size, cfg.hidden_size, bias=False, device=device, dtype=dtype)

    def forward(self, x):
        return self.down_proj(torch.nn.functional.silu(self.gate_proj(x)) * self.up_proj(x))


class _Block(nn.Module):
    def __init__(self, cfg: Qwen3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.self_attn = _Attention(cfg, operations, device=device, dtype=dtype)
        self.mlp = _MLP(cfg, operations, device=device, dtype=dtype)
        self.input_layernorm = _RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.post_attention_layernorm = _RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)

    def forward(self, x, cos, sin, mask):
        x = x + self.self_attn(self.input_layernorm(x), cos, sin, mask)
        x = x + self.mlp(self.post_attention_layernorm(x))
        return x


def _module_device(module: nn.Module) -> torch.device:
    """The device a module's real tensors live on, tolerant of a quantized
    submodule whose ``.weight`` was cleared to ``None`` after load.

    A quantized ``Embedding``/``Linear`` (int8-tensorwise, nvfp4 —
    vendor/gpl/comfyui/ops.py) frees its empty float ``.weight`` once its
    dequant state is loaded into other buffers instead (``_int8_weight``,
    ``nvfp4_packed``, ...), so reading ``embed_tokens.weight.device``
    unconditionally AttributeErrors on a checkpoint whose embedding is
    quantized (e.g. MiniMax-H3's real nvfp4_awq TE, whose `embed_tokens` is
    int8 — see the port report addendum). Every loaded tensor in a module
    sits on the same device at any given moment (``post_load`` runs on the
    freshly-loaded, still-CPU module, before the loader's later
    ``.to(device)``), so the first non-None parameter or buffer anywhere in
    the tree gives the right answer regardless of which submodule happens to
    be quantized.
    """
    for p in module.parameters():
        if p is not None:
            return p.device
    for b in module.buffers():
        if b is not None:
            return b.device
    raise RuntimeError(f"{module.__class__.__name__} has no tensors to read a device from")


class _Qwen3Transformer(nn.Module):
    """The ``model.*`` submodule (embed_tokens / layers / norm / [visual])."""

    def __init__(self, cfg: Qwen3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.embed_tokens = operations.Embedding(cfg.vocab_size, cfg.hidden_size, device=device, dtype=dtype)
        self.layers = nn.ModuleList(
            [_Block(cfg, operations, device=device, dtype=dtype) for _ in range(cfg.num_hidden_layers)]
        )
        # MiniMax-H3's real Comfy-Org trimmed repack has NO `model.norm.weight`
        # at all (verified against both the bf16 and nvfp4_awq headers) — every
        # caller of this arch (Klein/Krea-2/Z-Image/H3) taps an intermediate
        # layer and never runs the final norm anyway (see `forward`'s "NOT
        # passed through the final norm"); Anima is the one exception that
        # DOES call `.norm()` (`anima.py`'s `encode`), but Anima's checkpoint
        # is not trimmed and genuinely has the key. `cfg.has_final_norm` is
        # presence-derived from the checkpoint (te_detect.py), not
        # variant-hardcoded, so it stays correct for any current or future
        # checkpoint shape without special-casing H3 by name here.
        if cfg.has_final_norm:
            self.norm = _RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        # Non-persistent: never in the checkpoint, recomputed in post_load.
        self.register_buffer("inv_freq", torch.empty(cfg.head_dim // 2), persistent=False)
        # Krea-2 vision tower (Qwen3-VL-4B) — attached HERE, not on `Qwen3Model`
        # directly, so its state-dict keys resolve to the checkpoint's
        # `model.visual.*` prefix (see qwen3_vl_vision.py's Qwen3VLVisionTower
        # docstring). Absent entirely unless `cfg.vision` is True. MiniMax-H3's
        # 32B checkpoint carries its tower top-level instead (`vision_top_level`)
        # — that attachment happens on `Qwen3Model` below, not here.
        if cfg.vision and not cfg.vision_top_level:
            self.visual = _build_vision_tower(cfg, operations, device=device, dtype=dtype)

    def recompute_inv_freq(self) -> None:
        half = torch.arange(0, self.cfg.head_dim, 2, dtype=torch.float32)
        self.inv_freq = (1.0 / (self.cfg.rope_theta ** (half / self.cfg.head_dim))).to(_module_device(self))

    def _rope(self, seq_len: int, device, dtype):
        pos = torch.arange(seq_len, device=device, dtype=torch.float32)
        freqs = torch.outer(pos, self.inv_freq.to(device))  # [S, head_dim/2]
        emb = torch.cat((freqs, freqs), dim=-1)             # [S, head_dim]
        cos = emb.cos()[None, None].to(dtype)               # [1,1,S,head_dim]
        sin = emb.sin()[None, None].to(dtype)
        return cos, sin

    def _mrope(self, position_ids: torch.Tensor, device, dtype):
        """3-axis INTERLEAVED m-RoPE cos/sin for Krea-2's image-conditioned
        encode. ``position_ids``: ``[3, S]`` (batch size 1 — see
        :meth:`Qwen3VLTextEncoder._encode_with_images`). Reduces to
        :meth:`_rope`'s bit-identical output whenever all 3 axes agree
        (text-only position ids), since ``_interleave_mrope_freqs`` is then a
        no-op relabelling of the same values.
        """
        inv_freq = self.inv_freq.to(device)
        freqs = position_ids.to(torch.float32).unsqueeze(-1) * inv_freq  # [3, S, head_dim//2]
        freqs_t = _interleave_mrope_freqs(freqs)
        emb = torch.cat((freqs_t, freqs_t), dim=-1)  # [S, head_dim]
        cos = emb.cos()[None, None].to(dtype)
        sin = emb.sin()[None, None].to(dtype)
        return cos, sin

    def forward(
        self, input_ids, attention_mask, layers_to_extract, capture: str = "input",
        inputs_embeds: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
        deepstack_embeds: list[torch.Tensor] | None = None,
        visual_pos_mask: torch.Tensor | None = None,
    ):
        # Text-only default path (inputs_embeds=None, position_ids=None,
        # deepstack_embeds=None) is UNCHANGED from before vision support
        # existed -- bit-identical (Klein/Z-Image/Anima/Krea-2 text-only all
        # take this path; none of them ever pass the new kwargs).
        #
        # activation_dtype is fp32 by default (matches ComfyUI's fp32 TE
        # activations) or bf16 under $NATIVE_QWEN3_TE_BF16 (see that flag's
        # module-level comment). Every downstream op in this forward already
        # threads x.dtype dynamically (RoPE cos/sin's final `.to(dtype)`, the
        # attention mask's `x.dtype`-parameterised build, `rms_norm`'s
        # weight-to-x cast) rather than hardcoding fp32, so casting the entry
        # point is sufficient for the dtype to hold all the way to every
        # Linear -- no other fp32 island re-promotes it back up.
        activation_dtype = _qwen3_te_activation_dtype()
        if inputs_embeds is not None:
            x = inputs_embeds.to(activation_dtype)
        else:
            # Embedding is exact row selection; gather then cast works for any
            # ops namespace (plain or cast).
            x = self.embed_tokens(input_ids).to(activation_dtype)
        b, s, _ = x.shape
        cos, sin = self._mrope(position_ids, x.device, x.dtype) if position_ids is not None else self._rope(s, x.device, x.dtype)

        mask = None
        if attention_mask is not None:
            mask = 1.0 - attention_mask.to(x.dtype).reshape((b, 1, -1, s)).expand(b, 1, s, s)
            mask = mask.masked_fill(mask.to(torch.bool), torch.finfo(x.dtype).min / 4)
        if s > 1:
            causal = torch.empty(s, s, dtype=x.dtype, device=x.device).fill_(torch.finfo(x.dtype).min / 4).triu_(1)
            mask = causal if mask is None else mask + causal

        # capture="input": Klein — the state BEFORE layer i.
        # capture="output": Krea-2 — the state AFTER layer i runs.
        if capture not in ("input", "output"):
            raise ValueError(f"capture must be 'input' or 'output', got {capture!r}")
        only = set(layers_to_extract)
        n = len(self.layers)
        if any(idx >= n for idx in only):
            raise ValueError(
                f"Qwen3 needs layers {sorted(only)} but this checkpoint has only {n}."
            )
        collected: list[torch.Tensor] = []
        for i, layer in enumerate(self.layers):
            if capture == "input" and i in only:
                collected.append(x.unsqueeze(1))
            x = layer(x, cos, sin, mask)
            if deepstack_embeds is not None and i < len(deepstack_embeds):
                x = _deepstack_inject(x, visual_pos_mask, deepstack_embeds[i])
            if capture == "output" and i in only:
                collected.append(x.unsqueeze(1))
        # Intermediate states are NOT passed through the final norm — matching
        # both ComfyUI's `layer_norm_hidden_state=False` and HF's own
        # `output_hidden_states=True` tuple, which is what diffusers' Krea2Pipeline
        # stacks from.
        return torch.cat(collected, dim=1)  # [B, len(layers), S, H]


class Qwen3Model(NativeArchModule):
    """Native arch module wrapper: ``model.*`` keys map 1:1 to the checkpoint.

    ``self.visual`` (top-level) exists ONLY when ``cfg.vision and
    cfg.vision_top_level`` (MiniMax-H3's 32B checkpoint) — a text-only or
    nested-tower (Krea-2 4B) construction has no such attribute at all, so its
    state-dict key set is exactly as before this attachment point existed.
    """

    def __init__(self, cfg: Qwen3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.model = _Qwen3Transformer(cfg, operations, device=device, dtype=dtype)
        if cfg.vision and cfg.vision_top_level:
            self.visual = _build_vision_tower(cfg, operations, device=device, dtype=dtype)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "Qwen3Model":
        return cls(Qwen3Config.from_dict(config), operations)

    def post_load(self) -> None:
        self.model.recompute_inv_freq()

    def forward(
        self, input_ids, attention_mask=None, layers_to_extract=KLEIN_LAYERS, capture: str = "input",
        inputs_embeds=None, position_ids=None, deepstack_embeds=None, visual_pos_mask=None,
    ):
        return self.model(
            input_ids, attention_mask, layers_to_extract, capture=capture,
            inputs_embeds=inputs_embeds, position_ids=position_ids,
            deepstack_embeds=deepstack_embeds, visual_pos_mask=visual_pos_mask,
        )


class Qwen3TextEncoder(NativeTextEncoder):
    """Klein text encoder: prompts -> {"context": [B,S,3H], "attention_mask": [B,S]}."""

    def __init__(self, module: Qwen3Model, tokenizer, variant: str, device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self.role = variant
        self._device = torch.device(device)

    def to(self, device: str | torch.device) -> "Qwen3TextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        ids, mask = self.tokenizer(texts, device=self._device)
        return self._encode_ids(ids, mask)

    def _encode_ids(self, ids, mask) -> dict[str, torch.Tensor]:
        stacked = self.module(ids, attention_mask=mask, layers_to_extract=KLEIN_LAYERS)  # [B,3,S,H]
        # Flux2 stacking: [B,3,S,H] -> [B,S,3,H] -> [B,S,3H]  (ComfyUI Flux2TEModel).
        context = stacked.movedim(1, 2).reshape(stacked.shape[0], stacked.shape[2], -1)
        return {"context": context, "attention_mask": mask}

    def _tokenize_weighted(self, prompt: str):
        return self.tokenizer.tokenize_with_weights(prompt, device=self._device)


def _trim_padded_tail(context: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Drop the trailing padding ``tokenization.py``'s 512-token minimum adds,
    for a batch where every row's real content ends no later than the
    longest row's.

    ``tokenization.py`` right-pads every prompt to ``max(512, longest)`` for
    ComfyUI-parity hidden states, mask 0 for padding. Krea-2's DiT
    (``build_stream_inputs``) keeps that trailing padding as a joint
    ``[text; image]`` key-padding mask whenever any row has a real ``0`` in
    its mask - which is *every* normal single-prompt generation, since a
    typical prompt is far short of 512 tokens. A non-None mask forces the
    attention dispatcher off its fast fused path onto ``sdpa`` for every block
    on every step (sage2/flash need the plain, unmasked call), even though the
    masked-out tail contributes exactly zero to any real token's output
    (softmax excludes a ``-inf``-masked key/value entirely - dropping it
    outright is mathematically identical, not an approximation).

    Trimming to ``keep = max over the batch of each row's real-token count``
    makes a batch-1 (or equal-length-batch) mask all-True, so
    ``build_stream_inputs`` takes its existing all-valid short-circuit and
    sage2/flash run on the full joint sequence. A batch with unequal real
    lengths still gets a real (now merely tighter) mask - unchanged behavior,
    still ``sdpa`` for that case, which is correct.

    Deliberately NOT folded into ``_encode_ids``: ``NativeTextEncoder.
    encode_weighted`` (base.py) calls ``_encode_ids`` twice - once for the real
    prompt, once for an empty-prompt baseline right-padded (via ``_align_len``)
    to the real prompt's *raw* token count - and depends on both calls
    returning the SAME sequence length so it can subtract token-for-token. The
    empty prompt's own real-token count is unrelated to the real prompt's, so
    trimming inside ``_encode_ids`` would desync those two lengths and break
    prompt-weighting for Krea-2. Trimming only in the plain (unweighted)
    ``encode()`` path avoids that hazard entirely.
    """
    keep = int(mask.sum(dim=1).max().item())
    return context[:, :keep], mask[:, :keep]


class Qwen3VLTextEncoder(NativeTextEncoder):
    """Krea-2 text encoder (Qwen3-VL-4B language model).

    prompts -> ``{"context": [B, S', 12, hidden], "attention_mask": [B, S']}``.

    The layer axis is kept SEPARATE (position 2) — Krea-2's ``prepare_context``
    attends across the 12 layers before collapsing them, so (unlike Klein) they
    are NOT concatenated on the feature axis. By default only the language
    model is used; the checkpoint's vision tower is dropped at load. The
    template prefix tokens are stripped from the returned sequence, matching
    diffusers' ``Krea2Pipeline`` (``hidden_states[:, prompt_template_encode_start_idx:]``).

    ``encode(texts, images=...)``: vision-grounded instruction encode,
    when this encoder was built from a ``vision=True`` load (see
    ``loader.py``) — the source image(s) are embedded through the Qwen3-VL
    vision tower and spliced into the LLM's input sequence + DeepStack layers,
    ported from ``comfyui-krea2edit``'s ``Krea2EditGroundedEncode`` node
    (Apache-2.0, lbouaraba). See :meth:`_encode_with_images`.
    """

    role = "qwen3vl_4b"

    def __init__(self, module: Qwen3Model, tokenizer, variant: str = "qwen3vl_4b",
                 device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self.role = variant
        self._device = torch.device(device)
        self._has_vision = hasattr(module.model, "visual")

    def to(self, device: str | torch.device) -> "Qwen3VLTextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(
        self, texts: list[str], images: list[torch.Tensor] | None = None,
        grounding_px: int = 768, system_prompt: str | None = None,
    ) -> dict[str, torch.Tensor]:
        if images:
            return self._post_encode(self._encode_with_images(texts, images, grounding_px, system_prompt))
        ids, mask, _prefix_len = self.tokenizer(texts, device=self._device)
        return self._post_encode(self._encode_ids(ids, mask))

    def _encode_with_images(
        self, texts: list[str], images: list[torch.Tensor], grounding_px: int, system_prompt: str | None,
    ) -> dict[str, torch.Tensor]:
        """Splice per-image vision-tower embeddings (+ DeepStack taps) into the
        token embedding sequence in place of each image's ``<|vision_start|>
        <|image_pad|><|vision_end|>`` slot, build the matching interleaved
        3-axis m-RoPE position ids, and run the LM (with DeepStack injection)
        on the result.

        Restricted to one prompt per call (batch size 1) — same restriction as
        :meth:`~.qwen25_vl.Qwen25VLTextEncoder._encode_with_images` and for the
        same reason (the position-id / DeepStack-mask construction is
        inherently per-sequence, no batch axis).

        Caching: this method does not itself cache — the CALLER
        (``Krea2ClipTextEncoder``) is responsible for folding an image
        identity into its cache key (``embed_cache.image_content_fingerprint``)
        alongside ``grounding_px``, or two different source images with the
        same prompt text would alias to the same cached embedding.
        """
        if not self._has_vision:
            raise NativeEngineUnsupportedError(
                "this krea-2 qwen3-vl text encoder has no vision tower loaded; "
                "request the vision-enabled variant at load time "
                "(load_text_encoder(..., vision=True))"
            )
        if len(texts) != 1:
            raise ValueError("image-conditioned encode() supports exactly one prompt per call")

        ids, mask, prefix_len = self.tokenizer.tokenize_with_images(
            texts[0], num_images=len(images), device=self._device, system_prompt=system_prompt,
        )
        pad_positions = (ids[0] == _KREA2VL_IMAGE_PAD_TOKEN).nonzero(as_tuple=True)[0].tolist()
        if len(pad_positions) != len(images):
            raise ValueError(
                f"template has {len(pad_positions)} <|image_pad|> slot(s) but got {len(images)} image(s)"
            )

        text_embeds = self.module.model.embed_tokens(ids).to(torch.float32)  # [1, S, H]
        visual: Qwen3VLVisionTower = self.module.model.visual

        embed_pieces: list[torch.Tensor] = []
        mask_pieces: list[torch.Tensor] = []
        image_spans: list[tuple[int, int, torch.Tensor]] = []
        deepstack_pieces: list[list[torch.Tensor]] = [[] for _ in visual.deepstack_indexes]
        cursor = 0
        for pad_pos, image in zip(pad_positions, images):
            embed_pieces.append(text_embeds[:, cursor:pad_pos])
            mask_pieces.append(mask[:, cursor:pad_pos])

            patches, grid_thw = preprocess_qwen3_vl_image(
                image.to(self._device), grounding_px=grounding_px,
                patch_size=visual.patch_size,
                temporal_patch_size=visual.patch_embed.temporal_patch_size,
                merge_size=visual.spatial_merge_size,
            )
            merged, deepstack_feats = visual(patches.to(self._device, dtype=torch.float32), grid_thw)
            start = sum(p.shape[1] for p in embed_pieces)
            embed_pieces.append(merged.unsqueeze(0).to(text_embeds.dtype))
            mask_pieces.append(torch.ones((mask.shape[0], merged.shape[0]), dtype=mask.dtype, device=mask.device))
            image_spans.append((start, merged.shape[0], grid_thw))
            for j, feat in enumerate(deepstack_feats):
                deepstack_pieces[j].append(feat)

            cursor = pad_pos + 1
        embed_pieces.append(text_embeds[:, cursor:])
        mask_pieces.append(mask[:, cursor:])

        embeds = torch.cat(embed_pieces, dim=1)
        new_mask = torch.cat(mask_pieces, dim=1)
        # Position-id CONSTRUCTION is shared with Qwen2.5-VL (see this module's
        # header docstring) — only the rotary COMBINATION (interleaved, in
        # `_Qwen3Transformer._mrope`) differs.
        position_ids = qwen25vl_mrope_position_ids(image_spans, embeds.shape[1], embeds.device)

        visual_pos_mask = new_mask.new_zeros(new_mask.shape, dtype=torch.bool)
        for start, size, _grid in image_spans:
            visual_pos_mask[:, start:start + size] = True
        deepstack_embeds = [torch.cat(pieces, dim=0) for pieces in deepstack_pieces]

        stacked = self.module(
            None, attention_mask=new_mask, layers_to_extract=KREA2_LAYERS, capture="output",
            inputs_embeds=embeds, position_ids=position_ids,
            deepstack_embeds=deepstack_embeds, visual_pos_mask=visual_pos_mask,
        )
        # Splicing only ever inserts tokens AFTER the prefix boundary (every
        # <|image_pad|> sits inside the user turn, past the system+"user\n"
        # prefix), so `prefix_len` — computed on the PRE-splice sequence —
        # still points at the correct boundary in the spliced one.
        context = stacked.movedim(1, 2)[:, prefix_len:]
        return {"context": context, "attention_mask": new_mask[:, prefix_len:]}

    def _encode_ids(self, ids, mask) -> dict[str, torch.Tensor]:
        pl = self.tokenizer._prefix_len
        stacked = self.module(ids, attention_mask=mask, layers_to_extract=KREA2_LAYERS, capture="output")  # [B,12,S,H]
        # -> [B, S, 12, H]; strip the template prefix from the sequence axis.
        context = stacked.movedim(1, 2)[:, pl:]
        return {"context": context, "attention_mask": mask[:, pl:]}

    def _tokenize_weighted(self, prompt: str):
        ids, mask, weights = self.tokenizer.tokenize_with_weights(prompt, device=self._device)
        pl = self.tokenizer._prefix_len
        # strip prefix from weights so they align 1:1 with the prefix-stripped context.
        return ids, mask, weights[:, pl:]

    @torch.inference_mode()
    def encode_weighted(self, prompt: str) -> dict[str, torch.Tensor]:
        # `NativeTextEncoder.encode_weighted` (base.py) calls `_encode_ids` twice
        # internally (real prompt + an empty-prompt baseline aligned to the same
        # raw length) and needs both calls to return the SAME sequence length so
        # it can subtract token-for-token - trimming inside `_encode_ids` would
        # desync those two lengths (see `_trim_padded_tail`'s docstring). So the
        # base class runs untrimmed, and only the FINAL, already-interpolated
        # result is trimmed here, using the real prompt's own (correct) mask.
        return self._post_encode(super().encode_weighted(prompt))

    def _post_encode(self, result: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        # Trim here (both plain and weighted paths) rather than in `_encode_ids`:
        # encode_weighted's real/baseline calls must stay length-aligned until
        # after the weight subtraction - see `_trim_padded_tail`'s docstring.
        context, mask = _trim_padded_tail(result["context"], result["attention_mask"])
        return {"context": context, "attention_mask": mask}


class ZImageTextEncoder(NativeTextEncoder):
    """Z-Image text encoder (Qwen3-4B language model).

    prompts -> ``{"context": [B, S, hidden=2560], "attention_mask": [B, S]}``.

    Unlike Klein (which concatenates layers 9/18/27) Z-Image consumes a SINGLE
    hidden state: the OUTPUT of the penultimate decoder layer (ComfyUI
    ``layer="hidden", layer_idx=-2`` -> ``num_layers - 2``), NOT passed through
    the model's final RMS norm (``layer_norm_hidden_state=False``). The full
    templated sequence is kept — Z-Image's NextDiT pads the caption with a learned
    pad token and attends without a mask, so no prefix is stripped here.
    """

    role = "z_image_qwen3"

    def __init__(self, module: Qwen3Model, tokenizer, variant: str = "z_image_qwen3",
                 device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self.role = variant
        self._device = torch.device(device)
        # Penultimate layer (ComfyUI layer_idx=-2), derived from the checkpoint depth.
        self._layer = module.cfg.num_hidden_layers - 2

    def to(self, device: str | torch.device) -> "ZImageTextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        ids, mask = self.tokenizer(texts, device=self._device)
        return self._encode_ids(ids, mask)

    def _encode_ids(self, ids, mask) -> dict[str, torch.Tensor]:
        stacked = self.module(ids, attention_mask=mask, layers_to_extract=(self._layer,), capture="output")  # [B,1,S,H]
        context = stacked.squeeze(1)   # [B, S, H] — single penultimate layer, no final norm
        return {"context": context, "attention_mask": mask}

    def _tokenize_weighted(self, prompt: str):
        return self.tokenizer.tokenize_with_weights(prompt, device=self._device)


def _find_pad_run(token_ids: list[int], pad_token_id: int, start: int) -> tuple[int, int]:
    """Locate the next maximal contiguous run of ``pad_token_id`` at/after ``start``.

    Stands in for the reference's ``processor.create_mm_token_type_ids`` (a HF
    processor this port has no dependency on): a MiniMax-H3 presentation places
    each vision block's placeholder tokens (``<|image_pad|>``/``<|video_pad|>``)
    as one contiguous run per block, in packed order — scanning for runs is
    exactly equivalent for locating them.
    """
    n = len(token_ids)
    i = start
    while i < n and token_ids[i] != pad_token_id:
        i += 1
    if i >= n:
        raise ValueError(f"no run of pad token {pad_token_id} found at/after position {start}")
    j = i
    while j < n and token_ids[j] == pad_token_id:
        j += 1
    return i, j - i


def _sample_reference_video_frames(
    num_frames: int, *, fps: float, sample_fps: float, temporal_patch: int,
) -> tuple[list[int], list[float]]:
    """Which frames of a normalized `ref2va` video reference the conditioner
    reads, and the timestamp labelling every merged frame-group
    (``MiniMaxH3Ref2VATextEncoderStep._sample_video_condition_frames``).

    The conditioner reads at ``sample_fps``: every ``fps / sample_fps``-th
    frame, deduplicated by index. Qwen3-VL then merges the sampled frames in
    groups of ``temporal_patch`` — repeating the LAST one when the count does
    not divide, which is why the timestamp list is padded the same way — and a
    merged group is labelled with the MEAN of its own timestamps.

    That mean is rendered by ``"{:.1f}"``, which is round-half-to-EVEN, so a
    2 fps pair's ``(0.0 + 0.5) / 2 = 0.25`` renders as ``"<0.2 seconds>"`` and
    not ``"<0.3 seconds>"``. Reproduced deliberately; a "fix" to half-up
    changes the tokenized presentation.
    """
    stride = fps / sample_fps
    indices: list[int] = []
    cursor = 0.0
    while round(cursor) < num_frames:
        if not indices or round(cursor) > indices[-1]:
            indices.append(round(cursor))
        cursor += stride
    if len(indices) < temporal_patch:
        minimum = round((temporal_patch - 1) * stride) + 1
        raise ValueError(
            f"a reference video is read at {sample_fps:g} fps and its sampled frames are merged in groups of "
            f"{temporal_patch}, so it must run at least {minimum} frames at {fps:g} fps "
            f"({minimum / fps:.2g} seconds), got {num_frames}"
        )

    timestamps = [index / sample_fps for index in range(len(indices))]
    timestamps += [timestamps[-1]] * (-len(timestamps) % temporal_patch)
    block_timestamps = [
        (timestamps[index] + timestamps[index + temporal_patch - 1]) / 2
        for index in range(0, len(timestamps), temporal_patch)
    ]
    return indices, block_timestamps


@dataclass
class MiniMaxH3VisionRun:
    """One already-preprocessed vision block of a MiniMax-H3 presentation.

    Presentation-building (resizing/patchifying images or video frame-groups,
    choosing labels, ordering blocks) is the generation pipe's job — this is
    the primitive the pipe hands the encoder, one per vision block it placed
    in ``token_ids``, in the SAME packed order.

    ``patches``: the vision tower's own patch input for this block — a single
    image, or one already-merged video frame-group — both
    ``[num_patches, patch_dim]``, the convention
    :func:`~.qwen3_vl_vision.preprocess_qwen3_vl_image` produces.
    ``grid_thw``: that block's ``[1, 3]`` patch-unit grid.
    ``pad_token_id``: which placeholder token (``<|image_pad|>`` 151655 for an
    image or a video's merged frame-group, ``<|video_pad|>`` 151656 is also
    used by MiniMax-H3's own ref2va presentation per the reference's
    ``vision()`` helper) the pipe used for this block's run in ``token_ids``.
    """

    patches: torch.Tensor
    grid_thw: torch.Tensor
    pad_token_id: int


@dataclass
class MiniMaxH3Reference:
    """One `ref2va` reference as the CONDITIONER sees it, in packed order —
    :meth:`MiniMaxH3TextEncoder.encode_reference_request`'s input.

    Mirrors diffusers' `MiniMaxH3ImageReference`/`VideoReference`/
    `AudioReference` (``modular_pipelines/minimax_h3/references.py``) reduced
    to what the presentation actually branches on. Deliberately NOT the same
    record the generator pipe conditions on
    (``pipes/generator/video_minimax_h3/conditioning.ReferenceMedia``): that
    one carries PIL images, frame arrays and waveforms at the VAEs' rates,
    while this one carries only already-preprocessed float ``[0, 1]`` pixels
    at the vision tower's convention — and no waveform at all, because a
    waveform never reaches the conditioner.

    ``media``: ``[H, W, 3]`` for ``kind="image"``, ``[F, H, W, 3]`` at the
    conditioner's own frame rate for ``kind="video"``, ``None`` for
    ``kind="audio"``.

    ``has_audio``: whether this reference carries a soundtrack, which earns it
    an ``"<Audio j>: "`` label and nothing else. True for every
    ``kind="audio"`` reference and for a ``kind="video"`` one whose source had
    sound; never for an image.
    """

    kind: str  # "image" | "video" | "audio"
    media: torch.Tensor | None = None
    has_audio: bool = False


class MiniMaxH3TextEncoder(NativeTextEncoder):
    """MiniMax-H3's Qwen3-VL-32B conditioner.

    Contract (``h3_architecture_dossier.md`` §C, diffusers'
    ``get_qwen3vl_prompt_embeds`` — Apache-2.0): the tokenized presentation
    verbatim, ``add_special_tokens=False``, NO chat template, NO system
    prompt; an all-real (no padding, no truncation) attention mask; no
    pooling. Returns the UNNORMALIZED hidden state after decoder layer 49
    (``hidden_states[50]`` — see ``MINIMAX_H3_TEXT_ENCODER_LAYER_INDEX``'s
    docstring for the indexing derivation), never the final norm.

    ``encode(texts)`` is the plain t2va path (no vision). ``encode_presentation``
    is the general entry point a generation pipe drives for fl2va/ref2va: a
    pre-tokenized presentation (raw prompt, or label + vision-markup tokens
    the pipe built per keyframe/reference, in packed order) plus the matching
    :class:`MiniMaxH3VisionRun` list. ``encode_request`` builds `fl2va`'s
    presentation (keyframes, hardcoded ``"<Picture i>: "`` labels);
    ``encode_reference_request`` builds `ref2va`'s from an ordered
    :class:`MiniMaxH3Reference` list, numbering ``"<Picture i>: "``/``"<Video
    k>: "``/``"<Audio j>: "`` per MODALITY itself -- see its docstring for why
    the two don't share one method. Splice + DeepStack injection mechanics
    are shared with Krea-2's
    ``Qwen3VLTextEncoder._encode_with_images`` (same Qwen3-VL family); what
    differs is the tower's TOP-LEVEL attachment point (``module.visual``, not
    ``module.model.visual``), the raw untemplated tokenization, and the fixed
    layer-49 output tap.
    """

    role = "qwen3vl_32b"

    def __init__(self, module: Qwen3Model, tokenizer, variant: str = "qwen3vl_32b",
                 device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self.role = variant
        self._device = torch.device(device)
        self._has_vision = hasattr(module, "visual")

    def to(self, device: str | torch.device) -> "MiniMaxH3TextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        """Plain t2va encode: one raw prompt, no vision blocks."""
        if len(texts) != 1:
            raise ValueError("MiniMax-H3 packs one request into one sequence, so encode() takes exactly one prompt")
        token_ids = self.tokenizer(texts[0])
        return self.encode_presentation(token_ids)

    @torch.inference_mode()
    def encode_presentation(
        self, token_ids: list[int], vision_runs: list[MiniMaxH3VisionRun] | None = None,
    ) -> dict[str, torch.Tensor]:
        if vision_runs and not self._has_vision:
            raise NativeEngineUnsupportedError(
                "this minimax-h3 text encoder has no vision tower loaded; "
                "request the vision-enabled variant at load time (load_text_encoder(..., vision=True))"
            )
        if not vision_runs:
            ids = torch.tensor([token_ids], dtype=torch.long, device=self._device)
            stacked = self.module(
                ids, attention_mask=None,
                layers_to_extract=(MINIMAX_H3_TEXT_ENCODER_LAYER_INDEX,), capture="output",
            )  # [1, 1, S, 5120]
            return {"context": stacked.squeeze(1)}

        return self._encode_with_vision(token_ids, vision_runs)

    def _encode_with_vision(
        self, token_ids: list[int], vision_runs: list[MiniMaxH3VisionRun],
    ) -> dict[str, torch.Tensor]:
        ids = torch.tensor([token_ids], dtype=torch.long, device=self._device)
        text_embeds = self.module.model.embed_tokens(ids).to(torch.float32)  # [1, S, H]
        visual: Qwen3VLVisionTower = self.module.visual

        embed_pieces: list[torch.Tensor] = []
        spans: list[tuple[int, int, torch.Tensor]] = []
        deepstack_pieces: list[list[torch.Tensor]] = [[] for _ in visual.deepstack_indexes]
        cursor = 0
        for run in vision_runs:
            # ONE tower call per run, whatever its frame-group count: the
            # tower attends over the whole `grid_thw`, so splitting a video
            # reference into per-group calls would change its own attention,
            # not just the bookkeeping. The merged tokens are then scattered
            # across that run's `grid_t` SEPARATE placeholder runs, because
            # MiniMax-H3's presentation puts a `"<t seconds>"` text segment
            # between consecutive frame-groups. An image is `grid_t == 1` and
            # takes this loop exactly once.
            merged, deepstack_feats = visual(run.patches.to(self._device, dtype=torch.float32), run.grid_thw)
            num_blocks = int(run.grid_thw[0, 0])
            if merged.shape[0] % num_blocks:
                raise ValueError(
                    f"vision block produced {merged.shape[0]} merged token(s), which does not divide into the "
                    f"{num_blocks} frame-group(s) its grid_thw declares"
                )
            block_size = merged.shape[0] // num_blocks
            # Every frame-group is its own m-RoPE span, holding T constant and
            # tiling H/W exactly like a single image does.
            block_grid = torch.tensor(
                [[1, int(run.grid_thw[0, 1]), int(run.grid_thw[0, 2])]],
                device=run.grid_thw.device, dtype=run.grid_thw.dtype,
            )
            for block in range(num_blocks):
                span_start, span_len = _find_pad_run(token_ids, run.pad_token_id, cursor)
                embed_pieces.append(text_embeds[:, cursor:span_start])
                if block_size != span_len:
                    raise ValueError(
                        f"vision block produced {block_size} merged token(s) but its placeholder "
                        f"run in token_ids is {span_len} token(s) long"
                    )
                start = sum(p.shape[1] for p in embed_pieces)
                block_tokens = merged[block * block_size:(block + 1) * block_size]
                embed_pieces.append(block_tokens.unsqueeze(0).to(text_embeds.dtype))
                spans.append((start, block_size, block_grid))
                cursor = span_start + span_len

            # DeepStack features are per merged token in the SAME order the
            # blocks were spliced in, so they concatenate once per run rather
            # than once per block.
            for j, feat in enumerate(deepstack_feats):
                deepstack_pieces[j].append(feat)
        embed_pieces.append(text_embeds[:, cursor:])

        embeds = torch.cat(embed_pieces, dim=1)
        # Position-id CONSTRUCTION is shared with Krea-2/Qwen2.5-VL (see this
        # module's header docstring) — one span per vision block, in order,
        # works identically whether the block is a keyframe (fl2va) or one
        # merged frame-group of a reference video (ref2va): each individually
        # holds T constant and tiles H/W, exactly like a single image.
        position_ids = qwen25vl_mrope_position_ids(spans, embeds.shape[1], embeds.device)

        visual_pos_mask = embeds.new_zeros(embeds.shape[:2], dtype=torch.bool)
        for start, size, _grid in spans:
            visual_pos_mask[:, start:start + size] = True
        deepstack_embeds = [torch.cat(pieces, dim=0) for pieces in deepstack_pieces]

        stacked = self.module(
            None, attention_mask=None,
            layers_to_extract=(MINIMAX_H3_TEXT_ENCODER_LAYER_INDEX,), capture="output",
            inputs_embeds=embeds, position_ids=position_ids,
            deepstack_embeds=deepstack_embeds, visual_pos_mask=visual_pos_mask,
        )
        return {"context": stacked.squeeze(1)}

    def encode_request(
        self, text: str, images: list[torch.Tensor] | None = None,
        min_pixels: int = H3_VISION_MIN_PIXELS, max_pixels: int = H3_VISION_MAX_PIXELS,
    ) -> dict[str, torch.Tensor]:
        r"""
        Build MiniMax-H3's `fl2va` presentation for one request and encode it.

        The high-level entry point ``src.pipelines.pipes.model_loader.minimax_h3.
        clip.MiniMaxH3TextEncoderContract`` is written against: the prompt
        verbatim, preceded by one ``"<Picture i>: "`` label + vision block per
        keyframe image (in the order given — the pipe's own keyframe order,
        "first" before "last"), matching diffusers'
        ``MiniMaxH3FL2VATextEncoderStep`` (Apache-2.0). With no images this is
        the plain t2va presentation (identical to :meth:`encode`, plus
        `token_tags`).

        ``images``, when given, are already-preprocessed ``[H, W, 3]`` float
        ``[0, 1]`` tensors — the SAME convention Krea-2's
        ``Qwen3VLTextEncoder.encode(images=...)`` uses (this module never
        does PIL/CHW normalization or resizing beyond the vision tower's own
        smart-resize; that is the calling pipe's job). Unlike Krea-2's
        edit-mode encode, ``grounding_px=0`` (no long-side pre-cap) is passed
        to :func:`~.qwen3_vl_vision.preprocess_qwen3_vl_image` — the
        Krea-2-specific 768px training-jitter cap has no reference basis for
        H3's keyframes, whose reference (``encoders.py``) preprocesses through
        the plain HF ``Qwen3VLImageProcessor`` smart-resize, uncapped.

        ``min_pixels``/``max_pixels`` default to :data:`~.qwen3_vl_vision.
        H3_VISION_MIN_PIXELS`/:data:`~.qwen3_vl_vision.H3_VISION_MAX_PIXELS`
        — H3's own smart-resize bounds (`text_encoder/preprocessor_config.json`
        ``size.shortest_edge``/``size.longest_edge``), NOT
        ``preprocess_qwen3_vl_image``'s own defaults, which are Krea-2's 4B
        checkpoint's (smaller) bounds and would be silently wrong here.
        Exposed as parameters (not hardcoded) so a caller can override.

        Returns ``{"context": [1, num_text_tokens, 5120], "token_tags":
        [num_text_tokens] long}`` — see :data:`MINIMAX_H3_VIDEO_TAG`'s
        docstring for the exact per-row tag derivation.
        """
        if not images:
            token_ids = self.tokenizer(text)
            out = self.encode_presentation(token_ids)
            out["token_tags"] = torch.full(
                (len(token_ids),), MINIMAX_H3_TEXT_TAG, dtype=torch.long, device=out["context"].device,
            )
            return out
        if not self._has_vision:
            raise NativeEngineUnsupportedError(
                "this minimax-h3 text encoder has no vision tower loaded; "
                "request the vision-enabled variant at load time (load_text_encoder(..., vision=True))"
            )

        visual: Qwen3VLVisionTower = self.module.visual
        vision_start_id = self.tokenizer.convert_tokens_to_ids("<|vision_start|>")
        image_pad_id = self.tokenizer.convert_tokens_to_ids("<|image_pad|>")
        vision_end_id = self.tokenizer.convert_tokens_to_ids("<|vision_end|>")

        token_ids: list[int] = []
        token_tags: list[int] = []
        vision_runs: list[MiniMaxH3VisionRun] = []
        for index, image in enumerate(images):
            label_ids = self.tokenizer(f"<Picture {index + 1}>: ")
            patches, grid_thw = preprocess_qwen3_vl_image(
                image.to(self._device), grounding_px=0,
                min_pixels=min_pixels, max_pixels=max_pixels,
                patch_size=visual.patch_size,
                temporal_patch_size=visual.patch_embed.temporal_patch_size,
                merge_size=visual.spatial_merge_size,
            )
            num_image_tokens = int(grid_thw[0].prod()) // (visual.spatial_merge_size ** 2)
            vision_ids = [vision_start_id] + [image_pad_id] * num_image_tokens + [vision_end_id]

            token_ids += label_ids + vision_ids
            token_tags += [MINIMAX_H3_TEXT_TAG] * len(label_ids) + [MINIMAX_H3_VIDEO_TAG] * len(vision_ids)
            vision_runs.append(MiniMaxH3VisionRun(patches=patches, grid_thw=grid_thw, pad_token_id=image_pad_id))

        prompt_ids = self.tokenizer(text)
        token_ids += prompt_ids
        token_tags += [MINIMAX_H3_TEXT_TAG] * len(prompt_ids)

        out = self.encode_presentation(token_ids, vision_runs=vision_runs)
        out["token_tags"] = torch.tensor(token_tags, dtype=torch.long, device=out["context"].device)
        return out

    def encode_reference_request(
        self, text: str, references: list["MiniMaxH3Reference"],
        min_pixels: int = H3_VISION_MIN_PIXELS, max_pixels: int = H3_VISION_MAX_PIXELS,
        video_fps: float = MINIMAX_H3_VIDEO_FPS, video_sample_fps: float = MINIMAX_H3_VIDEO_SAMPLE_FPS,
    ) -> dict[str, torch.Tensor]:
        r"""
        Build MiniMax-H3's `ref2va` presentation for one request's references
        -- images, videos and audio -- and encode it.

        Port of diffusers' ``MiniMaxH3Ref2VATextEncoderStep._build_presentation``
        (Apache-2.0). `references` is the ordered list of
        :class:`MiniMaxH3Reference` in the SAME packed order that fixes each
        reference's position on `layout.build_ref2va_packed_sequence`'s shared
        rotary clock -- a different order is a different request. Per kind:

        ``"image"`` -- ``"<Picture i>: "`` and one ``<|image_pad|>`` vision
        block, exactly as :meth:`encode_request` builds a keyframe's.

        ``"video"`` -- ``"<Video k>: "``, then one ``<|video_pad|>`` block per
        merged frame-group, each PRECEDED by its own
        ``f"<{timestamp:.1f} seconds>"`` text. The frames must already be
        normalized to `video_fps` (`conditioning.normalize_reference_video`);
        the conditioner then reads them at `video_sample_fps` -- far coarser
        than what the video VAE sees of the same reference.

        ``"audio"`` -- ``"<Audio j>: "`` and nothing else. A waveform never
        reaches the conditioner; the sound is conditioned on through the audio
        VAE's rows in the packed sequence, not through this encoder.

        **The labels are numbered here, per MODALITY, not by the caller.**
        Three counters advance independently in packed order, and anything
        carrying sound -- a standalone ``"audio"`` reference AND a ``"video"``
        reference with a soundtrack -- takes an ``"<Audio j>: "`` off the same
        audio counter, emitted BEFORE its ``"<Video k>: "`` so the labels
        mirror the order those rows are packed in. This used to be a caller-
        supplied flat ``labels`` list, which cannot express a video: its
        ``"<t seconds>"`` texts are INTERLEAVED between vision blocks and are
        derived from the frame-sampling only this method performs, and
        emitting ``"<Audio j>: "`` and ``"<Video k>: "`` as one concatenated
        string is a different token sequence at the BPE seam than the two
        separate tokenizer calls the reference makes.

        Pixels are float ``[0, 1]``: ``[H, W, 3]`` for an image, ``[F, H, W,
        3]`` for a video -- the same convention :meth:`encode_request` uses.

        Returns ``{"context": [1, num_text_tokens, 5120], "token_tags":
        [num_text_tokens] long}``, the same shape :meth:`encode_request`
        returns.
        """
        for index, reference in enumerate(references):
            if reference.kind not in ("image", "video", "audio"):
                raise ValueError(
                    f"references[{index}] must be 'image', 'video' or 'audio', got {reference.kind!r}"
                )
        if not references:
            token_ids = self.tokenizer(text)
            out = self.encode_presentation(token_ids)
            out["token_tags"] = torch.full(
                (len(token_ids),), MINIMAX_H3_TEXT_TAG, dtype=torch.long, device=out["context"].device,
            )
            return out
        needs_vision = any(reference.kind in ("image", "video") for reference in references)
        if needs_vision and not self._has_vision:
            raise NativeEngineUnsupportedError(
                "this minimax-h3 text encoder has no vision tower loaded; "
                "request the vision-enabled variant at load time (load_text_encoder(..., vision=True))"
            )

        visual: Qwen3VLVisionTower | None = self.module.visual if self._has_vision else None
        vision_start_id = self.tokenizer.convert_tokens_to_ids("<|vision_start|>")
        image_pad_id = self.tokenizer.convert_tokens_to_ids("<|image_pad|>")
        video_pad_id = self.tokenizer.convert_tokens_to_ids("<|video_pad|>")
        vision_end_id = self.tokenizer.convert_tokens_to_ids("<|vision_end|>")

        token_ids: list[int] = []
        token_tags: list[int] = []
        vision_runs: list[MiniMaxH3VisionRun] = []

        def emit_text(value: str) -> None:
            segment = self.tokenizer(value)
            token_ids.extend(segment)
            token_tags.extend([MINIMAX_H3_TEXT_TAG] * len(segment))

        def emit_vision(pad_token_id: int, num_tokens: int) -> None:
            block = [vision_start_id] + [pad_token_id] * num_tokens + [vision_end_id]
            token_ids.extend(block)
            token_tags.extend([MINIMAX_H3_VIDEO_TAG] * len(block))

        counts = {"image": 0, "video": 0, "audio": 0}
        for reference in references:
            if reference.has_audio:
                counts["audio"] += 1
                emit_text(f"<Audio {counts['audio']}>: ")
            if reference.kind == "image":
                counts["image"] += 1
                emit_text(f"<Picture {counts['image']}>: ")
                patches, grid_thw = preprocess_qwen3_vl_image(
                    reference.media.to(self._device), grounding_px=0,
                    min_pixels=min_pixels, max_pixels=max_pixels,
                    patch_size=visual.patch_size,
                    temporal_patch_size=visual.patch_embed.temporal_patch_size,
                    merge_size=visual.spatial_merge_size,
                )
                emit_vision(image_pad_id, int(grid_thw[0].prod()) // (visual.spatial_merge_size ** 2))
                vision_runs.append(
                    MiniMaxH3VisionRun(patches=patches, grid_thw=grid_thw, pad_token_id=image_pad_id)
                )
            elif reference.kind == "video":
                counts["video"] += 1
                emit_text(f"<Video {counts['video']}>: ")
                temporal_patch = visual.patch_embed.temporal_patch_size
                indices, block_timestamps = _sample_reference_video_frames(
                    reference.media.shape[0], fps=video_fps, sample_fps=video_sample_fps,
                    temporal_patch=temporal_patch,
                )
                patches, grid_thw = preprocess_qwen3_vl_video(
                    reference.media[indices].to(self._device),
                    min_pixels=min_pixels, max_pixels=max_pixels,
                    patch_size=visual.patch_size, temporal_patch_size=temporal_patch,
                    merge_size=visual.spatial_merge_size,
                )
                if int(grid_thw[0, 0]) != len(block_timestamps):
                    raise ValueError(
                        f"a reference video merged into {int(grid_thw[0, 0])} vision block(s) but MiniMax-H3 "
                        f"labels {len(block_timestamps)} of them"
                    )
                tokens_per_block = (
                    int(grid_thw[0, 1]) * int(grid_thw[0, 2]) // (visual.spatial_merge_size ** 2)
                )
                for timestamp in block_timestamps:
                    emit_text(f"<{timestamp:.1f} seconds>")
                    emit_vision(video_pad_id, tokens_per_block)
                vision_runs.append(
                    MiniMaxH3VisionRun(patches=patches, grid_thw=grid_thw, pad_token_id=video_pad_id)
                )

        emit_text(text)

        out = self.encode_presentation(token_ids, vision_runs=vision_runs)
        out["token_tags"] = torch.tensor(token_tags, dtype=torch.long, device=out["context"].device)
        return out
