# Vendored from ByteDance's SeedVR2 — https://github.com/ByteDance-Seed/SeedVR
# Upstream path: models/dit_v2 @ unknown; vendored ~2025 (moved into
# vendor/seedvr2/ from src/platform/runtime/native/arch/seedvr2/model.py as
# part of the license-relocation workstream, BE-97).
# License: Apache-2.0 (see LICENSE).

"""SeedVR2 3B NaDiT building blocks — the multimodal-branching wrapper,
timestep embedding, AdaSingle modulation, SwiGLU MLP, NaPatch in/out, and the
windowed multimodal attention + transformer block.

The top-level ``SeedVR2`` class (``src/platform/runtime/native/arch/seedvr2/model.py``)
extends ``NativeArchModule`` (PotionUI's own loader contract, not ByteDance's)
and owns nothing beyond composing these blocks + ``from_config``/``post_load``,
so it stays in src and imports from here.
"""

from __future__ import annotations

import math
from itertools import chain
from typing import Any, Callable, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange
from torch.nn.modules.utils import _triple

from . import na
from .attention import varlen_attention
from .cache import Cache
from .rope import get_na_rope
from .window import get_window_op

Tensor = torch.Tensor


# ---------------------------------------------------------------------------
# Multimodal (video/text) branching wrapper.
# ---------------------------------------------------------------------------
class MMArg:
    """Marker holding distinct video/text values for a call-time argument."""

    __slots__ = ("vid", "txt")

    def __init__(self, vid: Any, txt: Any) -> None:
        self.vid = vid
        self.txt = txt


def _branch_args(key: str, args: Tuple) -> list:
    return [getattr(v, key) if isinstance(v, MMArg) else v for v in args]


def _branch_kwargs(key: str, kwargs: dict) -> dict:
    return {k: (getattr(v, key) if isinstance(v, MMArg) else v) for k, v in kwargs.items()}


class MMModule(nn.Module):
    """Run one submodule over the video stream and (unless ``vid_only``) the text
    stream. ``shared_weights`` uses a single ``.all`` module for both (the later
    NaDiT blocks); otherwise separate ``.vid`` / ``.txt`` modules (the mm blocks).

    ``builder`` is a zero-arg factory — video and text dims are equal for SeedVR2,
    so both branches are built identically. Call-time arguments that differ per
    branch are passed as :class:`MMArg`.
    """

    def __init__(self, builder: Callable[[], nn.Module], *, shared_weights: bool = False, vid_only: bool = False) -> None:
        super().__init__()
        self.shared_weights = shared_weights
        self.vid_only = vid_only
        if shared_weights:
            self.all = builder()
        else:
            self.vid = builder()
            self.txt = None if vid_only else builder()

    def forward(self, vid: Tensor, txt: Tensor, *args, **kwargs) -> Tuple[Tensor, Tensor]:
        vid_mod = self.all if self.shared_weights else self.vid
        vid = vid_mod(vid, *_branch_args("vid", args), **_branch_kwargs("vid", kwargs))
        if not self.vid_only:
            txt_mod = self.all if self.shared_weights else self.txt
            txt = txt_mod(txt, *_branch_args("txt", args), **_branch_kwargs("txt", kwargs))
        return vid, txt


# ---------------------------------------------------------------------------
# Timestep embedding (emb_in).
# ---------------------------------------------------------------------------
def _sinusoidal_embedding(timesteps: Tensor, dim: int, max_period: int = 10000) -> Tensor:
    """diffusers ``get_timestep_embedding`` with ``flip_sin_to_cos=False`` and
    ``downscale_freq_shift=0`` (the exact flags the reference emb_in uses)."""
    half = dim // 2
    exponent = -math.log(max_period) * torch.arange(half, dtype=torch.float32, device=timesteps.device)
    exponent = exponent / half
    emb = torch.exp(exponent)
    emb = timesteps[:, None].float() * emb[None, :]
    emb = torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)
    if dim % 2 == 1:
        emb = F.pad(emb, (0, 1, 0, 0))
    return emb


class TimeEmbedding(nn.Module):
    def __init__(self, sinusoidal_dim: int, hidden_dim: int, output_dim: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.sinusoidal_dim = sinusoidal_dim
        self.proj_in = operations.Linear(sinusoidal_dim, hidden_dim, dtype=dtype, device=device)
        self.proj_hid = operations.Linear(hidden_dim, hidden_dim, dtype=dtype, device=device)
        self.proj_out = operations.Linear(hidden_dim, output_dim, dtype=dtype, device=device)
        self.act = nn.SiLU()

    def forward(self, timestep, device, dtype) -> Tensor:
        if not torch.is_tensor(timestep):
            timestep = torch.tensor([timestep], device=device, dtype=dtype)
        if timestep.ndim == 0:
            timestep = timestep[None]
        emb = _sinusoidal_embedding(timestep, self.sinusoidal_dim).to(dtype)
        emb = self.act(self.proj_in(emb))
        emb = self.act(self.proj_hid(emb))
        return self.proj_out(emb)


# ---------------------------------------------------------------------------
# adaLN single-token modulation (AdaSingle).
# ---------------------------------------------------------------------------
def _expand_dims(x: Tensor, dim: int, ndim: int) -> Tensor:
    shape = x.shape
    shape = shape[:dim] + (1,) * (ndim - len(shape)) + shape[dim:]
    return x.reshape(shape)


class AdaSingle(nn.Module):
    """Per-token shift/scale/gate: an emb-derived term (split from the 6*dim
    timestep embedding) plus a learned per-layer parameter."""

    def __init__(self, dim: int, emb_dim: int, layers: List[str], modes: Tuple[str, ...] = ("in", "out")) -> None:
        assert emb_dim == 6 * dim, "AdaSingle requires emb_dim == 6 * dim"
        super().__init__()
        self.dim = dim
        self.emb_dim = emb_dim
        self.layers = list(layers)
        for l in self.layers:
            if "in" in modes:
                self.register_parameter(f"{l}_shift", nn.Parameter(torch.randn(dim) / dim ** 0.5))
                self.register_parameter(f"{l}_scale", nn.Parameter(torch.randn(dim) / dim ** 0.5 + 1))
            if "out" in modes:
                self.register_parameter(f"{l}_gate", nn.Parameter(torch.randn(dim) / dim ** 0.5))

    def forward(self, hid: Tensor, emb: Tensor, layer: str, mode: str, cache: Cache,
                branch_tag: str = "", hid_len: Optional[Tensor] = None) -> Tensor:
        idx = self.layers.index(layer)
        # NOTE: this fresh split uses len(self.layers) groups; for the final
        # ``layers=["out"]`` ada it is the *wrong* width and is deliberately
        # discarded via the cache collision documented in cache.py.
        e = rearrange(emb, "b (d l g) -> b d l g", l=len(self.layers), g=3)[..., idx, :]
        e = _expand_dims(e, 1, hid.ndim + 1)
        if hid_len is not None:
            e = cache(f"emb_repeat_{idx}_{branch_tag}", lambda: torch.repeat_interleave(e, hid_len, dim=0))

        shiftA, scaleA, gateA = e.unbind(-1)
        shiftB = getattr(self, f"{layer}_shift", None)
        scaleB = getattr(self, f"{layer}_scale", None)
        gateB = getattr(self, f"{layer}_gate", None)
        # Under manual_cast the learned params keep storage dtype; align to the
        # activation dtype before the (in-place) modulation arithmetic.
        if shiftB is not None:
            shiftB = shiftB.to(hid.dtype)
        if scaleB is not None:
            scaleB = scaleB.to(hid.dtype)
        if gateB is not None:
            gateB = gateB.to(hid.dtype)

        if mode == "in":
            return hid.mul_(scaleA + scaleB).add_(shiftA + shiftB)
        if mode == "out":
            return hid.mul_(gateA + gateB) if gateB is not None else hid.mul_(gateA)
        raise NotImplementedError(mode)


# ---------------------------------------------------------------------------
# SwiGLU MLP.
# ---------------------------------------------------------------------------
class SwiGLUMLP(nn.Module):
    def __init__(self, dim: int, hidden: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.proj_in_gate = operations.Linear(dim, hidden, bias=False, dtype=dtype, device=device)
        self.proj_out = operations.Linear(hidden, dim, bias=False, dtype=dtype, device=device)
        self.proj_in = operations.Linear(dim, hidden, bias=False, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        return self.proj_out(F.silu(self.proj_in_gate(x)) * self.proj_in(x))


# ---------------------------------------------------------------------------
# NaPatch in/out (patchify / unpatchify over the native-resolution layout).
# ---------------------------------------------------------------------------
class NaPatchIn(nn.Module):
    def __init__(self, in_channels: int, patch_size, dim: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.patch_size = _triple(patch_size)
        t, h, w = self.patch_size
        self.proj = operations.Linear(in_channels * t * h * w, dim, dtype=dtype, device=device)

    def forward(self, vid: Tensor, vid_shape: Tensor, cache: Cache) -> Tuple[Tensor, Tensor]:
        cache = cache.namespace("patch")
        vid_shape_before = cache("vid_shape_before_patchify", lambda: vid_shape)
        t, h, w = self.patch_size
        if not (t == h == w == 1):
            vids = na.unflatten(vid, vid_shape)
            for i in range(len(vids)):
                if t > 1 and vid_shape_before[i, 0] % t != 0:
                    vids[i] = torch.cat([vids[i][:1]] * (t - vids[i].size(0) % t) + [vids[i]], dim=0)
                vids[i] = rearrange(vids[i], "(T t) (H h) (W w) c -> T H W (t h w c)", t=t, h=h, w=w)
            vid, vid_shape = na.flatten(vids)
        return self.proj(vid), vid_shape


class NaPatchOut(nn.Module):
    def __init__(self, out_channels: int, patch_size, dim: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.patch_size = _triple(patch_size)
        t, h, w = self.patch_size
        self.proj = operations.Linear(dim, out_channels * t * h * w, dtype=dtype, device=device)

    def forward(self, vid: Tensor, vid_shape: Tensor, cache: Cache) -> Tuple[Tensor, Tensor]:
        cache = cache.namespace("patch")
        vid_shape_before = cache.get("vid_shape_before_patchify")
        t, h, w = self.patch_size
        vid = self.proj(vid)
        if not (t == h == w == 1):
            vids = na.unflatten(vid, vid_shape)
            for i in range(len(vids)):
                vids[i] = rearrange(vids[i], "T H W (t h w c) -> (T t) (H h) (W w) c", t=t, h=h, w=w)
                if t > 1 and vid_shape_before[i, 0] % t != 0:
                    vids[i] = vids[i][(t - vid_shape_before[i, 0] % t):]
            vid, vid_shape = na.flatten(vids)
        return vid, vid_shape


# ---------------------------------------------------------------------------
# Windowed multimodal attention (NaSwinAttention).
# ---------------------------------------------------------------------------
def _cu_seqlens(all_len: Tensor) -> Tensor:
    return F.pad(all_len.cumsum(0), (1, 0)).int()


class NaSwinAttention(nn.Module):
    def __init__(self, dim: int, heads: int, head_dim: int, qk_bias: bool, norm_eps: float,
                 rope_type: str, rope_dim: int, shared_weights: bool, window, window_method: str,
                 operations, dtype=None, device=None) -> None:
        super().__init__()
        inner = heads * head_dim
        self.head_dim = head_dim
        self.window = _triple(window)
        self.window_method = window_method
        self.window_op = get_window_op(window_method)
        self.proj_qkv = MMModule(
            lambda: operations.Linear(dim, inner * 3, bias=qk_bias, dtype=dtype, device=device),
            shared_weights=shared_weights,
        )
        self.proj_out = MMModule(
            lambda: operations.Linear(inner, dim, bias=True, dtype=dtype, device=device),
            shared_weights=shared_weights,
        )
        self.norm_q = MMModule(
            lambda: operations.RMSNorm(head_dim, eps=norm_eps, elementwise_affine=True, dtype=dtype, device=device),
            shared_weights=shared_weights,
        )
        self.norm_k = MMModule(
            lambda: operations.RMSNorm(head_dim, eps=norm_eps, elementwise_affine=True, dtype=dtype, device=device),
            shared_weights=shared_weights,
        )
        self.rope = get_na_rope(rope_type, rope_dim)

    def forward(self, vid: Tensor, txt: Tensor, vid_shape: Tensor, txt_shape: Tensor, cache: Cache) -> Tuple[Tensor, Tensor]:
        vid_qkv, txt_qkv = self.proj_qkv(vid, txt)
        cache_win = cache.namespace(f"{self.window_method}_{self.window}")

        def make_window(x: Tensor) -> List[Tensor]:
            t, h, w, _ = x.shape
            return [x[st, sh, sw] for (st, sh, sw) in self.window_op((t, h, w), self.window)]

        window_partition, window_reverse, window_shape, window_count = cache_win(
            "win_transform", lambda: na.window_idx(vid_shape, make_window)
        )
        vid_qkv_win = window_partition(vid_qkv)

        vid_qkv_win = rearrange(vid_qkv_win, "l (o h d) -> l o h d", o=3, d=self.head_dim)
        txt_qkv = rearrange(txt_qkv, "l (o h d) -> l o h d", o=3, d=self.head_dim)
        vid_q, vid_k, vid_v = vid_qkv_win.unbind(1)
        txt_q, txt_k, txt_v = txt_qkv.unbind(1)

        vid_q, txt_q = self.norm_q(vid_q, txt_q)
        vid_k, txt_k = self.norm_k(vid_k, txt_k)

        txt_len = cache("txt_len", lambda: txt_shape.prod(-1))
        vid_len_win = cache_win("vid_len", lambda: window_shape.prod(-1))
        txt_len_win = cache_win("txt_len", lambda: txt_len.repeat_interleave(window_count))
        all_len_win = cache_win("all_len", lambda: vid_len_win + txt_len_win)
        concat_win, unconcat_win = cache_win(
            "mm_pnp", lambda: na.repeat_concat_idx(vid_len_win, txt_len, window_count)
        )

        if self.rope is not None:
            num_h = txt_q.shape[1]

            def repeat_per_window(t: Tensor) -> Tuple[Tensor, Tensor]:
                # Text positions are window-independent, so every window gets an
                # identical copy; this only shapes txt to match the per-window vid
                # structure the mm-RoPE zip expects.
                flat = rearrange(t, "l h d -> l (h d)")
                parts = na.unflatten(flat, txt_shape)
                parts = list(chain(*[[x] * n for x, n in zip(parts, window_count.tolist())]))
                flat, shape = na.flatten(parts)
                return rearrange(flat, "l (h d) -> l h d", h=num_h), shape

            txt_q_rep, txt_shape_rep = repeat_per_window(txt_q)
            txt_k_rep, _ = repeat_per_window(txt_k)
            vid_q, vid_k, txt_q, txt_k = self.rope(
                vid_q, vid_k, window_shape, txt_q_rep, txt_k_rep, txt_shape_rep, cache_win
            )

        cu = cache_win("cu_seqlens", lambda: _cu_seqlens(all_len_win))
        out = varlen_attention(
            concat_win(vid_q, txt_q),
            concat_win(vid_k, txt_k),
            concat_win(vid_v, txt_v),
            cu_seqlens_q=cu,
            cu_seqlens_k=cu,
        ).type_as(vid_q)

        vid_out, txt_out = unconcat_win(out)
        vid_out = rearrange(vid_out, "l h d -> l (h d)")
        txt_out = rearrange(txt_out, "l h d -> l (h d)")
        vid_out = window_reverse(vid_out)

        return self.proj_out(vid_out, txt_out)


# ---------------------------------------------------------------------------
# Transformer block (NaMMSRTransformerBlock).
# ---------------------------------------------------------------------------
class NaMMSRTransformerBlock(nn.Module):
    def __init__(self, cfg, layer_index: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        shared = not cfg.is_mm_layer(layer_index)
        is_last = layer_index == cfg.num_layers - 1
        dim = cfg.vid_dim
        self.is_last_layer = is_last

        self.attn_norm = MMModule(
            lambda: operations.RMSNorm(dim, eps=cfg.norm_eps, elementwise_affine=False, dtype=dtype, device=device),
            shared_weights=shared,
        )
        self.attn = NaSwinAttention(
            dim, cfg.heads, cfg.head_dim, qk_bias=False, norm_eps=cfg.norm_eps,
            rope_type="mmrope3d", rope_dim=cfg.rope_dim, shared_weights=shared,
            window=cfg.window, window_method=cfg.window_method(layer_index),
            operations=operations, dtype=dtype, device=device,
        )
        self.mlp_norm = MMModule(
            lambda: operations.RMSNorm(dim, eps=cfg.norm_eps, elementwise_affine=False, dtype=dtype, device=device),
            shared_weights=shared, vid_only=is_last,
        )
        self.mlp = MMModule(
            lambda: SwiGLUMLP(dim, cfg.mlp_hidden, operations, dtype=dtype, device=device),
            shared_weights=shared, vid_only=is_last,
        )
        self.ada = MMModule(
            lambda: AdaSingle(dim, cfg.emb_dim, ["attn", "mlp"]),
            shared_weights=shared, vid_only=is_last,
        )

    def forward(self, vid: Tensor, txt: Tensor, vid_shape: Tensor, txt_shape: Tensor,
                emb: Tensor, cache: Cache) -> Tuple[Tensor, Tensor, Tensor, Tensor]:
        ada_kwargs = dict(
            emb=emb,
            hid_len=MMArg(cache("vid_len", lambda: vid_shape.prod(-1)),
                         cache("txt_len", lambda: txt_shape.prod(-1))),
            cache=cache,
            branch_tag=MMArg("vid", "txt"),
        )

        vid_a, txt_a = self.attn_norm(vid, txt)
        vid_a, txt_a = self.ada(vid_a, txt_a, layer="attn", mode="in", **ada_kwargs)
        vid_a, txt_a = self.attn(vid_a, txt_a, vid_shape, txt_shape, cache)
        vid_a, txt_a = self.ada(vid_a, txt_a, layer="attn", mode="out", **ada_kwargs)
        vid_a, txt_a = vid_a + vid, txt_a + txt

        vid_m, txt_m = self.mlp_norm(vid_a, txt_a)
        vid_m, txt_m = self.ada(vid_m, txt_m, layer="mlp", mode="in", **ada_kwargs)
        vid_m, txt_m = self.mlp(vid_m, txt_m)
        vid_m, txt_m = self.ada(vid_m, txt_m, layer="mlp", mode="out", **ada_kwargs)
        vid_m, txt_m = vid_m + vid_a, txt_m + txt_a

        return vid_m, txt_m, vid_shape, txt_shape
