"""CLIP-L text encoder for Flux1 (pooled vector only).

Vendored from ComfyUI ``comfy/clip_model.py`` (``CLIPTextModel_``). Flux1 uses
CLIP-L solely for its pooled vector (raw, pre-projection, read at the EOS token),
which feeds the DiT ``vector_in``. The full sequence output is discarded, so the
``text_projection`` layer is intentionally omitted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn

from ..base import NativeArchModule
from ._functional import optimized_attention
from .base import NativeTextEncoder, _module_to, _module_unload

logger = logging.getLogger(__name__)


@dataclass
class CLIPLConfig:
    hidden_size: int = 768
    num_hidden_layers: int = 12
    num_attention_heads: int = 12
    intermediate_size: int = 3072
    max_position_embeddings: int = 77
    vocab_size: int = 49408
    eps: float = 1e-5
    eos_token_id: int = 49407

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "CLIPLConfig":
        # heads/intermediate overridable for tiny test models; detection never
        # supplies them, so production uses the CLIP-L defaults.
        return cls(
            hidden_size=int(config.get("hidden_size", 768)),
            num_hidden_layers=int(config.get("num_layers", 12)),
            vocab_size=int(config.get("vocab_size", 49408)),
            num_attention_heads=int(config.get("num_attention_heads", 12)),
            intermediate_size=int(config.get("intermediate_size", 3072)),
        )


def _quick_gelu(a: torch.Tensor) -> torch.Tensor:
    return a * torch.sigmoid(1.702 * a)


class _CLIPAttention(nn.Module):
    def __init__(self, cfg: CLIPLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.heads = cfg.num_attention_heads
        dim = cfg.hidden_size
        self.q_proj = operations.Linear(dim, dim, bias=True, device=device, dtype=dtype)
        self.k_proj = operations.Linear(dim, dim, bias=True, device=device, dtype=dtype)
        self.v_proj = operations.Linear(dim, dim, bias=True, device=device, dtype=dtype)
        self.out_proj = operations.Linear(dim, dim, bias=True, device=device, dtype=dtype)

    def forward(self, x, mask):
        q = self.q_proj(x)
        k = self.k_proj(x)
        v = self.v_proj(x)
        out = optimized_attention(q, k, v, self.heads, mask)
        return self.out_proj(out)


class _CLIPMLP(nn.Module):
    def __init__(self, cfg: CLIPLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.fc1 = operations.Linear(cfg.hidden_size, cfg.intermediate_size, bias=True, device=device, dtype=dtype)
        self.fc2 = operations.Linear(cfg.intermediate_size, cfg.hidden_size, bias=True, device=device, dtype=dtype)

    def forward(self, x):
        return self.fc2(_quick_gelu(self.fc1(x)))


class _CLIPLayer(nn.Module):
    def __init__(self, cfg: CLIPLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.layer_norm1 = operations.LayerNorm(cfg.hidden_size, device=device, dtype=dtype)
        self.self_attn = _CLIPAttention(cfg, operations, device=device, dtype=dtype)
        self.layer_norm2 = operations.LayerNorm(cfg.hidden_size, device=device, dtype=dtype)
        self.mlp = _CLIPMLP(cfg, operations, device=device, dtype=dtype)

    def forward(self, x, mask):
        x = x + self.self_attn(self.layer_norm1(x), mask)
        x = x + self.mlp(self.layer_norm2(x))
        return x


class _CLIPEncoder(nn.Module):
    def __init__(self, cfg: CLIPLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.layers = nn.ModuleList([_CLIPLayer(cfg, operations, device=device, dtype=dtype) for _ in range(cfg.num_hidden_layers)])

    def forward(self, x, mask):
        for layer in self.layers:
            x = layer(x, mask)
        return x


class _CLIPEmbeddings(nn.Module):
    def __init__(self, cfg: CLIPLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.token_embedding = operations.Embedding(cfg.vocab_size, cfg.hidden_size, device=device, dtype=dtype)
        self.position_embedding = operations.Embedding(cfg.max_position_embeddings, cfg.hidden_size, device=device, dtype=dtype)

    def forward(self, input_tokens, dtype):
        pos = self.position_embedding.weight.to(dtype=dtype, device=input_tokens.device)
        # Gather then cast (exact for embeddings; any ops namespace).
        return self.token_embedding(input_tokens).to(dtype) + pos


class _CLIPTextModelInner(nn.Module):
    def __init__(self, cfg: CLIPLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.embeddings = _CLIPEmbeddings(cfg, operations, device=device, dtype=dtype)
        self.encoder = _CLIPEncoder(cfg, operations, device=device, dtype=dtype)
        self.final_layer_norm = operations.LayerNorm(cfg.hidden_size, device=device, dtype=dtype)

    def forward(self, input_tokens, eos_index):
        dtype = torch.float32
        x = self.embeddings(input_tokens, dtype=dtype)
        s = x.shape[1]
        causal = torch.full((s, s), -torch.finfo(x.dtype).max, dtype=x.dtype, device=x.device).triu_(1)
        x = self.encoder(x, causal)
        x = self.final_layer_norm(x)
        # Raw pooled (pre-projection) at the EOS token per row.
        pooled = x[torch.arange(x.shape[0], device=x.device), eos_index.to(x.device)]
        return pooled


class CLIPLModel(NativeArchModule):
    """CLIP-L encoder. Keys: ``text_model.*`` (no ``text_projection``)."""

    def __init__(self, cfg: CLIPLConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.text_model = _CLIPTextModelInner(cfg, operations, device=device, dtype=dtype)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "CLIPLModel":
        return cls(CLIPLConfig.from_dict(config), operations)

    def post_load(self) -> None:
        # No computed buffers (position embedding is learned).
        return None

    def forward(self, input_tokens, eos_index):
        return self.text_model(input_tokens, eos_index)


class CLIPLTextEncoder(NativeTextEncoder):
    """Flux1 CLIP-L: prompts -> {"pooled": [B, 768]}."""

    role = "clip_l"

    def __init__(self, module: CLIPLModel, tokenizer, device: str | torch.device = "cpu") -> None:
        self.module = module
        self.tokenizer = tokenizer
        self._device = torch.device(device)

    def to(self, device: str | torch.device) -> "CLIPLTextEncoder":
        self._device = torch.device(device)
        _module_to(self.module, device)
        return self

    def unload(self) -> None:
        _module_unload(self.module)

    @torch.inference_mode()
    def encode(self, texts: list[str]) -> dict[str, torch.Tensor]:
        ids, _mask, eos_index = self.tokenizer(texts, device=self._device)
        pooled = self.module(ids, eos_index)
        return {"pooled": pooled}
