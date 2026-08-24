# Vendored from ByteDance's SeedVR2 — https://github.com/ByteDance-Seed/SeedVR
# Upstream path: models/dit (7B, video-only pixel RoPE; the numz
# ComfyUI-SeedVR2_VideoUpscaler dit_7b mirror was the vendoring base) @
# unknown; vendored ~2025 (moved into vendor/seedvr2/seedvr2_7b/ from
# src/platform/runtime/native/arch/seedvr2_7b/ as part of the
# license-relocation workstream, BE-97).
# License: Apache-2.0 (see ../LICENSE).

"""Video-only 3D pixel RoPE for the SeedVR2 7B NaDiT.

The 7B backbone ropes **only** the video q/k, *within each attention window*, and
leaves text tokens un-rotated (there is no multimodal-offset joint RoPE like the
3B's ``mmrope3d``). The reference builds it from lucidrains'
``rotary_embedding_torch`` as::

    RotaryEmbedding(dim = (head_dim // 2) // 3, freqs_for = "pixel", max_freq = 256)

i.e. a *pixel*-basis rotary whose per-frequency table is
``linspace(1, max_freq/2, dim//2) * pi`` (for the 7B: head_dim 128 ->
(64 // 3) = 21 -> 10 stored freqs, matching the checkpoint's ``rope.rope.freqs``
length-10 buffer). Positions are the pixel convention ``linspace(-1, 1, steps=n)``
per axis (contrast the 3B's language ``arange(n)``), each axis expanded ``r=2`` and
the 3 axes concatenated -> 60 rotary dims (< head_dim 128, the tail passes through).

``rotary_embedding_torch`` is not vendored, so the tiny slice used is reimplemented
here bit-for-bit; ``rotate_half`` / ``apply_rotary_emb`` are shared verbatim with the
3B module. The module nesting (``NaVideoRotaryEmbedding3d.rope`` holding ``freqs``)
mirrors the reference so ``blocks.{i}.attn.rope.rope.freqs`` loads verbatim. RoPE is
computed in fp32 and cast back, matching the reference.
"""

from __future__ import annotations

import math
from typing import Tuple

import torch
from einops import rearrange, repeat
from torch import nn

from ..cache import Cache
from ..rope import apply_rotary_emb


class _PixelRotary(nn.Module):
    """Single pixel-basis rotary holding the persistent ``freqs`` buffer.

    ``dim`` is the per-axis rotary width fed to ``RotaryEmbedding`` (== ``(head_dim
    // 2) // 3``); the stored buffer has ``dim // 2`` frequencies.
    """

    def __init__(self, dim: int, max_freq: float = 256.0) -> None:
        super().__init__()
        freqs = torch.linspace(1.0, max_freq / 2, dim // 2) * math.pi
        # Persistent (in the checkpoint) — matches ``rope.rope.freqs``.
        self.register_buffer("freqs", freqs, persistent=True)

    def _axis_freqs(self, length: int) -> torch.Tensor:
        # Pixel convention: positions span [-1, 1] (not arange like the language basis).
        # Positional math runs in fp32: RoPE is fp32 anyway, and a fp8 checkpoint
        # stores this buffer as fp8 (trig on fp8 is unimplemented) — upcast first.
        freqs = self.freqs.float()
        pos = torch.linspace(-1.0, 1.0, steps=length, device=freqs.device)
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


class NaVideoRotaryEmbedding3d(nn.Module):
    """Native-resolution video-only 3D pixel RoPE.

    ``dim`` is ``head_dim // 2`` (the reference's ``NaRotaryEmbedding3d(dim=head_dim
    // 2)``); split across the 3 axes it drives a ``(head_dim // 2) // 3``-wide
    per-axis pixel rotary.
    """

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.rope = _PixelRotary(dim=dim // 3, max_freq=256.0)

    def _freqs_for(self, shape: torch.LongTensor) -> torch.Tensor:
        freq_list = []
        for f, h, w in shape.tolist():
            axial = self.rope.get_axial_freqs(f, h, w)
            freq_list.append(axial.reshape(-1, axial.size(-1)))
        return torch.cat(freq_list, dim=0)

    def forward(
        self,
        q: torch.Tensor,  # L h d
        k: torch.Tensor,  # L h d
        shape: torch.LongTensor,
        cache: Cache,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        freqs = cache("rope_freqs_3d", lambda: self._freqs_for(shape))
        freqs = freqs.to(device=q.device)

        def _apply(x: torch.Tensor) -> torch.Tensor:
            x = rearrange(x, "L h d -> h L d")
            x = apply_rotary_emb(freqs, x.float()).to(x.dtype)
            return rearrange(x, "h L d -> L h d")

        return _apply(q), _apply(k)
