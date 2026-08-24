"""Gemma3-12B text encoder for LTX-2 conditioning.

Vendored from ComfyUI ``comfy/text_encoders/llama.py`` (the ``Gemma3_12B`` config /
``gemma3`` transformer type) + ``comfy/text_encoders/lt.py`` (LTX conditioning).
Language-model only: the checkpoint's SigLIP ``vision_model.*`` and
``multi_modal_projector.*`` are stripped at load.

Gemma3 vs the Qwen/Llama family (see ``qwen3.py``):
  * FOUR per-block norms (input / post-attention / pre-feedforward /
    post-feedforward) and every RMS norm is ``rms_norm(x, weight + 1)``.
  * ``normalize_in``: the token embeddings are scaled by ``sqrt(hidden)``.
  * DUAL RoPE: a *global* rope (theta 1e6, scale 8) and a *local* rope (theta 1e4,
    scale 1). Sliding-window layers use the local rope; the every-6th global layer
    uses the global rope. This applies at every sequence length.
  * Sliding-window attention (window 1024) on 5 of every 6 layers — only bites when
    the sequence exceeds the window.
  * head_dim 256, GELU-tanh MLP, q/k RMS norm over head_dim.

LTX conditioning contract (``Gemma3TextEncoder.encode``):
  ComfyUI ``LTXAVTEModel`` runs Gemma with ``layer="all"`` (all 49 hidden states —
  the input to each of the 48 layers, un-normed, plus the final-normed output),
  stacks them, per-(batch, layer) min-max normalises to ``8·(x−mean)/(max−min+eps)``
  over the (seq, hidden) axes, and flattens to ``3840·49 = 188160``. That 188160-d
  sequence is the input to the LTX ``text_embedding_projection`` (+ embeddings
  connector).

  The projection + ``Embeddings1DConnector`` (×2, video/audio) live on the LTX DiT /
  generator side: arch-flux's ``LTXAVModel`` already constructs the
  connector modules, and their weights + the projection weights come from the
  **all-in-one LTX DiT checkpoint** — that is the source of truth for the exact
  topology (which differs 19b vs 2.3: 2.3 uses a dual projection 188160→4096/2048
  and an 8-layer/4096 connector; 19b uses a single 188160→3840 projection and a
  2-layer/3840 connector). The standalone ``ltx-2.3_text_projection`` and
  ``ltx-2-19b-embeddings_connector`` files are DIFFERENT versions and do NOT compose
  with each other — use the matching all-in-one checkpoint's own weights.

RoPE inv_freq (both global and local) are non-persistent buffers recomputed in
``post_load``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn

from ..base import NativeArchModule
from ._functional import optimized_attention, rms_norm
from .base import NativeTextEncoder, _module_to, _module_unload

logger = logging.getLogger(__name__)

# gemma3 sliding-window pattern: window 1024 on 5 of every 6 layers, global on the
# 6th (0 = global / no sliding). Also selects which rope a layer uses.
_SLIDING_PATTERN = (1024, 1024, 1024, 1024, 1024, 0)
# Number of hidden states ComfyUI's layer="all" collects for a 48-layer model:
# 48 layer inputs + 1 final-normed output.
GEMMA3_NUM_STATES = 49


@dataclass
class Gemma3Config:
    hidden_size: int = 3840
    intermediate_size: int = 15360
    num_hidden_layers: int = 48
    vocab_size: int = 262208
    num_attention_heads: int = 16
    num_key_value_heads: int = 8
    head_dim: int = 256
    rms_norm_eps: float = 1e-6
    rope_theta_global: float = 1000000.0
    rope_theta_local: float = 10000.0
    rope_scale_global: float = 8.0
    rope_scale_local: float = 1.0

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "Gemma3Config":
        return cls(
            hidden_size=int(config["hidden_size"]),
            intermediate_size=int(config.get("intermediate_size", 15360)),
            num_hidden_layers=int(config["num_layers"]),
            vocab_size=int(config["vocab_size"]),
            num_attention_heads=int(config.get("num_attention_heads", 16)),
            num_key_value_heads=int(config.get("num_key_value_heads", 8)),
            head_dim=int(config.get("head_dim", 256)),
        )


def _rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2 :]
    return torch.cat((-x2, x1), dim=-1)


def _apply_rope(xq, xk, cos, sin):
    org = xq.dtype
    q = (xq * cos) + (_rotate_half(xq) * sin)
    k = (xk * cos) + (_rotate_half(xk) * sin)
    return q.to(org), k.to(org)


class _GemmaRMSNorm(nn.Module):
    """gemma3 RMS norm: scale by ``(weight + 1)`` (``rms_norm_add``)."""

    def __init__(self, dim: int, eps: float, device=None, dtype=None) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.empty(dim, device=device, dtype=dtype))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return rms_norm(x, self.weight + 1.0, self.eps)


class _GemmaAttention(nn.Module):
    def __init__(self, cfg: Gemma3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.num_heads = cfg.num_attention_heads
        self.num_kv_heads = cfg.num_key_value_heads
        self.head_dim = cfg.head_dim
        inner = self.num_heads * self.head_dim
        kv = self.num_kv_heads * self.head_dim
        self.q_proj = operations.Linear(cfg.hidden_size, inner, bias=False, device=device, dtype=dtype)
        self.k_proj = operations.Linear(cfg.hidden_size, kv, bias=False, device=device, dtype=dtype)
        self.v_proj = operations.Linear(cfg.hidden_size, kv, bias=False, device=device, dtype=dtype)
        self.o_proj = operations.Linear(inner, cfg.hidden_size, bias=False, device=device, dtype=dtype)
        self.q_norm = _GemmaRMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.k_norm = _GemmaRMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)

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


class _GemmaMLP(nn.Module):
    def __init__(self, cfg: Gemma3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.gate_proj = operations.Linear(cfg.hidden_size, cfg.intermediate_size, bias=False, device=device, dtype=dtype)
        self.up_proj = operations.Linear(cfg.hidden_size, cfg.intermediate_size, bias=False, device=device, dtype=dtype)
        self.down_proj = operations.Linear(cfg.intermediate_size, cfg.hidden_size, bias=False, device=device, dtype=dtype)

    def forward(self, x):
        return self.down_proj(torch.nn.functional.gelu(self.gate_proj(x), approximate="tanh") * self.up_proj(x))


class _GemmaBlock(nn.Module):
    """gemma3 block: 4 norms (input / post-attn / pre-ff / post-ff)."""

    def __init__(self, cfg: Gemma3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.self_attn = _GemmaAttention(cfg, operations, device=device, dtype=dtype)
        self.mlp = _GemmaMLP(cfg, operations, device=device, dtype=dtype)
        eps = cfg.rms_norm_eps
        self.input_layernorm = _GemmaRMSNorm(cfg.hidden_size, eps, device=device, dtype=dtype)
        self.post_attention_layernorm = _GemmaRMSNorm(cfg.hidden_size, eps, device=device, dtype=dtype)
        self.pre_feedforward_layernorm = _GemmaRMSNorm(cfg.hidden_size, eps, device=device, dtype=dtype)
        self.post_feedforward_layernorm = _GemmaRMSNorm(cfg.hidden_size, eps, device=device, dtype=dtype)

    def forward(self, x, cos, sin, mask):
        residual = x
        h = self.input_layernorm(x)
        h = self.self_attn(h, cos, sin, mask)
        h = self.post_attention_layernorm(h)
        x = residual + h
        residual = x
        h = self.pre_feedforward_layernorm(x)
        h = self.mlp(h)
        h = self.post_feedforward_layernorm(h)
        return residual + h


class _Gemma3Transformer(nn.Module):
    def __init__(self, cfg: Gemma3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.embed_tokens = operations.Embedding(cfg.vocab_size, cfg.hidden_size, device=device, dtype=dtype)
        self.layers = nn.ModuleList([_GemmaBlock(cfg, operations, device=device, dtype=dtype) for _ in range(cfg.num_hidden_layers)])
        self.norm = _GemmaRMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        half = cfg.head_dim // 2
        self.register_buffer("inv_freq_global", torch.empty(half), persistent=False)
        self.register_buffer("inv_freq_local", torch.empty(half), persistent=False)

    def recompute_inv_freq(self) -> None:
        cfg = self.cfg
        k = torch.arange(0, cfg.head_dim, 2, dtype=torch.float32)
        dev = self.embed_tokens.weight.device
        self.inv_freq_global = ((1.0 / (cfg.rope_theta_global ** (k / cfg.head_dim))) / cfg.rope_scale_global).to(dev)
        self.inv_freq_local = ((1.0 / (cfg.rope_theta_local ** (k / cfg.head_dim))) / cfg.rope_scale_local).to(dev)

    def _rope(self, inv_freq, seq_len, device, dtype):
        pos = torch.arange(seq_len, device=device, dtype=torch.float32)
        freqs = torch.outer(pos, inv_freq.to(device))
        emb = torch.cat((freqs, freqs), dim=-1)
        return emb.cos()[None, None].to(dtype), emb.sin()[None, None].to(dtype)

    def forward(self, input_ids, attention_mask):
        # gemma3 normalize_in: scale embeddings by sqrt(hidden).
        x = self.embed_tokens(input_ids).to(torch.float32) * (self.cfg.hidden_size ** 0.5)
        b, s, _ = x.shape
        cos_g, sin_g = self._rope(self.inv_freq_global, s, x.device, x.dtype)
        cos_l, sin_l = self._rope(self.inv_freq_local, s, x.device, x.dtype)

        base_mask = None
        if attention_mask is not None:
            m = 1.0 - attention_mask.to(x.dtype).reshape((b, 1, -1, s)).expand(b, 1, s, s)
            base_mask = m.masked_fill(m.to(torch.bool), torch.finfo(x.dtype).min / 4)
        causal = None
        if s > 1:
            causal = torch.empty(s, s, dtype=x.dtype, device=x.device).fill_(torch.finfo(x.dtype).min / 4).triu_(1)

        def full_mask(sliding_window):
            mask = causal if base_mask is None else (base_mask if causal is None else base_mask + causal)
            if sliding_window and s > sliding_window:
                sl = torch.full((s, s), torch.finfo(x.dtype).min / 4, dtype=x.dtype, device=x.device).tril_(diagonal=-sliding_window)
                mask = sl if mask is None else mask + sl
            return mask

        # layer="all": collect every layer's input (un-normed), then the final norm.
        collected: list[torch.Tensor] = []
        for i, layer in enumerate(self.layers):
            collected.append(x.unsqueeze(1))
            window = _SLIDING_PATTERN[i % len(_SLIDING_PATTERN)]
            if window:  # sliding layer -> LOCAL rope
                cos, sin = cos_l, sin_l
            else:       # global layer -> GLOBAL rope
                cos, sin = cos_g, sin_g
            x = layer(x, cos, sin, full_mask(window))
        collected.append(self.norm(x).unsqueeze(1))   # final-normed state
        return torch.cat(collected, dim=1)             # [B, num_layers+1, S, H]


class Gemma3Model(NativeArchModule):
    """Native arch module: ``model.*`` keys map 1:1 (vision tower dropped)."""

    def __init__(self, cfg: Gemma3Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.model = _Gemma3Transformer(cfg, operations, device=device, dtype=dtype)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "Gemma3Model":
        return cls(Gemma3Config.from_dict(config), operations)

    def post_load(self) -> None:
        self.model.recompute_inv_freq()

    def forward(self, input_ids, attention_mask=None):
        return self.model(input_ids, attention_mask)


class Gemma3TextEncoder(NativeTextEncoder):
    """LTX-2 Gemma3-12B: prompts -> {"context": [B, S, 188160], "attention_mask": [B, S]}.

    ``188160 = hidden(3840) * 49`` — the RAW flattened (channel-major) stack of all
    49 hidden states. Normalisation + ``text_embedding_projection`` are applied
    downstream by ``LTXAVModel.apply_text_conditioning`` (they differ per variant).
    """

    role = "gemma3_12b"

    def __init__(self, module: Gemma3Model, tokenizer, variant: str = "gemma3_12b",
                 device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self.role = variant
        self._device = torch.device(device)

    def to(self, device: str | torch.device) -> "Gemma3TextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        ids, mask = self.tokenizer(texts, device=self._device)
        stacked = self.module(ids, attention_mask=mask)      # [B, L+1, S, H]
        out = stacked.movedim(1, -1)                         # [B, S, H, L+1]
        # RAW channel-major stack — normalisation is variant-specific (19b min-max
        # vs 2.3 per-token RMS) and lives in LTXAVModel.apply_text_conditioning.
        out = out.reshape(out.shape[0], out.shape[1], -1)    # [B, S, H*(L+1)]
        return {"context": out, "attention_mask": mask}
