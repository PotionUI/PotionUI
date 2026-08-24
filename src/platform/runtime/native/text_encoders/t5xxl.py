"""T5-XXL text encoder for Flux1.

Vendored from ComfyUI ``comfy/text_encoders/t5.py`` (encoder-only, XXL config).
Produces the Flux1 cross-attention context (last hidden state, 4096-dim). Matches
ComfyUI's Flux path: ``layer="last"`` and no padding attention mask
(``t5_attention_mask=False``), so the encoder attends over every position.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn

from ..base import NativeArchModule
from ._functional import cast_to_input, optimized_attention
from .base import NativeTextEncoder, _module_to, _module_unload

logger = logging.getLogger(__name__)


@dataclass
class T5Config:
    d_model: int = 4096
    d_kv: int = 64
    num_heads: int = 64
    d_ff: int = 10240
    num_layers: int = 24
    vocab_size: int = 32128
    eps: float = 1e-6
    # T5-XXL shares the relative-attention bias from block 0; UMT5 gives every
    # block its own (ComfyUI: `relative_attention` = model_type != "umt5").
    per_layer_bias: bool = False

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "T5Config":
        # d_kv/num_heads/d_ff overridable for tiny test models; detection never
        # supplies them, so production uses the T5-XXL defaults.
        return cls(
            d_model=int(config.get("hidden_size", 4096)),
            num_layers=int(config.get("num_layers", 24)),
            vocab_size=int(config.get("vocab_size", 32128)),
            d_kv=int(config.get("d_kv", 64)),
            num_heads=int(config.get("num_heads", 64)),
            d_ff=int(config.get("d_ff", 10240)),
            per_layer_bias=bool(config.get("per_layer_bias", False)),
        )

    @property
    def inner_dim(self) -> int:
        return self.d_kv * self.num_heads


def _gelu_tanh(a: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.gelu(a, approximate="tanh")


class _T5LayerNorm(nn.Module):
    def __init__(self, dim: int, eps: float, device=None, dtype=None) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.empty(dim, device=device, dtype=dtype))
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        variance = x.pow(2).mean(-1, keepdim=True)
        x = x * torch.rsqrt(variance + self.eps)
        return cast_to_input(self.weight, x) * x


class _T5DenseGatedActDense(nn.Module):
    def __init__(self, cfg: T5Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.wi_0 = operations.Linear(cfg.d_model, cfg.d_ff, bias=False, device=device, dtype=dtype)
        self.wi_1 = operations.Linear(cfg.d_model, cfg.d_ff, bias=False, device=device, dtype=dtype)
        self.wo = operations.Linear(cfg.d_ff, cfg.d_model, bias=False, device=device, dtype=dtype)

    def forward(self, x):
        return self.wo(_gelu_tanh(self.wi_0(x)) * self.wi_1(x))


class _T5LayerFF(nn.Module):
    def __init__(self, cfg: T5Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.DenseReluDense = _T5DenseGatedActDense(cfg, operations, device=device, dtype=dtype)
        self.layer_norm = _T5LayerNorm(cfg.d_model, cfg.eps, device=device, dtype=dtype)

    def forward(self, x):
        return x + self.DenseReluDense(self.layer_norm(x))


class _T5Attention(nn.Module):
    def __init__(self, cfg: T5Config, relative_attention_bias: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.q = operations.Linear(cfg.d_model, cfg.inner_dim, bias=False, device=device, dtype=dtype)
        self.k = operations.Linear(cfg.d_model, cfg.inner_dim, bias=False, device=device, dtype=dtype)
        self.v = operations.Linear(cfg.d_model, cfg.inner_dim, bias=False, device=device, dtype=dtype)
        self.o = operations.Linear(cfg.inner_dim, cfg.d_model, bias=False, device=device, dtype=dtype)
        self.num_heads = cfg.num_heads
        self.relative_attention_bias = None
        if relative_attention_bias:
            self.relative_attention_num_buckets = 32
            self.relative_attention_max_distance = 128
            self.relative_attention_bias = operations.Embedding(self.relative_attention_num_buckets, self.num_heads, device=device, dtype=dtype)

    @staticmethod
    def _relative_position_bucket(relative_position, num_buckets=32, max_distance=128):
        relative_buckets = 0
        num_buckets //= 2
        relative_buckets += (relative_position > 0).to(torch.long) * num_buckets
        relative_position = torch.abs(relative_position)
        max_exact = num_buckets // 2
        is_small = relative_position < max_exact
        relative_position_if_large = max_exact + (
            torch.log(relative_position.float() / max_exact)
            / math.log(max_distance / max_exact)
            * (num_buckets - max_exact)
        ).to(torch.long)
        relative_position_if_large = torch.min(
            relative_position_if_large, torch.full_like(relative_position_if_large, num_buckets - 1)
        )
        relative_buckets += torch.where(is_small, relative_position, relative_position_if_large)
        return relative_buckets

    def compute_bias(self, query_length, key_length, device, dtype):
        context_position = torch.arange(query_length, dtype=torch.long, device=device)[:, None]
        memory_position = torch.arange(key_length, dtype=torch.long, device=device)[None, :]
        relative_position = memory_position - context_position
        bucket = self._relative_position_bucket(
            relative_position, self.relative_attention_num_buckets, self.relative_attention_max_distance
        )
        values = self.relative_attention_bias(bucket).to(dtype)
        return values.permute([2, 0, 1]).unsqueeze(0).contiguous()

    def forward(self, x, mask, past_bias):
        q = self.q(x)
        k = self.k(x)
        v = self.v(x)
        if self.relative_attention_bias is not None:
            past_bias = self.compute_bias(x.shape[1], x.shape[1], x.device, x.dtype)
        if past_bias is not None:
            mask = past_bias if mask is None else mask + past_bias
        out = optimized_attention(q, k * ((k.shape[-1] / self.num_heads) ** 0.5), v, self.num_heads, mask)
        return self.o(out), past_bias


class _T5LayerSelfAttention(nn.Module):
    def __init__(self, cfg: T5Config, relative_attention_bias: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.SelfAttention = _T5Attention(cfg, relative_attention_bias, operations, device=device, dtype=dtype)
        self.layer_norm = _T5LayerNorm(cfg.d_model, cfg.eps, device=device, dtype=dtype)

    def forward(self, x, mask, past_bias):
        out, past_bias = self.SelfAttention(self.layer_norm(x), mask, past_bias)
        return x + out, past_bias


class _T5Block(nn.Module):
    def __init__(self, cfg: T5Config, relative_attention_bias: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.layer = nn.ModuleList()
        self.layer.append(_T5LayerSelfAttention(cfg, relative_attention_bias, operations, device=device, dtype=dtype))
        self.layer.append(_T5LayerFF(cfg, operations, device=device, dtype=dtype))

    def forward(self, x, mask, past_bias):
        x, past_bias = self.layer[0](x, mask, past_bias)
        x = self.layer[-1](x)
        return x, past_bias


class _T5Stack(nn.Module):
    def __init__(self, cfg: T5Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.block = nn.ModuleList(
            [_T5Block(cfg, relative_attention_bias=(cfg.per_layer_bias or i == 0),
                      operations=operations, device=device, dtype=dtype)
             for i in range(cfg.num_layers)]
        )
        self.final_layer_norm = _T5LayerNorm(cfg.d_model, cfg.eps, device=device, dtype=dtype)

    def forward(self, x, attention_mask=None):
        mask = None
        if attention_mask is not None:
            mask = 1.0 - attention_mask.to(x.dtype).reshape((attention_mask.shape[0], 1, -1, attention_mask.shape[-1])).expand(attention_mask.shape[0], 1, attention_mask.shape[-1], attention_mask.shape[-1])
            mask = mask.masked_fill(mask.to(torch.bool), -torch.finfo(x.dtype).max)
        past_bias = None
        for block in self.block:
            x, past_bias = block(x, mask, past_bias)
        return self.final_layer_norm(x)


class T5XXLModel(NativeArchModule):
    """T5-XXL encoder. Keys: ``shared.weight`` + ``encoder.*``."""

    def __init__(self, cfg: T5Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.shared = operations.Embedding(cfg.vocab_size, cfg.d_model, device=device, dtype=dtype)
        self.encoder = _T5Stack(cfg, operations, device=device, dtype=dtype)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "T5XXLModel":
        return cls(T5Config.from_dict(config), operations)

    def post_load(self) -> None:
        # No computed buffers (relative-position bias is a learned embedding).
        return None

    def forward(self, input_ids, attention_mask=None):
        # Gather then cast (exact for embeddings; fp32 TE activations like ComfyUI).
        x = self.shared(input_ids).to(torch.float32)
        return self.encoder(x, attention_mask=attention_mask)


class T5XXLTextEncoder(NativeTextEncoder):
    """Flux1 T5-XXL: prompts -> {"context": [B, S, 4096]}."""

    role = "t5xxl"

    def __init__(self, module: T5XXLModel, tokenizer, device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self._device = torch.device(device)

    def to(self, device: str | torch.device) -> "T5XXLTextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        ids, _mask = self.tokenizer(texts, device=self._device)
        # Flux1 runs T5 without a padding mask (t5_attention_mask=False).
        context = self.module(ids, attention_mask=None)
        return {"context": context}


class UMT5TextEncoder(NativeTextEncoder):
    """Wan UMT5-XXL: prompts -> {"context": [B, S, 4096], "attention_mask": [B, S]}.

    Uses the same T5 arch as :class:`T5XXLModel` (built with ``per_layer_bias=True``
    so every block owns its relative-attention bias). Unlike Flux1's T5, Wan runs
    UMT5 WITH the padding mask (ComfyUI ``enable_attention_masks=True``) and zeroes
    out the hidden states at masked positions (``zero_out_masked=True``).
    """

    role = "umt5_xxl"

    def __init__(self, module: T5XXLModel, tokenizer, device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self._device = torch.device(device)

    def to(self, device: str | torch.device) -> "UMT5TextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        ids, mask = self.tokenizer(texts, device=self._device)
        context = self.module(ids, attention_mask=mask)
        # zero_out_masked: pad positions contribute nothing downstream.
        context = context * mask.unsqueeze(-1).to(context.dtype)
        return {"context": context, "attention_mask": mask}
