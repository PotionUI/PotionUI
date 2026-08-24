"""Gemma4-Unified-12B text encoder for LTX-2.5 conditioning.

Ported from transformers' ``Gemma4Unified*`` modeling code (Apache-2.0;
``modular_gemma4.py`` / ``modular_gemma4_unified.py``), the same family the
LTX-2.5 release TE (``gemma4-12b-with-proj-ltx-2.5-bf16.safetensors``) is built
from. Structural facts (checkpoint key layout, comfy-flat prefixes) were
additionally cross-checked against Lightricks/LTX-2's
``text_encoders/gemma/gemma_assets.py`` / ``encoder_configurator.py`` — those
files are community-licensed, so only key names/shapes were read from them,
never their code.

Gemma4-unified vs Gemma3 (see ``gemma3.py``):
  * RMS norm is a PLAIN ``x/rms(x) * weight`` (``Gemma3nRMSNorm``), NOT gemma3's
    ``weight + 1`` convention.
  * Attention has a THIRD per-head norm, ``v_norm``, applied to the value
    states (no rotary). It carries no learned scale (``with_scale=False`` in
    transformers -> no ``weight`` parameter, hence no checkpoint key) so it is
    a stateless RMS normalisation here, not an ``nn.Module``.
  * Per-layer ``layer_scalar``: a *persistent* ``torch.ones(1)`` buffer
    multiplied into the block's output right before it becomes the next
    layer's input. Unlike gemma3's non-persistent RoPE buffers, this one IS a
    real checkpoint key (``model.layers.N.layer_scalar``) and the sole
    reliable structural fact that a comfy-flat Gemma4 file carries that a
    comfy-flat Gemma3 file never does (see ``te_detect.py``).
  * Sliding-window layers (5 of every 6, "sliding_attention") keep gemma3's
    local-rope shape: full ``head_dim`` rotated, theta 1e4, no scaling. The
    6th ("full_attention") layer instead uses transformers' "proportional"
    RoPE: only the first ``partial_rotary_factor`` (0.25) fraction of the
    head is rotated (theta 1e6), the rest is zero-padded ("NoPE" — those
    channels are position-invariant) — and that layer's head_dim
    (``global_head_dim``) can differ from the sliding layers' (recovered from
    real shapes in ``loader._build_config``, never assumed equal). The last
    layer is ALWAYS full_attention too (transformers forces it), which only
    changes anything when ``num_hidden_layers`` isn't a multiple of 6.
  * ``attention_k_eq_v`` (ON in the shipped LTX-2.5 TE): on the full_attention
    layers only, K doubles as V — those layers carry NO ``v_proj`` key, and use
    their own (much smaller) ``num_global_key_value_heads`` for KV sizing
    instead of ``num_key_value_heads``. Sliding layers are unaffected.
  * Attention is UNSCALED (``scaling = 1.0`` in transformers, shared with
    gemma3n): the learned ``q_norm``/``k_norm`` carry the temperature, so the
    usual ``1/sqrt(head_dim)`` factor must NOT be applied. gemma3 differs
    (``query_pre_attn_scalar ** -0.5``).
  * ``normalize_in`` (embeddings scaled by ``sqrt(hidden)``) and the
    "layer=all" (num_layers+1 hidden states, un-normed inputs + final-normed
    output) encode contract are UNCHANGED from gemma3 — LTX's conditioning
    pipeline treats both TEs the same way downstream.
  * The unified variant (unlike dense Gemma4) has no per-layer input
    embeddings and no MoE block, so neither is implemented here — the LTX-2.5
    TE is exclusively the unified checkpoint.

Out of scope (not needed for the unified LTX-2.5 checkpoint, and unverifiable
without a real file): KV-layer sharing (``num_kv_shared_layers``) and the fused
double-wide MLP. Both default off in transformers' ``Gemma4UnifiedTextConfig``;
if a real checkpoint ever needs them this module will need extending, not
silently miscompute.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule
from ._functional import optimized_attention
from .base import NativeTextEncoder, _module_to, _module_unload

logger = logging.getLogger(__name__)

# transformers' sliding-window-pattern default (`sliding_window_pattern=6`):
# every 6th layer is full_attention, and the LAST layer always is too (forced
# even when the modulo pattern wouldn't put one there).
_SLIDING_WINDOW_PATTERN = 6


def is_global_layer(index: int, num_layers: int) -> bool:
    """True if layer ``index`` (of ``num_layers``) is "full_attention" (global)."""
    return ((index + 1) % _SLIDING_WINDOW_PATTERN == 0) or (index == num_layers - 1)


@dataclass
class Gemma4Config:
    hidden_size: int = 3840
    intermediate_size: int = 15360
    num_hidden_layers: int = 48
    vocab_size: int = 262144
    num_attention_heads: int = 16
    num_key_value_heads: int = 8
    num_global_key_value_heads: int = 1
    attention_k_eq_v: bool = True
    head_dim: int = 256
    global_head_dim: int = 256
    rms_norm_eps: float = 1e-6
    rope_theta_local: float = 10000.0
    rope_theta_global: float = 1000000.0
    global_partial_rotary_factor: float = 0.25
    sliding_window: int = 1024

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "Gemma4Config":
        head_dim = int(config.get("head_dim", 256))
        return cls(
            hidden_size=int(config["hidden_size"]),
            intermediate_size=int(config.get("intermediate_size", 15360)),
            num_hidden_layers=int(config["num_layers"]),
            vocab_size=int(config["vocab_size"]),
            num_attention_heads=int(config.get("num_attention_heads", 16)),
            num_key_value_heads=int(config.get("num_key_value_heads", 8)),
            num_global_key_value_heads=int(config.get("num_global_key_value_heads", 1)),
            attention_k_eq_v=bool(config.get("attention_k_eq_v", True)),
            head_dim=head_dim,
            # Falls back to the local head_dim when the checkpoint's single
            # full_attention layer wasn't found (e.g. an unrealistically tiny
            # config) -- never left unset.
            global_head_dim=int(config.get("global_head_dim", head_dim)),
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


def _rms_norm_weighted(x: torch.Tensor, weight: torch.Tensor, eps: float) -> torch.Tensor:
    """Plain ``x/rms(x) * weight`` (``Gemma3nRMSNorm``/``Gemma4RMSNorm``) — NOT gemma3's ``weight + 1``."""
    return F.rms_norm(x, weight.shape, weight=weight.to(dtype=x.dtype, device=x.device), eps=eps)


def _rms_norm_unscaled(x: torch.Tensor, dim: int, eps: float) -> torch.Tensor:
    """``v_norm``: same RMS normalisation, no learned scale (``with_scale=False`` upstream)."""
    return F.rms_norm(x, (dim,), weight=None, eps=eps)


class _Gemma4RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float, device=None, dtype=None) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.empty(dim, device=device, dtype=dtype))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return _rms_norm_weighted(x, self.weight, self.eps)


class _Gemma4Attention(nn.Module):
    def __init__(self, cfg: Gemma4Config, is_global: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.num_heads = cfg.num_attention_heads
        # `use_alternative_attention` upstream: k_eq_v applies to the
        # full_attention layers ONLY, and switches the KV head count too.
        self.k_eq_v = is_global and cfg.attention_k_eq_v
        self.num_kv_heads = cfg.num_global_key_value_heads if self.k_eq_v else cfg.num_key_value_heads
        self.head_dim = cfg.global_head_dim if is_global else cfg.head_dim
        self.eps = cfg.rms_norm_eps
        inner = self.num_heads * self.head_dim
        kv = self.num_kv_heads * self.head_dim
        self.q_proj = operations.Linear(cfg.hidden_size, inner, bias=False, device=device, dtype=dtype)
        self.k_proj = operations.Linear(cfg.hidden_size, kv, bias=False, device=device, dtype=dtype)
        self.v_proj = (
            None if self.k_eq_v
            else operations.Linear(cfg.hidden_size, kv, bias=False, device=device, dtype=dtype)
        )
        self.o_proj = operations.Linear(inner, cfg.hidden_size, bias=False, device=device, dtype=dtype)
        self.q_norm = _Gemma4RMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.k_norm = _Gemma4RMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)

    def forward(self, x, cos, sin, mask):
        b, s, _ = x.shape
        xq = self.q_proj(x).view(b, s, self.num_heads, self.head_dim).transpose(1, 2)
        xk = self.k_proj(x).view(b, s, self.num_kv_heads, self.head_dim).transpose(1, 2)
        # k_eq_v: V is the k_proj output BEFORE k_norm and BEFORE RoPE — only
        # v_norm is applied to it (upstream aliases `value_states = key_states`
        # ahead of both, and every step below is out-of-place).
        xv = xk if self.k_eq_v else self.v_proj(x).view(b, s, self.num_kv_heads, self.head_dim).transpose(1, 2)
        xq = self.q_norm(xq)
        xk = self.k_norm(xk)
        xq, xk = _apply_rope(xq, xk, cos, sin)
        xv = _rms_norm_unscaled(xv, self.head_dim, self.eps)
        xk = xk.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
        xv = xv.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
        out = optimized_attention(xq, xk, xv, self.num_heads, mask=mask, skip_reshape=True, scale=1.0)
        return self.o_proj(out)


class _Gemma4MLP(nn.Module):
    def __init__(self, cfg: Gemma4Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.gate_proj = operations.Linear(cfg.hidden_size, cfg.intermediate_size, bias=False, device=device, dtype=dtype)
        self.up_proj = operations.Linear(cfg.hidden_size, cfg.intermediate_size, bias=False, device=device, dtype=dtype)
        self.down_proj = operations.Linear(cfg.intermediate_size, cfg.hidden_size, bias=False, device=device, dtype=dtype)

    def forward(self, x):
        return self.down_proj(F.gelu(self.gate_proj(x), approximate="tanh") * self.up_proj(x))


class _Gemma4DecoderLayer(nn.Module):
    """4-norm block (input / post-attn / pre-ff / post-ff), same as gemma3, plus
    the trailing ``layer_scalar`` multiply gemma3 doesn't have."""

    def __init__(self, cfg: Gemma4Config, is_global: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.self_attn = _Gemma4Attention(cfg, is_global, operations, device=device, dtype=dtype)
        self.mlp = _Gemma4MLP(cfg, operations, device=device, dtype=dtype)
        eps = cfg.rms_norm_eps
        self.input_layernorm = _Gemma4RMSNorm(cfg.hidden_size, eps, device=device, dtype=dtype)
        self.post_attention_layernorm = _Gemma4RMSNorm(cfg.hidden_size, eps, device=device, dtype=dtype)
        self.pre_feedforward_layernorm = _Gemma4RMSNorm(cfg.hidden_size, eps, device=device, dtype=dtype)
        self.post_feedforward_layernorm = _Gemma4RMSNorm(cfg.hidden_size, eps, device=device, dtype=dtype)
        self.register_buffer("layer_scalar", torch.ones(1))

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
        x = residual + h
        return x * self.layer_scalar


class _Gemma4Transformer(nn.Module):
    def __init__(self, cfg: Gemma4Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.embed_tokens = operations.Embedding(cfg.vocab_size, cfg.hidden_size, device=device, dtype=dtype)
        self.is_global = [is_global_layer(i, cfg.num_hidden_layers) for i in range(cfg.num_hidden_layers)]
        self.layers = nn.ModuleList([
            _Gemma4DecoderLayer(cfg, g, operations, device=device, dtype=dtype) for g in self.is_global
        ])
        self.norm = _Gemma4RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.register_buffer("inv_freq_local", torch.empty(cfg.head_dim // 2), persistent=False)
        self.register_buffer("inv_freq_global", torch.empty(cfg.global_head_dim // 2), persistent=False)

    def recompute_inv_freq(self) -> None:
        cfg = self.cfg
        dev = self.embed_tokens.weight.device

        k = torch.arange(0, cfg.head_dim, 2, dtype=torch.float32)
        self.inv_freq_local = (1.0 / (cfg.rope_theta_local ** (k / cfg.head_dim))).to(dev)

        gd = cfg.global_head_dim
        rope_angles = int(cfg.global_partial_rotary_factor * gd // 2)
        kk = torch.arange(0, 2 * rope_angles, 2, dtype=torch.float32)
        inv_freq_rotated = 1.0 / (cfg.rope_theta_global ** (kk / gd))
        nope_angles = gd // 2 - rope_angles
        if nope_angles > 0:
            inv_freq_global = torch.cat((inv_freq_rotated, torch.zeros(nope_angles, dtype=torch.float32)))
        else:
            inv_freq_global = inv_freq_rotated
        self.inv_freq_global = inv_freq_global.to(dev)

    def _rope(self, inv_freq, seq_len, device, dtype):
        pos = torch.arange(seq_len, device=device, dtype=torch.float32)
        freqs = torch.outer(pos, inv_freq.to(device))
        emb = torch.cat((freqs, freqs), dim=-1)
        return emb.cos()[None, None].to(dtype), emb.sin()[None, None].to(dtype)

    def forward(self, input_ids, attention_mask):
        # normalize_in: scale embeddings by sqrt(hidden) — same as gemma3.
        x = self.embed_tokens(input_ids).to(torch.float32) * (self.cfg.hidden_size ** 0.5)
        b, s, _ = x.shape
        cos_l, sin_l = self._rope(self.inv_freq_local, s, x.device, x.dtype)
        cos_g, sin_g = self._rope(self.inv_freq_global, s, x.device, x.dtype)

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
            if self.is_global[i]:
                cos, sin, window = cos_g, sin_g, 0
            else:
                cos, sin, window = cos_l, sin_l, self.cfg.sliding_window
            x = layer(x, cos, sin, full_mask(window))
        collected.append(self.norm(x).unsqueeze(1))   # final-normed state
        return torch.cat(collected, dim=1)             # [B, num_layers+1, S, H]


class Gemma4Model(NativeArchModule):
    """Native arch module: ``model.*`` keys map 1:1 (vision/audio towers dropped)."""

    def __init__(self, cfg: Gemma4Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.model = _Gemma4Transformer(cfg, operations, device=device, dtype=dtype)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "Gemma4Model":
        return cls(Gemma4Config.from_dict(config), operations)

    def post_load(self) -> None:
        self.model.recompute_inv_freq()

    def forward(self, input_ids, attention_mask=None):
        return self.model(input_ids, attention_mask)


class Gemma4TextEncoder(NativeTextEncoder):
    """LTX-2.5 Gemma4-Unified-12B: same raw channel-major-stack contract as
    :class:`~.gemma3.Gemma3TextEncoder` — see that class's docstring for the
    ``188160``-style flattening and why normalisation/projection stay downstream.
    """

    role = "gemma4_12b"

    def __init__(self, module: Gemma4Model, tokenizer, variant: str = "gemma4_12b",
                 device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self.role = variant
        self._device = torch.device(device)

    def to(self, device: str | torch.device) -> "Gemma4TextEncoder":
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
        out = out.reshape(out.shape[0], out.shape[1], -1)    # [B, S, H*(L+1)]
        return {"context": out, "attention_mask": mask}
