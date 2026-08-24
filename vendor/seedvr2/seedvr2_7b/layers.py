# Vendored from ByteDance's SeedVR2 — https://github.com/ByteDance-Seed/SeedVR
# Upstream path: models/dit (7B; the numz ComfyUI-SeedVR2_VideoUpscaler
# dit_7b mirror was the vendoring base) @ unknown; vendored ~2025 (moved into
# vendor/seedvr2/seedvr2_7b/ from src/platform/runtime/native/arch/seedvr2_7b/model.py
# as part of the license-relocation workstream, BE-97).
# License: Apache-2.0 (see ../LICENSE).

"""SeedVR2 7B NaDiT building blocks — plain GELU-tanh MLP, windowed multimodal
attention with video-only pixel RoPE, and the (always-multimodal) transformer
block. Reuses the 3B's ``AdaSingle``/``MMArg``/``MMModule``/``NaPatchIn``/
``NaPatchOut``/``_cu_seqlens`` (``..layers``) — the 7B differs only in MLP type,
RoPE convention, and having no shared-weight blocks.

The top-level ``SeedVR27B`` class
(``src/platform/runtime/native/arch/seedvr2_7b/model.py``) extends
``NativeArchModule`` (PotionUI's own loader contract) and owns nothing beyond
composing these blocks + ``from_config``/``post_load``, so it stays in src and
imports from here.
"""

from __future__ import annotations

from typing import List, Tuple

import torch
import torch.nn as nn
from einops import rearrange

from .. import na
from ..attention import varlen_attention
from ..cache import Cache
from ..layers import AdaSingle, MMArg, MMModule, NaPatchIn, NaPatchOut, _cu_seqlens  # noqa: F401 — re-exported for arch/model.py
from ..window import get_window_op
from .rope import NaVideoRotaryEmbedding3d

Tensor = torch.Tensor


# ---------------------------------------------------------------------------
# Plain MLP (mlp_type="normal": GELU-tanh with bias, expand_ratio 4).
# ---------------------------------------------------------------------------
class MLP(nn.Module):
    def __init__(self, dim: int, hidden: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.proj_in = operations.Linear(dim, hidden, dtype=dtype, device=device)
        self.act = nn.GELU(approximate="tanh")
        self.proj_out = operations.Linear(hidden, dim, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        return self.proj_out(self.act(self.proj_in(x)))


# ---------------------------------------------------------------------------
# Windowed multimodal attention (NaSwinAttention) — video-only RoPE, all-split.
# ---------------------------------------------------------------------------
class NaSwinAttention(nn.Module):
    def __init__(self, dim: int, heads: int, head_dim: int, norm_eps: float,
                 window, window_method: str, operations, dtype=None, device=None) -> None:
        super().__init__()
        inner = heads * head_dim
        self.head_dim = head_dim
        self.window = window
        self.window_method = window_method
        self.window_op = get_window_op(window_method)
        # qk_bias=False; every block keeps split .vid/.txt weights (shared_qkv=False).
        self.proj_qkv = MMModule(
            lambda: operations.Linear(dim, inner * 3, bias=False, dtype=dtype, device=device),
        )
        self.proj_out = MMModule(
            lambda: operations.Linear(inner, dim, bias=True, dtype=dtype, device=device),
        )
        self.norm_q = MMModule(
            lambda: operations.RMSNorm(head_dim, eps=norm_eps, elementwise_affine=True, dtype=dtype, device=device),
        )
        self.norm_k = MMModule(
            lambda: operations.RMSNorm(head_dim, eps=norm_eps, elementwise_affine=True, dtype=dtype, device=device),
        )
        # Video-only 3D pixel RoPE (dim = head_dim // 2). Buffer key: rope.rope.freqs.
        self.rope = NaVideoRotaryEmbedding3d(dim=head_dim // 2)

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

        # Video-only rope, applied within windows; text tokens are un-rotated.
        vid_q, vid_k = self.rope(vid_q, vid_k, window_shape, cache_win)

        txt_len = cache("txt_len", lambda: txt_shape.prod(-1))
        vid_len_win = cache_win("vid_len", lambda: window_shape.prod(-1))
        txt_len_win = cache_win("txt_len", lambda: txt_len.repeat_interleave(window_count))
        all_len_win = cache_win("all_len", lambda: vid_len_win + txt_len_win)
        concat_win, unconcat_win = cache_win(
            "mm_pnp", lambda: na.repeat_concat_idx(vid_len_win, txt_len, window_count)
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
# Transformer block (NaMMSRTransformerBlock) — plain MLP, all blocks multimodal.
# ---------------------------------------------------------------------------
class NaMMSRTransformerBlock(nn.Module):
    def __init__(self, cfg, layer_index: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        dim = cfg.vid_dim

        self.attn_norm = MMModule(
            lambda: operations.RMSNorm(dim, eps=cfg.norm_eps, elementwise_affine=False, dtype=dtype, device=device),
        )
        self.attn = NaSwinAttention(
            dim, cfg.heads, cfg.head_dim, norm_eps=cfg.norm_eps,
            window=cfg.window, window_method=cfg.window_method(layer_index),
            operations=operations, dtype=dtype, device=device,
        )
        self.mlp_norm = MMModule(
            lambda: operations.RMSNorm(dim, eps=cfg.norm_eps, elementwise_affine=False, dtype=dtype, device=device),
        )
        self.mlp = MMModule(
            lambda: MLP(dim, cfg.mlp_hidden, operations, dtype=dtype, device=device),
        )
        self.ada = MMModule(
            lambda: AdaSingle(dim, cfg.emb_dim, ["attn", "mlp"]),
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
