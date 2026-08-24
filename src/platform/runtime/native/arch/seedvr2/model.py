# Derived from: ByteDance's SeedVR2 models/dit_v2 (Apache-2.0). The block-level
# building blocks moved to vendor/seedvr2/layers.py, vendor/seedvr2/attention.py,
# vendor/seedvr2/cache.py, vendor/seedvr2/na.py, vendor/seedvr2/rope.py,
# vendor/seedvr2/window.py verbatim. This class stays in src because it
# extends NativeArchModule (PotionUI's own loader contract, not ByteDance's) and
# owns only from_config/post_load composition.

"""SeedVR2 NaDiT — ``SeedVR2`` (``NativeArchModule``).

Vendored from ByteDance's SeedVR2 ``models/dit_v2`` (Apache-2.0), adapted to the
native ``operations`` seam and trimmed to single-GPU inference. What SeedVR2 IS: a
Native-resolution DiT for video/image **restoration** — joint video+text
attention with 3D Swin windows, ``AdaSingle`` timestep modulation and a SwiGLU
MLP, predicting a v-target from a 33-channel latent (16 noisy + 16 low-res
conditioning + 1 mask) at 16 output channels. The block-level building blocks
live in ``vendor/seedvr2/``; this module keeps the top-level ``SeedVR2`` class.

Faithful to the reference (math unchanged): NaPatch in/out, the windowed
multimodal attention with per-window text repetition, the multimodal 3D RoPE
(:mod:`vendor.seedvr2.rope`), ``AdaSingle`` modulation and the SwiGLU MLP. The
one non-obvious behaviour reproduced deliberately is the ``vid_out_ada`` cache
collision — see :mod:`vendor.seedvr2.cache`.

Dropped as no-ops for single-GPU inference: sequence-parallel ``slice_inputs`` /
``gather_*`` distributed ops (identity here) and gradient checkpointing. Flash
varlen attention is replaced by the project's attention dispatcher over the same
window blocks (:mod:`vendor.seedvr2.attention`).

Forward-call contract
---------------------
``forward(vid, timestep, txt)``

  * ``vid``      — 5D latent ``(B, 33, T, H, W)`` (noise+conditioning already
                   concatenated on the channel dim). Packed to the reference's
                   ``(L, C)`` native-resolution layout internally; ``B == 1`` is
                   the primary path.
  * ``timestep`` — scalar / ``(B,)`` diffusion timestep (used AS-IS, sinusoidally
                   embedded like the reference — no x1000 rescale here).
  * ``txt``      — text embeddings ``(L, 5120)`` or ``(B, L, 5120)``.

Returns the v-prediction ``(B, 16, T, H, W)``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import torch
import torch.nn as nn
from einops import rearrange

from vendor.seedvr2.attention import set_attention_backend
from vendor.seedvr2.cache import Cache
from vendor.seedvr2.layers import AdaSingle, NaMMSRTransformerBlock, NaPatchIn, NaPatchOut, TimeEmbedding
from vendor.seedvr2 import na

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from .config import SeedVR2Config

Tensor = torch.Tensor

# vendor/seedvr2/attention.py must not import src (layering guard) — this is
# the one module that constructs SeedVR2/SeedVR27B (and therefore the
# NaSwinAttention instances that call the injected attention backend), so
# wiring it here guarantees it's set before any forward() runs.
set_attention_backend(_dispatch_attention)


# ---------------------------------------------------------------------------
# The SeedVR2 module.
# ---------------------------------------------------------------------------
class SeedVR2(NativeArchModule):
    """SeedVR2 Native-resolution DiT (3B ``mmdit_sr`` restoration backbone)."""

    def __init__(self, cfg: SeedVR2Config, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.patch_size = cfg.patch_size

        self.vid_in = NaPatchIn(cfg.vid_in_channels, cfg.patch_size, cfg.vid_dim, operations, dtype=dtype, device=device)
        self.txt_in = operations.Linear(cfg.txt_in_dim, cfg.vid_dim, bias=True, dtype=dtype, device=device)
        self.emb_in = TimeEmbedding(256, cfg.vid_dim, cfg.emb_dim, operations, dtype=dtype, device=device)
        self.blocks = nn.ModuleList(
            [NaMMSRTransformerBlock(cfg, i, operations, dtype=dtype, device=device) for i in range(cfg.num_layers)]
        )
        self.vid_out_norm = None
        if cfg.vid_out_norm:
            self.vid_out_norm = operations.RMSNorm(cfg.vid_dim, eps=cfg.norm_eps, elementwise_affine=True, dtype=dtype, device=device)
            self.vid_out_ada = AdaSingle(cfg.vid_dim, cfg.emb_dim, ["out"], modes=("in",))
        self.vid_out = NaPatchOut(cfg.vid_out_channels, cfg.patch_size, cfg.vid_dim, operations, dtype=dtype, device=device)

        # Engine helpers read latent geometry off ``.params``.
        self.params = SimpleNamespace(in_channels=cfg.vid_in_channels, out_channels=cfg.vid_out_channels)

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "SeedVR2":
        return cls(SeedVR2Config.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """No derived buffers to rebuild: the RoPE ``freqs`` tables are persistent
        checkpoint buffers (loaded verbatim), not ``__init__``-computed state, and
        every positional grid is generated per forward from the token shapes."""
        return None

    # -- forward ------------------------------------------------------------

    def forward(self, vid: Tensor, timestep, txt: Tensor, **kwargs) -> Tensor:
        device, dtype = vid.device, vid.dtype
        B = vid.shape[0]

        # Pack (B, C, T, H, W) -> native-resolution (L, C) channels-last + shapes.
        vid_flat, vid_shape = na.flatten([rearrange(vid[b], "c t h w -> t h w c") for b in range(B)])
        if txt.ndim == 3:
            txt_flat, txt_shape = na.flatten([txt[b] for b in range(B)])
        else:
            txt_flat, txt_shape = na.flatten([txt])

        cache = Cache()
        txt_h = self.txt_in(txt_flat)
        vid_h, vid_shape = self.vid_in(vid_flat, vid_shape, cache)
        emb = self.emb_in(timestep, device=device, dtype=dtype)

        for block in self.blocks:
            vid_h, txt_h, vid_shape, txt_shape = block(vid_h, txt_h, vid_shape, txt_shape, emb, cache)

        if self.vid_out_norm is not None:
            vid_h = self.vid_out_norm(vid_h)
            vid_h = self.vid_out_ada(
                vid_h, emb, layer="out", mode="in", cache=cache, branch_tag="vid",
                hid_len=cache("vid_len", lambda: vid_shape.prod(-1)),
            )

        vid_h, vid_shape = self.vid_out(vid_h, vid_shape, cache)
        vids = na.unflatten(vid_h, vid_shape)
        return torch.stack([rearrange(v, "t h w c -> c t h w") for v in vids], dim=0)
