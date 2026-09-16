# Derived from: https://github.com/multimodal-art-projection/YuE src/yue2/modeling_yue2.py (Apache-2.0)

"""YuE2 backbone: 28 Qwen3-shaped layers, each with a second (NAR) attention
+ MLP path sharing the layer's residual stream — the AR path (causal,
KV-cached) drives token generation; the NAR path (bidirectional over a cached
AR prefix) drives the acoustic flow-matching velocity field.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ...attention import grouped_attention
from ...base import NativeArchModule
from ...text_encoders._functional import rms_norm
from .config import YuE2Config

_TIMESTEP_FREQ_DIM = 256


class RMSNorm(nn.Module):
    """RMS norm with an owned weight."""

    def __init__(self, dim: int, eps: float, device=None, dtype=None) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.empty(dim, device=device, dtype=dtype))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return rms_norm(x, self.weight, self.eps)


class GatedMLP(nn.Module):
    """SwiGLU MLP shared (same math, independent weights) by every AR/NAR path."""

    def __init__(self, hidden_size: int, intermediate_size: int, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.gate_proj = operations.Linear(hidden_size, intermediate_size, bias=False, device=device, dtype=dtype)
        self.up_proj = operations.Linear(hidden_size, intermediate_size, bias=False, device=device, dtype=dtype)
        self.down_proj = operations.Linear(intermediate_size, hidden_size, bias=False, device=device, dtype=dtype)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))


def _rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2:]
    return torch.cat((-x2, x1), dim=-1)


def _apply_rope(q: torch.Tensor, k: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor):
    org = q.dtype
    q_out = (q * cos) + (_rotate_half(q) * sin)
    k_out = (k * cos) + (_rotate_half(k) * sin)
    return q_out.to(org), k_out.to(org)


def _module_device(module: nn.Module) -> torch.device:
    for p in module.parameters():
        if p is not None:
            return p.device
    for b in module.buffers():
        if b is not None:
            return b.device
    raise RuntimeError(f"{module.__class__.__name__} has no tensors to read a device from")


class Attention(nn.Module):
    """GQA + per-head QK-norm + RoPE, with an explicit prefill/step/joint split."""

    def __init__(self, cfg: YuE2Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.num_heads = cfg.num_attention_heads
        self.num_kv_heads = cfg.num_key_value_heads
        self.head_dim = cfg.head_dim
        inner = self.num_heads * self.head_dim
        kv_inner = self.num_kv_heads * self.head_dim
        self.q_proj = operations.Linear(cfg.hidden_size, inner, bias=False, device=device, dtype=dtype)
        self.k_proj = operations.Linear(cfg.hidden_size, kv_inner, bias=False, device=device, dtype=dtype)
        self.v_proj = operations.Linear(cfg.hidden_size, kv_inner, bias=False, device=device, dtype=dtype)
        self.o_proj = operations.Linear(inner, cfg.hidden_size, bias=False, device=device, dtype=dtype)
        self.q_norm = RMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.k_norm = RMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)

    def project_qkv(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor):
        b, s, _ = x.shape
        q = self.q_norm(self.q_proj(x).view(b, s, self.num_heads, self.head_dim))
        k = self.k_norm(self.k_proj(x).view(b, s, self.num_kv_heads, self.head_dim))
        v = self.v_proj(x).view(b, s, self.num_kv_heads, self.head_dim)
        rc, rs = cos[None, :, None, :].to(q.dtype), sin[None, :, None, :].to(q.dtype)
        q, k = _apply_rope(q, k, rc, rs)
        return q, k, v

    def _attend(self, q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, mask: torch.Tensor | None) -> torch.Tensor:
        out = grouped_attention(q, k, v, heads=self.num_heads, kvheads=self.num_kv_heads, mask=mask, backend="sdpa")
        return out.transpose(1, 2).reshape(q.shape[0], -1, self.num_heads * self.head_dim)

    def prefill(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor,
                cache_k: torch.Tensor, cache_v: torch.Tensor) -> torch.Tensor:
        q, k, v = self.project_qkv(x, cos, sin)
        q, k, v = q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2)
        length = x.shape[1]
        cache_k[:, :, :length, :] = k.to(cache_k.dtype)
        cache_v[:, :, :length, :] = v.to(cache_v.dtype)
        causal = torch.full((length, length), float("-inf"), device=x.device, dtype=x.dtype).triu(1)
        return self.o_proj(self._attend(q, k, v, mask=causal))

    def step(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor,
              cache_k: torch.Tensor, cache_v: torch.Tensor, pos: int) -> torch.Tensor:
        q, k, v = self.project_qkv(x, cos, sin)
        q, k, v = q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2)
        cache_k[:, :, pos:pos + 1, :] = k.to(cache_k.dtype)
        cache_v[:, :, pos:pos + 1, :] = v.to(cache_v.dtype)
        k_all = cache_k[:, :, :pos + 1, :].to(q.dtype)
        v_all = cache_v[:, :, :pos + 1, :].to(q.dtype)
        return self.o_proj(self._attend(q, k_all, v_all, mask=None))

    def joint(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor,
              ar_k: torch.Tensor, ar_v: torch.Tensor) -> torch.Tensor:
        q, k, v = self.project_qkv(x, cos, sin)
        q, k, v = q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2)
        k = torch.cat((ar_k.to(k.dtype), k), dim=2)
        v = torch.cat((ar_v.to(v.dtype), v), dim=2)
        return self.o_proj(self._attend(q, k, v, mask=None))


class DecoderLayer(nn.Module):
    """One backbone layer: an AR (causal) path and a NAR (bidirectional) path,
    each with its own attention + MLP, sharing the layer's residual stream.
    """

    def __init__(self, cfg: YuE2Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.input_layernorm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.self_attn = Attention(cfg, operations, device=device, dtype=dtype)
        self.post_attention_layernorm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.mlp = GatedMLP(cfg.hidden_size, cfg.intermediate_size, operations, device=device, dtype=dtype)
        self.nar_input_layernorm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.nar_self_attn = Attention(cfg, operations, device=device, dtype=dtype)
        self.nar_pre_mlp_layernorm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.nar_mlp = GatedMLP(cfg.hidden_size, cfg.intermediate_size, operations, device=device, dtype=dtype)

    def prefill(self, x, cos, sin, cache_k, cache_v):
        x = x + self.self_attn.prefill(self.input_layernorm(x), cos, sin, cache_k, cache_v)
        x = x + self.mlp(self.post_attention_layernorm(x))
        return x

    def step(self, x, cos, sin, cache_k, cache_v, pos):
        x = x + self.self_attn.step(self.input_layernorm(x), cos, sin, cache_k, cache_v, pos)
        x = x + self.mlp(self.post_attention_layernorm(x))
        return x

    def nar_forward(self, x, cos, sin, ar_k, ar_v):
        x = x + self.nar_self_attn.joint(self.nar_input_layernorm(x), cos, sin, ar_k, ar_v)
        x = x + self.nar_mlp(self.nar_pre_mlp_layernorm(x))
        return x


class TimestepEmbedder(nn.Module):
    """Sinusoidal timestep -> SiLU MLP -> ``hidden_size``."""

    def __init__(self, hidden_size: int, operations, device=None, dtype=None, frequency_embedding_size: int = _TIMESTEP_FREQ_DIM) -> None:
        super().__init__()
        self.frequency_embedding_size = frequency_embedding_size
        self.mlp = nn.Sequential(
            operations.Linear(frequency_embedding_size, hidden_size, bias=True, device=device, dtype=dtype),
            nn.SiLU(),
            operations.Linear(hidden_size, hidden_size, bias=True, device=device, dtype=dtype),
        )

    def forward(self, t: torch.Tensor) -> torch.Tensor:
        half = self.frequency_embedding_size // 2
        freqs = torch.exp(-math.log(10000) * torch.arange(half, device=t.device, dtype=torch.float32) / half)
        args = t.float().unsqueeze(-1) * freqs.unsqueeze(0)
        emb = torch.cat([torch.cos(args), torch.sin(args)], dim=-1)
        return self.mlp(emb.to(next(self.parameters()).dtype))


class AudioPositionEmbedding(nn.Module):
    """Non-learnable 1D sinusoidal PE for latent frames — recomputed in
    :meth:`YuE2Model.post_load`, never trusted from a checkpoint buffer.
    """

    def __init__(self, max_frames: int, hidden_size: int, device=None) -> None:
        super().__init__()
        self.max_frames = max_frames
        self.hidden_size = hidden_size
        self.register_buffer("pe", self._compute(max_frames, hidden_size, device), persistent=False)

    @staticmethod
    def _compute(max_frames: int, hidden_size: int, device=None) -> torch.Tensor:
        pe = torch.zeros(max_frames, hidden_size, device=device)
        position = torch.arange(0, max_frames, dtype=torch.float32, device=device).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, hidden_size, 2, dtype=torch.float32, device=device) * (-math.log(10000.0) / hidden_size))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        return pe

    def recompute(self, device=None) -> None:
        self.pe = self._compute(self.max_frames, self.hidden_size, device if device is not None else self.pe.device)

    def forward(self, position_ids: torch.Tensor) -> torch.Tensor:
        return self.pe[position_ids]


@dataclass
class YuE2KVCache:
    """Preallocated per-layer KV cache, never resized."""

    keys: list[torch.Tensor]
    values: list[torch.Tensor]
    max_len: int
    filled_len: int = 0


@dataclass
class YuE2NarContext:
    """One acoustic window's cached AR prefix + the NAR side's RoPE/position embeddings."""

    ar_cache: list[tuple[torch.Tensor, torch.Tensor]]
    cos: torch.Tensor
    sin: torch.Tensor
    pos_emb: torch.Tensor
    nar_length: int


class Backbone(nn.Module):
    def __init__(self, cfg: YuE2Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.embed_tokens = operations.Embedding(cfg.vocab_size, cfg.hidden_size, device=device, dtype=dtype)
        self.layers = nn.ModuleList(
            [DecoderLayer(cfg, operations, device=device, dtype=dtype) for _ in range(cfg.num_hidden_layers)]
        )
        self.norm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.register_buffer(
            "inv_freq",
            1.0 / (cfg.rope_theta ** (torch.arange(0, cfg.head_dim, 2, dtype=torch.float32, device=device) / cfg.head_dim)),
            persistent=False,
        )

    def recompute_inv_freq(self) -> None:
        device = _module_device(self)
        self.inv_freq = 1.0 / (
            self.cfg.rope_theta ** (torch.arange(0, self.cfg.head_dim, 2, dtype=torch.float32, device=device) / self.cfg.head_dim)
        )

    def rope_at(self, positions: torch.Tensor, dtype: torch.dtype):
        freqs = torch.outer(positions.to(torch.float32), self.inv_freq.to(positions.device))
        emb = torch.cat((freqs, freqs), dim=-1)
        return emb.cos().to(dtype), emb.sin().to(dtype)


class YuE2Model(NativeArchModule):
    """The whole placement unit: backbone + lm_head + the NAR auxiliary heads."""

    def __init__(self, cfg: YuE2Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.model = Backbone(cfg, operations, device=device, dtype=dtype)
        self.lm_head = operations.Linear(cfg.hidden_size, cfg.vocab_size, bias=False, device=device, dtype=dtype)
        self.llm2vae = operations.Linear(cfg.hidden_size, cfg.latent_dim, bias=True, device=device, dtype=dtype)
        self.vae2llm = operations.Linear(cfg.latent_dim, cfg.hidden_size, bias=True, device=device, dtype=dtype)
        self.time_embedder = TimestepEmbedder(cfg.hidden_size, operations, device=device, dtype=dtype)
        self.latent_pos_embed = AudioPositionEmbedding(cfg.max_latent_frames, cfg.hidden_size, device=device)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "YuE2Model":
        return cls(YuE2Config.from_detect_config(config), operations)

    def post_load(self) -> None:
        self.model.recompute_inv_freq()
        self.latent_pos_embed.recompute(self.lm_head.weight.device)

    def new_kv_cache(self, max_len: int, batch: int = 1, device=None, dtype: torch.dtype = torch.bfloat16) -> YuE2KVCache:
        device = device or _module_device(self)
        keys = [
            torch.zeros(batch, self.cfg.num_key_value_heads, max_len, self.cfg.head_dim, device=device, dtype=dtype)
            for _ in range(self.cfg.num_hidden_layers)
        ]
        values = [torch.zeros_like(k) for k in keys]
        return YuE2KVCache(keys=keys, values=values, max_len=max_len)

    def prefill(self, input_ids: torch.Tensor, cache: YuE2KVCache) -> torch.Tensor:
        x = self.model.embed_tokens(input_ids)
        length = x.shape[1]
        cos, sin = self.model.rope_at(torch.arange(length, device=x.device), x.dtype)
        for i, layer in enumerate(self.model.layers):
            x = layer.prefill(x, cos, sin, cache.keys[i], cache.values[i])
        cache.filled_len = length
        return self.model.norm(x)

    def step(self, input_ids: torch.Tensor, cache: YuE2KVCache) -> torch.Tensor:
        x = self.model.embed_tokens(input_ids)
        pos = cache.filled_len
        cos, sin = self.model.rope_at(torch.tensor([pos], device=x.device), x.dtype)
        for i, layer in enumerate(self.model.layers):
            x = layer.step(x, cos, sin, cache.keys[i], cache.values[i], pos)
        cache.filled_len = pos + 1
        return self.model.norm(x)

    def lm_head_logits(self, hidden: torch.Tensor) -> torch.Tensor:
        return self.lm_head(hidden)

    def new_nar_context(self, ar_token_ids: torch.Tensor, num_latent_frames: int, nar_cond_end: int = 0) -> YuE2NarContext:
        if nar_cond_end < 0:
            raise ValueError("nar_cond_end must be nonnegative")
        ar_length = ar_token_ids.shape[1]
        visible_length = min(nar_cond_end, ar_length) if nar_cond_end else ar_length
        device = ar_token_ids.device
        cache = self.new_kv_cache(max_len=ar_length, batch=1, device=device, dtype=next(self.parameters()).dtype)
        self.prefill(ar_token_ids, cache)
        ar_cache = [(k[:, :, :visible_length, :], v[:, :, :visible_length, :]) for k, v in zip(cache.keys, cache.values)]
        nar_length = num_latent_frames + 2
        positions = torch.arange(ar_length, ar_length + nar_length, device=device)
        dtype = next(self.parameters()).dtype
        cos, sin = self.model.rope_at(positions, dtype)
        local = torch.arange(nar_length, device=device).clamp(max=self.cfg.max_latent_frames - 1)
        pos_emb = self.latent_pos_embed(local).unsqueeze(0).to(dtype)
        return YuE2NarContext(ar_cache=ar_cache, cos=cos, sin=sin, pos_emb=pos_emb, nar_length=nar_length)

    def shift_timestep(self, t_value: float, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
        t_sig = torch.sigmoid(torch.tensor(t_value, dtype=dtype, device=device))
        shift = self.cfg.timestep_shift
        return shift * t_sig / (1 + (shift - 1) * t_sig)

    def nar_velocity(self, context: YuE2NarContext, x_t: torch.Tensor, t_value: float) -> torch.Tensor:
        t_lat = x_t.shape[0]
        if t_lat != context.nar_length - 2:
            raise ValueError(f"x_t has {t_lat} frames, context expects {context.nar_length - 2}")
        device = x_t.device
        dtype = next(self.parameters()).dtype
        x_nar = F.pad(x_t.to(dtype), (0, 0, 1, 1))
        shifted = self.shift_timestep(t_value, device, dtype)
        x = self.vae2llm(x_nar.unsqueeze(0))
        x = x + self.time_embedder(shifted.expand(context.nar_length)).unsqueeze(0)
        x = x + context.pos_emb
        for layer, (ar_k, ar_v) in zip(self.model.layers, context.ar_cache):
            x = layer.nar_forward(x, context.cos, context.sin, ar_k, ar_v)
        return self.llm2vae(self.model.norm(x))[0, 1:-1]


__all__ = [
    "YuE2Model",
    "YuE2KVCache",
    "YuE2NarContext",
    "Backbone",
    "DecoderLayer",
    "Attention",
    "GatedMLP",
    "RMSNorm",
    "TimestepEmbedder",
    "AudioPositionEmbedding",
]
