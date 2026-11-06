# Vendored from ByteDance's SeedVR2 — https://github.com/ByteDance-Seed/SeedVR
# Upstream path: models/dit_v2 (RoPE, lucidrains rotary_embedding_torch math
# reimplemented — see the module docstring) @ unknown; vendored ~2025 (moved
# into vendor/seedvr2/ from src/platform/runtime/native/arch/seedvr2/ as part
# of the license-relocation workstream, BE-97).
# License: Apache-2.0 (see LICENSE).

"""3D multimodal RoPE for NaDiT — vendored ``rotary_embedding_torch`` math.

The reference builds its positional embedding from lucidrains'
``rotary_embedding_torch`` (``RotaryEmbedding(dim=rope_dim//3, freqs_for="lang",
theta=10000)`` per axis, joined over T/H/W). That package is not vendored in this
repo, so the tiny slice actually used is reimplemented here **bit-for-bit**:

  * ``freqs = 1 / theta ** (arange(0, d, 2)[:d//2] / d)`` — the learned-length
    buffer stored in the checkpoint as ``...rope.rope.freqs`` (21 values for the
    3B model: ``d = rope_dim // 3 = 128 // 3 = 42``).
  * per-axis angles ``outer(pos, freqs)`` interleaved ``r=2`` (``[f0,f0,f1,f1,…]``),
    broadcast across the 3 axes and concatenated → 126 rotary dims (< head_dim
    128, so the last 2 dims pass through unrotated), then
  * ``x·cos + rotate_half(x)·sin`` over interleaved pairs (``qk_norm_rope``'s ``pair_rotate``).

The module nesting (``NaMMRotaryEmbedding3d.rope`` holding the ``freqs`` buffer)
mirrors the reference so the checkpoint key ``blocks.{i}.attn.rope.rope.freqs``
loads verbatim. RoPE is computed in fp32 and cast back, matching the reference.
"""

from __future__ import annotations

from typing import Optional, Tuple

import torch
from einops import repeat
from torch import nn

from .cache import Cache


def cos_sin(freqs: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
    return freqs.cos()[:, None], freqs.sin()[:, None]


class _LangRotary(nn.Module):
    """Single-axis language RoPE holding the persistent ``freqs`` buffer."""

    def __init__(self, dim: int, theta: float = 10000.0) -> None:
        super().__init__()
        freqs = 1.0 / (theta ** (torch.arange(0, dim, 2)[: (dim // 2)].float() / dim))
        # Persistent (in the checkpoint) — matches ``rope.rope.freqs``.
        self.register_buffer("freqs", freqs, persistent=True)

    def _axis_freqs(self, length: int) -> torch.Tensor:
        # fp32 math regardless of the stored dtype: fp8 checkpoints quantize the
        # freqs buffer too, and CUDA has no fp8 arithmetic kernels.
        freqs = self.freqs.float()
        pos = torch.arange(length, device=freqs.device, dtype=torch.float32)
        f = torch.einsum("..., f -> ... f", pos, freqs)
        return repeat(f, "... n -> ... (n r)", r=2)  # (length, 2 * len(freqs))

    def get_axial_freqs(self, *dims: int) -> torch.Tensor:
        all_freqs = []
        for ind, dim in enumerate(dims):
            f = self._axis_freqs(dim)
            slot = [None] * len(dims)
            slot[ind] = slice(None)
            all_freqs.append(f[(Ellipsis, *slot, slice(None))])
        all_freqs = torch.broadcast_tensors(*all_freqs)
        return torch.cat(all_freqs, dim=-1)


class NaMMRotaryEmbedding3d(nn.Module):
    """Multimodal 3D RoPE: video tokens get (T, H, W) positions offset past the
    text tokens; text tokens get 1-axis positions tiled across the 3 sub-bands."""

    mm = True

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.rope = _LangRotary(dim=dim // 3, theta=10000.0)

    def _freqs_for(self, vid_shape: torch.LongTensor, txt_shape: torch.LongTensor) -> Tuple[torch.Tensor, torch.Tensor]:
        vid_list, txt_list = [], []
        for (f, h, w), l in zip(vid_shape.tolist(), txt_shape[:, 0].tolist()):
            # Positions are absolute (arange), so sizing the grid to exactly what
            # this sample needs and slicing [l:l+f] is identical to the reference
            # slicing a fixed 1024x128x128 grid.
            vid_freqs = self.rope.get_axial_freqs(l + f, h, w)
            vid_list.append(vid_freqs[l : l + f].reshape(-1, vid_freqs.size(-1)))
            txt_freqs = self.rope.get_axial_freqs(l)
            txt_list.append(txt_freqs.repeat(1, 3).reshape(-1, vid_freqs.size(-1)))
        return torch.cat(vid_list, dim=0), torch.cat(txt_list, dim=0)

    def tables(self, vid_shape: torch.LongTensor, txt_shape: torch.LongTensor, cache: Cache):
        def build():
            vid_freqs, txt_freqs = self._freqs_for(vid_shape, txt_shape)
            return cos_sin(vid_freqs), cos_sin(txt_freqs)

        return cache("mmrope_freqs_3d", build)


def get_na_rope(rope_type: Optional[str], dim: int) -> Optional[NaMMRotaryEmbedding3d]:
    if rope_type is None:
        return None
    if rope_type == "mmrope3d":
        return NaMMRotaryEmbedding3d(dim=dim)
    raise NotImplementedError(f"{rope_type} is not supported.")
