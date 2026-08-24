# Derived from: diffusers `src/diffusers/models/transformers/
# transformer_minimax_music3.py` / `modular_pipelines/minimax_music3/encoders.py`
# (Apache-2.0, "Copyright 2026 The MiniMax Team and The HuggingFace Team") for
# the global-LLM architecture (standard Qwen3: GQA + per-head QK-norm + RoPE)
# and the per-frame KV-cached decode contract. Module names target the
# Comfy-Org single-file repack layout (`model.*`, `model.audio_decoder.*`,
# `model.audio_extra_embedding` — see the real headers in
# `ai/minimax_music3/`), not diffusers' own names.

"""``MiniMaxMusic3AudioLM`` — the 36-layer Qwen3-8B global LLM, its two
embedding-table layouts, its two lm_head layouts, and the attached RVQ depth
decoder + residual-code embedding table, wired as ONE ``NativeArchModule`` so
the model lifecycle manager places/evicts the whole AR core atomically (the
depth decoder runs on every frame right alongside the global LLM — splitting
them across devices would thrash exactly like the H3 mode-switch RAM OOM).

Both text-encoder layouts (see ``config.py``'s five independent booleans) are
built conditionally from the SAME class:

  * pruned — ``embed_tokens_prefill``/``embed_tokens_audio`` (two disjoint
    tables) + ``lm_head_pruned`` (16385-wide: index 0 is the stop token,
    index ``n`` is semantic code ``n-1``).
  * full — one ``embed_tokens`` table (text ids AND audio codes, the latter
    offset by ``AUDIO_CODE_OFFSET``) + ``lm_head`` (200000-wide: absolute id
    ``AUDIO_END_TOKEN_ID`` is the stop token, audio codes occupy
    ``[AUDIO_CODE_OFFSET, AUDIO_CODE_OFFSET+SEMANTIC_VOCAB_SIZE)`` and every
    other id is illegal here — see ``cfg_sampling.full_vocab_mask``).

KV cache: preallocated per layer, ``[2, num_key_value_heads, max_len,
head_dim]`` (batch 2 = the CFG conditional/unconditional rows, sharing every
weight) — never grown or concatenated (see :meth:`new_kv_cache`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn

from ...base import NativeArchModule
from ...text_encoders._functional import optimized_attention
from ._nn import GatedMLP, RMSNorm, apply_rope, module_device
from .config import MiniMaxMusic3TextEncoderConfig
from .depth_decoder import DepthDecoderModule
from .prompt import AUDIO_CODE_OFFSET, SEMANTIC_VOCAB_SIZE

# Full (un-pruned) checkpoint's `embed_tokens`/`lm_head` width — no named
# constant exists elsewhere (the pruned layout's widths ARE the prompt
# contract's own constants, see `_GlobalLM.__init__` below; the full
# checkpoint's 200000 is unrelated to any special-token id).
_FULL_VOCAB_SIZE = 200_000


@dataclass
class GlobalLMKVCache:
    """Preallocated per-layer KV cache. ``filled_len`` is how many positions
    (out of ``max_len``) are valid — advanced by exactly one per
    :meth:`MiniMaxMusic3AudioLM.prefill`/:meth:`~.step` call, never resized.
    """

    keys: list[torch.Tensor]
    values: list[torch.Tensor]
    max_len: int
    filled_len: int = 0


class _GlobalAttention(nn.Module):
    """GQA + per-head QK-norm + RoPE, with an explicit prefill/decode split
    (unlike ``text_encoders/qwen3.py``'s ``_Attention``, which only ever runs
    a full, uncached forward)."""

    def __init__(self, cfg: MiniMaxMusic3TextEncoderConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.num_heads = cfg.num_attention_heads
        self.num_kv_heads = cfg.num_key_value_heads
        self.head_dim = cfg.head_dim
        self._inner = self.num_heads * self.head_dim
        self._kv_inner = self.num_kv_heads * self.head_dim
        self.merged_qkv = cfg.merged_qkv
        if cfg.merged_qkv:
            self.qkv_proj = operations.Linear(
                cfg.hidden_size, self._inner + 2 * self._kv_inner, bias=False, device=device, dtype=dtype,
            )
        else:
            self.q_proj = operations.Linear(cfg.hidden_size, self._inner, bias=False, device=device, dtype=dtype)
            self.k_proj = operations.Linear(cfg.hidden_size, self._kv_inner, bias=False, device=device, dtype=dtype)
            self.v_proj = operations.Linear(cfg.hidden_size, self._kv_inner, bias=False, device=device, dtype=dtype)
        self.o_proj = operations.Linear(self._inner, cfg.hidden_size, bias=False, device=device, dtype=dtype)
        self.q_norm = RMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.k_norm = RMSNorm(self.head_dim, cfg.rms_norm_eps, device=device, dtype=dtype)

    def _qkv(self, x: torch.Tensor):
        b, s, _ = x.shape
        if self.merged_qkv:
            q, k, v = self.qkv_proj(x).split([self._inner, self._kv_inner, self._kv_inner], dim=-1)
        else:
            q, k, v = self.q_proj(x), self.k_proj(x), self.v_proj(x)
        q = self.q_norm(q.view(b, s, self.num_heads, self.head_dim).transpose(1, 2))
        k = self.k_norm(k.view(b, s, self.num_kv_heads, self.head_dim).transpose(1, 2))
        v = v.view(b, s, self.num_kv_heads, self.head_dim).transpose(1, 2)
        return q, k, v

    def prefill(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor,
                cache_k: torch.Tensor, cache_v: torch.Tensor) -> torch.Tensor:
        q, k, v = self._qkv(x)
        q, k = apply_rope(q, k, cos, sin)
        length = x.shape[1]
        cache_k[:, :, :length, :] = k.to(cache_k.dtype)
        cache_v[:, :, :length, :] = v.to(cache_v.dtype)
        rep = self.num_heads // self.num_kv_heads
        k_rep = k.repeat_interleave(rep, dim=1)
        v_rep = v.repeat_interleave(rep, dim=1)
        causal = torch.full((length, length), float("-inf"), device=x.device, dtype=x.dtype).triu(1)
        out = optimized_attention(q, k_rep, v_rep, self.num_heads, mask=causal, skip_reshape=True)
        return self.o_proj(out)

    def step(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor,
              cache_k: torch.Tensor, cache_v: torch.Tensor, pos: int) -> torch.Tensor:
        q, k, v = self._qkv(x)  # x is a single new position: seq len 1
        q, k = apply_rope(q, k, cos, sin)
        cache_k[:, :, pos:pos + 1, :] = k.to(cache_k.dtype)
        cache_v[:, :, pos:pos + 1, :] = v.to(cache_v.dtype)
        rep = self.num_heads // self.num_kv_heads
        # Attends over every filled position, itself included, unmasked: this
        # cache is write-once-per-position (never overwritten out of order),
        # so "everything filled so far" IS the causal prefix — no mask needed.
        k_all = cache_k[:, :, :pos + 1, :].to(q.dtype).repeat_interleave(rep, dim=1)
        v_all = cache_v[:, :, :pos + 1, :].to(q.dtype).repeat_interleave(rep, dim=1)
        out = optimized_attention(q, k_all, v_all, self.num_heads, mask=None, skip_reshape=True)
        return self.o_proj(out)


class _GlobalBlock(nn.Module):
    def __init__(self, cfg: MiniMaxMusic3TextEncoderConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.self_attn = _GlobalAttention(cfg, operations, device=device, dtype=dtype)
        self.mlp = GatedMLP(cfg.hidden_size, cfg.intermediate_size, cfg.merged_mlp, operations, device=device, dtype=dtype)
        self.input_layernorm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.post_attention_layernorm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)

    def prefill(self, x, cos, sin, cache_k, cache_v):
        x = x + self.self_attn.prefill(self.input_layernorm(x), cos, sin, cache_k, cache_v)
        x = x + self.mlp(self.post_attention_layernorm(x))
        return x

    def step(self, x, cos, sin, cache_k, cache_v, pos):
        x = x + self.self_attn.step(self.input_layernorm(x), cos, sin, cache_k, cache_v, pos)
        x = x + self.mlp(self.post_attention_layernorm(x))
        return x


class _GlobalLM(nn.Module):
    """``model.*`` — embeddings (either layout) + 36 blocks + final norm +
    lm_head (either layout) + the attached depth decoder + residual-code
    embedding table, all under this one prefix (matching the checkpoint)."""

    def __init__(self, cfg: MiniMaxMusic3TextEncoderConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        if cfg.pruned_embeddings:
            # `embed_tokens_prefill` covers every TEXT id [0, AUDIO_CODE_OFFSET)
            # (AUDIO_CODE_OFFSET is exactly the pruned text vocab size, not a
            # coincidence); `embed_tokens_audio` covers the SEMANTIC_VOCAB_SIZE
            # audio codes.
            self.embed_tokens_prefill = operations.Embedding(AUDIO_CODE_OFFSET, cfg.hidden_size, device=device, dtype=dtype)
            self.embed_tokens_audio = operations.Embedding(SEMANTIC_VOCAB_SIZE, cfg.hidden_size, device=device, dtype=dtype)
        else:
            self.embed_tokens = operations.Embedding(_FULL_VOCAB_SIZE, cfg.hidden_size, device=device, dtype=dtype)
        self.layers = nn.ModuleList([
            _GlobalBlock(cfg, operations, device=device, dtype=dtype) for _ in range(cfg.num_layers)
        ])
        self.norm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        if cfg.pruned_lm_head:
            # index 0 = stop token, index n = semantic code n-1 (see cfg_sampling.py).
            self.lm_head_pruned = operations.Linear(cfg.hidden_size, SEMANTIC_VOCAB_SIZE + 1, bias=False, device=device, dtype=dtype)
        else:
            self.lm_head = operations.Linear(cfg.hidden_size, _FULL_VOCAB_SIZE, bias=False, device=device, dtype=dtype)
        self.audio_decoder = DepthDecoderModule(cfg, operations, device=device, dtype=dtype)
        self.audio_extra_embedding = operations.Embedding(
            cfg.audio_vocab_size * (cfg.num_codebooks - 1), cfg.hidden_size, device=device, dtype=dtype,
        )
        # Non-persistent: never in the checkpoint, recomputed in post_load
        # (this engine's standing rotary-buffer rule).
        self.register_buffer("inv_freq", torch.empty(cfg.head_dim // 2), persistent=False)

    def recompute_inv_freq(self) -> None:
        half = torch.arange(0, self.cfg.head_dim, 2, dtype=torch.float32)
        self.inv_freq = (1.0 / (self.cfg.rope_theta ** (half / self.cfg.head_dim))).to(module_device(self))

    def rope_at(self, positions: torch.Tensor, dtype: torch.dtype):
        freqs = torch.outer(positions.to(torch.float32), self.inv_freq.to(positions.device))
        emb = torch.cat((freqs, freqs), dim=-1)
        cos = emb.cos()[None, None].to(dtype)
        sin = emb.sin()[None, None].to(dtype)
        return cos, sin


class MiniMaxMusic3AudioLM(NativeArchModule):
    """The AR core's whole placement unit — see the module docstring."""

    def __init__(self, cfg: MiniMaxMusic3TextEncoderConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.model = _GlobalLM(cfg, operations, device=device, dtype=dtype)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "MiniMaxMusic3AudioLM":
        return cls(MiniMaxMusic3TextEncoderConfig.from_detect_config(config), operations)

    def post_load(self) -> None:
        self.model.recompute_inv_freq()

    # -- embeddings / heads, layout-dispatched -----------------------------

    def embed_text(self, ids: torch.Tensor) -> torch.Tensor:
        """Returns the embedding table's own (module compute) dtype -- this
        feeds straight into ``prefill``'s transformer stack as ``x``, so it
        must match every layer's Linear weight dtype, never a hardcoded
        ``float32`` (see the diffusers reference's ``embed_tokens(text_ids)``,
        used as ``inputs_embeds`` with no cast)."""
        table = self.model.embed_tokens_prefill if self.cfg.pruned_embeddings else self.model.embed_tokens
        return table(ids)

    def embed_audio_code0(self, codes: torch.Tensor) -> torch.Tensor:
        """Same dtype contract as :meth:`embed_text` -- this feeds
        :meth:`step`'s ``inputs_embeds`` (via ``ar_loop._feedback_embedding``)
        and the depth decoder's ``projection`` directly."""
        if self.cfg.pruned_embeddings:
            return self.model.embed_tokens_audio(codes)
        return self.model.embed_tokens(codes + AUDIO_CODE_OFFSET)

    def lm_head_logits(self, hidden: torch.Tensor) -> torch.Tensor:
        head = self.model.lm_head_pruned if self.cfg.pruned_lm_head else self.model.lm_head
        return head(hidden)

    # -- KV cache + incremental decode --------------------------------------

    def new_kv_cache(self, max_len: int, batch: int = 2, device=None, dtype: torch.dtype = torch.bfloat16) -> GlobalLMKVCache:
        device = device or module_device(self)
        keys = [
            torch.zeros(batch, self.cfg.num_key_value_heads, max_len, self.cfg.head_dim, device=device, dtype=dtype)
            for _ in range(self.cfg.num_layers)
        ]
        values = [torch.zeros_like(k) for k in keys]
        return GlobalLMKVCache(keys=keys, values=values, max_len=max_len)

    def prefill(self, input_ids: torch.Tensor, cache: GlobalLMKVCache) -> torch.Tensor:
        """``input_ids``: ``[2, L]``. Returns the post-``norm`` hidden state
        for every position, ``[2, L, hidden]`` — the caller reads ``[:, -1]``."""
        x = self.embed_text(input_ids)
        length = x.shape[1]
        cos, sin = self.model.rope_at(torch.arange(length, device=x.device), x.dtype)
        for i, layer in enumerate(self.model.layers):
            x = layer.prefill(x, cos, sin, cache.keys[i], cache.values[i])
        cache.filled_len = length
        return self.model.norm(x)

    def step(self, inputs_embeds: torch.Tensor, cache: GlobalLMKVCache) -> torch.Tensor:
        """``inputs_embeds``: ``[2, 1, hidden]`` (a feedback embedding, not a
        token id — the AR loop never looks up ``embed_tokens`` after the
        prompt). Returns ``[2, 1, hidden]``, the post-``norm`` hidden state."""
        pos = cache.filled_len
        cos, sin = self.model.rope_at(torch.tensor([pos], device=inputs_embeds.device), inputs_embeds.dtype)
        x = inputs_embeds
        for i, layer in enumerate(self.model.layers):
            x = layer.step(x, cos, sin, cache.keys[i], cache.values[i], pos)
        cache.filled_len = pos + 1
        return self.model.norm(x)
