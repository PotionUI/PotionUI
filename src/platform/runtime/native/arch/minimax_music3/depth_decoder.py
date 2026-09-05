# Derived from: diffusers `src/diffusers/models/transformers/
# minimax_music3_rvq_depth_decoder.py` (Apache-2.0, "Copyright 2026 The
# MiniMax Team and The HuggingFace Team") for the depth-decoder block
# structure and per-step sampling recipe. Module names target the Comfy-Org
# single-file repack layout (`model.audio_decoder.*` — see ``te_detect.py``
# and the real headers in ``ai/minimax_music3/``), not diffusers' own names.

"""The 4-layer RVQ depth decoder: samples the 7 residual audio codes
``c1..c7`` for one AR frame, given that frame's already-sampled semantic code
``c0`` and the global LLM's hidden state.

The sequence never exceeds :data:`MAX_DEPTH_TOKENS` positions, so the cache
here is a fixed 8-slot buffer per layer, allocated once per song and reset per
frame rather than grown (:class:`DepthKVCache`). Without it each of the 7
codebooks re-runs the whole stack over the whole prefix — 2+3+...+8 = 35 token
rows per frame against the cached path's 2 (prefill) + 6 (steps) = 8.

The uncached whole-prefix forward is retained and is what
:func:`generate_depth_codes` runs when passed no cache: it is the reference
the cached path is tested against, and the only path that needs no per-frame
state.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import torch
import torch.nn as nn

from ...text_encoders._functional import optimized_attention
from ._nn import GatedMLP, RMSNorm
from .cfg_sampling import guided_top_k_sample_id
from .config import MiniMaxMusic3TextEncoderConfig

NUM_RESIDUAL_CODEBOOKS = 7
MAX_DEPTH_TOKENS = 1 + NUM_RESIDUAL_CODEBOOKS  # llm hidden + c0, then one feedback token per c1..c6.
_MAX_DEPTH_POSITIONS = 16  # `pos_embedding` table width; the loop never uses past index 7.


@dataclass
class DepthKVCache:
    """One frame's depth-decoder KV state: per layer, ``[B, heads,
    MAX_DEPTH_TOKENS, head_dim]``, allocated on the first
    :meth:`DepthDecoderModule.forward` that is handed this cache and never
    grown after (the depth sequence is bounded by construction).

    ``reset()`` between frames keeps the buffers and rewinds ``filled_len`` —
    stale slots past ``filled_len`` are never read, since every position is
    written before it is attended to. ``release()`` drops the buffers and
    poisons the cache; :func:`depth_kv_cache` calls it on every exit path.
    """

    keys: list[torch.Tensor] = field(default_factory=list)
    values: list[torch.Tensor] = field(default_factory=list)
    filled_len: int = 0
    released: bool = False

    def reset(self) -> None:
        self.filled_len = 0

    def release(self) -> None:
        self.keys = []
        self.values = []
        self.filled_len = 0
        self.released = True

    def retained_bytes(self) -> int:
        return sum(t.numel() * t.element_size() for t in (*self.keys, *self.values))


@contextmanager
def depth_kv_cache() -> Iterator[DepthKVCache]:
    """A depth cache whose buffers are released on completion, cancellation
    and error alike — the AR loop holds one for the whole song and resets it
    per frame (:mod:`.ar_loop`).
    """
    cache = DepthKVCache()
    try:
        yield cache
    finally:
        cache.release()


class _DepthAttention(nn.Module):
    """Causal self-attention, 16 heads x head_dim 256, full MHA (no GQA, no
    q/k-norm, no RoPE — unlike the global LLM's attention, none of the three
    are present in the checkpoint for ``model.audio_decoder.*``)."""

    def __init__(self, cfg: MiniMaxMusic3TextEncoderConfig, merged_qkv: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.num_heads = cfg.decoder_num_heads
        self.head_dim = cfg.decoder_head_dim
        inner = self.num_heads * self.head_dim
        self.merged_qkv = merged_qkv
        if merged_qkv:
            self.qkv_proj = operations.Linear(cfg.hidden_size, 3 * inner, bias=False, device=device, dtype=dtype)
        else:
            self.q_proj = operations.Linear(cfg.hidden_size, inner, bias=False, device=device, dtype=dtype)
            self.k_proj = operations.Linear(cfg.hidden_size, inner, bias=False, device=device, dtype=dtype)
            self.v_proj = operations.Linear(cfg.hidden_size, inner, bias=False, device=device, dtype=dtype)
        self.o_proj = operations.Linear(inner, cfg.hidden_size, bias=False, device=device, dtype=dtype)

    def _qkv(self, x: torch.Tensor):
        b, s, _ = x.shape
        if self.merged_qkv:
            inner = self.num_heads * self.head_dim
            q, k, v = self.qkv_proj(x).split([inner, inner, inner], dim=-1)
        else:
            q, k, v = self.q_proj(x), self.k_proj(x), self.v_proj(x)
        q = q.view(b, s, self.num_heads, self.head_dim).transpose(1, 2)
        k = k.view(b, s, self.num_heads, self.head_dim).transpose(1, 2)
        v = v.view(b, s, self.num_heads, self.head_dim).transpose(1, 2)
        return q, k, v

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        q, k, v = self._qkv(x)
        out = optimized_attention(q, k, v, self.num_heads, mask=mask, skip_reshape=True)
        return self.o_proj(out)

    def prefill(self, x: torch.Tensor, cache_k: torch.Tensor, cache_v: torch.Tensor) -> torch.Tensor:
        q, k, v = self._qkv(x)
        length = x.shape[1]
        cache_k[:, :, :length, :] = k.to(cache_k.dtype)
        cache_v[:, :, :length, :] = v.to(cache_v.dtype)
        causal = torch.full((length, length), float("-inf"), device=x.device, dtype=x.dtype).triu(1)
        out = optimized_attention(q, k, v, self.num_heads, mask=causal, skip_reshape=True)
        return self.o_proj(out)

    def step(self, x: torch.Tensor, cache_k: torch.Tensor, cache_v: torch.Tensor, pos: int) -> torch.Tensor:
        q, k, v = self._qkv(x)  # x is a single new position: seq len 1
        cache_k[:, :, pos:pos + 1, :] = k.to(cache_k.dtype)
        cache_v[:, :, pos:pos + 1, :] = v.to(cache_v.dtype)
        # Unmasked over the filled prefix, for the same reason as the global
        # LM's own decode step (see `lm.py`): every position is written before
        # it is ever attended to, so "filled so far" IS the causal prefix.
        k_all = cache_k[:, :, :pos + 1, :].to(q.dtype)
        v_all = cache_v[:, :, :pos + 1, :].to(q.dtype)
        out = optimized_attention(q, k_all, v_all, self.num_heads, mask=None, skip_reshape=True)
        return self.o_proj(out)


class _DepthBlock(nn.Module):
    def __init__(self, cfg: MiniMaxMusic3TextEncoderConfig, merged_qkv: bool, merged_mlp: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.self_attn = _DepthAttention(cfg, merged_qkv, operations, device=device, dtype=dtype)
        self.mlp = GatedMLP(cfg.hidden_size, cfg.decoder_intermediate_size, merged_mlp, operations, device=device, dtype=dtype)
        self.input_layernorm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.post_attention_layernorm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        x = x + self.self_attn(self.input_layernorm(x), mask)
        x = x + self.mlp(self.post_attention_layernorm(x))
        return x

    def prefill(self, x: torch.Tensor, cache_k: torch.Tensor, cache_v: torch.Tensor) -> torch.Tensor:
        x = x + self.self_attn.prefill(self.input_layernorm(x), cache_k, cache_v)
        x = x + self.mlp(self.post_attention_layernorm(x))
        return x

    def step(self, x: torch.Tensor, cache_k: torch.Tensor, cache_v: torch.Tensor, pos: int) -> torch.Tensor:
        x = x + self.self_attn.step(self.input_layernorm(x), cache_k, cache_v, pos)
        x = x + self.mlp(self.post_attention_layernorm(x))
        return x


class DepthDecoderModule(nn.Module):
    """``model.audio_decoder.*`` — attached as a submodule of the global LM
    (see :mod:`.lm`) so the two share one placement unit; this class carries
    only the depth decoder's own weights and forward.
    """

    def __init__(self, cfg: MiniMaxMusic3TextEncoderConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.pos_embedding = operations.Embedding(_MAX_DEPTH_POSITIONS, cfg.hidden_size, device=device, dtype=dtype)
        self.projection = operations.Linear(cfg.hidden_size, cfg.hidden_size, bias=False, device=device, dtype=dtype)
        self.layers = nn.ModuleList([
            _DepthBlock(cfg, cfg.decoder_merged_qkv, cfg.decoder_merged_mlp, operations, device=device, dtype=dtype)
            for _ in range(cfg.decoder_num_layers)
        ])
        self.norm = RMSNorm(cfg.hidden_size, cfg.rms_norm_eps, device=device, dtype=dtype)
        self.audio_heads = nn.ModuleList([
            operations.Linear(cfg.hidden_size, cfg.audio_vocab_size, bias=False, device=device, dtype=dtype)
            for _ in range(NUM_RESIDUAL_CODEBOOKS)
        ])

    def _allocate_cache(self, cache: DepthKVCache, like: torch.Tensor) -> None:
        shape = (like.shape[0], self.cfg.decoder_num_heads, MAX_DEPTH_TOKENS, self.cfg.decoder_head_dim)
        cache.keys = [
            torch.zeros(shape, device=like.device, dtype=like.dtype) for _ in range(len(self.layers))
        ]
        cache.values = [torch.zeros_like(k) for k in cache.keys]

    def _embed(self, token_embeds: torch.Tensor, start: int) -> torch.Tensor:
        positions = torch.arange(start, start + token_embeds.shape[1], device=token_embeds.device)
        return token_embeds + self.pos_embedding(positions).unsqueeze(0).to(token_embeds.dtype)

    def forward(self, token_embeds: torch.Tensor, cache: DepthKVCache | None = None) -> torch.Tensor:
        """``token_embeds``: ``[B, T, hidden]`` (already ``projection()``-ed
        by the caller). Returns the post-``norm`` hidden state, same shape.

        With no ``cache``, ``token_embeds`` is the WHOLE prefix and the stack
        runs over all of it. With a ``cache``, ``token_embeds`` is only what is
        new: the 2-token prefill while the cache is empty, then one token per
        call, each attending over the cached prefix. Learned absolute position
        indices are continuous across both (``cache.filled_len`` is the next
        index either way), so the two paths embed identical positions.

        Kept as ONE entry point on purpose: ``optimizations/compile.py``
        compiles this module whole and dispatches through ``__call__``, so a
        separate public ``prefill``/``step`` pair would silently run eager
        under that compile.
        """
        if cache is None:
            x = self._embed(token_embeds, 0)
            t = token_embeds.shape[1]
            causal = torch.full((t, t), float("-inf"), device=x.device, dtype=x.dtype).triu(1)
            for layer in self.layers:
                x = layer(x, causal)
            return self.norm(x)

        if cache.released:
            raise RuntimeError("depth KV cache used after release")
        start = cache.filled_len
        if start + token_embeds.shape[1] > MAX_DEPTH_TOKENS:
            raise ValueError(
                f"depth KV cache overflow: {start} filled + {token_embeds.shape[1]} new "
                f"exceeds {MAX_DEPTH_TOKENS} positions"
            )
        if not cache.keys:
            self._allocate_cache(cache, token_embeds)
        x = self._embed(token_embeds, start)
        for i, layer in enumerate(self.layers):
            if start == 0:
                x = layer.prefill(x, cache.keys[i], cache.values[i])
            else:
                x = layer.step(x, cache.keys[i], cache.values[i], start)
        cache.filled_len = start + token_embeds.shape[1]
        return self.norm(x)


def generate_depth_codes(
    lm,
    llm_hidden: torch.Tensor,
    code0: torch.Tensor,
    generator: torch.Generator,
    cfg_scale: float,
    top_k: int,
    cache: DepthKVCache | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Sample the 7 residual codes ``c1..c7`` for one frame.

    ``lm``: the :class:`~.lm.MiniMaxMusic3AudioLM` (needed for its
    ``embed_audio_code0``/``audio_extra_embedding`` embedding tables and its
    ``model.audio_decoder`` submodule). ``llm_hidden``: ``[2, 1, hidden]``,
    both CFG rows, the global LLM's hidden state for this frame (the same
    tensor ``lm_head`` was applied to). ``code0``: a 0-dim tensor (the
    already-sampled semantic code, kept on-device -- see :mod:`.ar_loop`'s
    ``_sample_semantic``). ``cache``, when given, is reset here and carries
    this frame's depth KV state, so each codebook feeds the decoder only its
    own new token instead of the whole growing prefix; without it every
    codebook re-runs the full prefix (the reference path). Returns
    ``(codes, depth_hidden)``: ``codes`` is a ``[7]`` long tensor
    ``[c1, ..., c7]``, sampled and written entirely on-device (no per-code
    GPU->CPU sync -- see :func:`.cfg_sampling.guided_top_k_sample_id`);
    ``depth_hidden`` is the CONDITIONAL row's (batch index 0) 7 per-step
    hidden states concatenated, ``[1, 7*hidden]`` — CFG needs both rows to
    sample, but only the conditional row's representation feeds the DiT
    condition later.
    """
    decoder: DepthDecoderModule = lm.model.audio_decoder
    hidden = llm_hidden.squeeze(1)  # [2, hidden]

    embed_c0 = lm.embed_audio_code0(code0.reshape(1)).expand(2, -1)  # [2, hidden]
    tokens = decoder.projection(torch.stack([hidden, embed_c0], dim=1))  # [2, 2, hidden]
    if cache is not None:
        cache.reset()

    codes = torch.empty(NUM_RESIDUAL_CODEBOOKS, dtype=torch.long, device=hidden.device)
    depth_hiddens: list[torch.Tensor] = []
    for i in range(1, NUM_RESIDUAL_CODEBOOKS + 1):
        out = decoder(tokens) if cache is None else decoder(tokens, cache)  # [2, T, hidden]
        last = out[:, -1, :]  # [2, hidden]
        depth_hiddens.append(last[0:1])  # conditional row only
        logits = decoder.audio_heads[i - 1](last)  # [2, audio_vocab_size]
        code_i = guided_top_k_sample_id(logits[0], logits[1], cfg_scale, top_k, generator, mask_fn=None)  # 0-dim, no sync
        codes[i - 1] = code_i  # GPU-side write into the preallocated tensor, no sync
        if i < NUM_RESIDUAL_CODEBOOKS:
            extra_idx = code_i + (i - 1) * lm.cfg.audio_vocab_size
            embed_i = lm.model.audio_extra_embedding(extra_idx.reshape(1)).expand(2, -1)  # [2, hidden]
            next_token = decoder.projection(embed_i).unsqueeze(1)  # [2, 1, hidden]
            tokens = next_token if cache is not None else torch.cat([tokens, next_token], dim=1)

    depth_hidden = torch.cat(depth_hiddens, dim=-1)  # [1, 7*hidden]
    return codes, depth_hidden
