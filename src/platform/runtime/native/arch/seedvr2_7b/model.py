# Derived from: ByteDance's SeedVR2 models/dit (Apache-2.0; the numz
# ComfyUI-SeedVR2_VideoUpscaler dit_7b mirror was the vendoring base). The
# block-level building blocks moved to vendor/seedvr2/seedvr2_7b/layers.py +
# vendor/seedvr2/seedvr2_7b/rope.py verbatim, reusing the 3B's shared
# blocks in vendor/seedvr2/layers.py. This class stays in src because it
# extends NativeArchModule (PotionUI's own loader contract, not ByteDance's).

"""SeedVR2 7B NaDiT — ``SeedVR27B`` (``NativeArchModule``).

Vendored from ByteDance's SeedVR2 ``models/dit`` (Apache-2.0; the numz
``ComfyUI-SeedVR2_VideoUpscaler`` ``dit_7b`` mirror was the vendoring base), adapted
to the native ``operations`` seam and trimmed to single-GPU inference. See the
package docstring (:mod:`.`) for how the 7B differs from the 3B backbone. The
block-level building blocks live in ``vendor/seedvr2/seedvr2_7b/``; this module
keeps the top-level ``SeedVR27B`` class.

Faithful to the reference (math unchanged): NaPatch in/out, windowed multimodal
attention with per-window text repetition, video-only 3D pixel RoPE
(:mod:`vendor.seedvr2.seedvr2_7b.rope`), ``AdaSingle`` modulation and a plain
GELU-tanh MLP. Every block is multimodal (split ``.vid``/``.txt`` weights);
there is no output-norm head.

Dropped as no-ops for single-GPU inference: sequence-parallel ``slice_inputs`` /
``gather_*`` distributed ops (identity here) and gradient checkpointing. Flash
varlen attention is replaced by the project's attention dispatcher over the same
window blocks (:mod:`vendor.seedvr2.attention`).

Forward-call contract (identical to the 3B ``SeedVR2``)
------------------------------------------------------
``forward(vid, timestep, txt)``

  * ``vid``      — 5D latent ``(B, 33, T, H, W)`` (noise+conditioning already
                   concatenated on the channel dim). Packed to the reference's
                   ``(L, C)`` native-resolution layout internally; ``B == 1`` is the
                   primary path.
  * ``timestep`` — scalar / ``(B,)`` diffusion timestep (sinusoidally embedded like
                   the reference — no x1000 rescale here).
  * ``txt``      — text embeddings ``(L, 5120)`` or ``(B, L, 5120)``.

Returns the v-prediction ``(B, 16, T, H, W)``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import torch
import torch.nn as nn
from einops import rearrange

from vendor.seedvr2 import na
from vendor.seedvr2.attention import set_attention_backend
from vendor.seedvr2.cache import Cache
from vendor.seedvr2.layers import NaPatchIn, NaPatchOut, TimeEmbedding
from vendor.seedvr2.seedvr2_7b.layers import NaMMSRTransformerBlock

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from .config import SeedVR27BConfig

Tensor = torch.Tensor

# vendor/seedvr2/attention.py must not import src (layering guard) — this is
# the one module that constructs SeedVR2/SeedVR27B, so wiring it here (again;
# arch/seedvr2/model.py already does the same call, idempotent) guarantees
# it's set even if this 7B module is imported without the 3B one.
set_attention_backend(_dispatch_attention)


# ---------------------------------------------------------------------------
# The SeedVR2 7B module.
# ---------------------------------------------------------------------------
class SeedVR27B(NativeArchModule):
    """SeedVR2 Native-resolution DiT (7B ``mmdit_sr`` restoration backbone)."""

    def __init__(self, cfg: SeedVR27BConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.patch_size = cfg.patch_size

        self.vid_in = NaPatchIn(cfg.vid_in_channels, cfg.patch_size, cfg.vid_dim, operations, dtype=dtype, device=device)
        self.txt_in = operations.Linear(cfg.txt_in_dim, cfg.vid_dim, bias=True, dtype=dtype, device=device)
        self.emb_in = TimeEmbedding(256, cfg.vid_dim, cfg.emb_dim, operations, dtype=dtype, device=device)
        self.blocks = nn.ModuleList(
            [NaMMSRTransformerBlock(cfg, i, operations, dtype=dtype, device=device) for i in range(cfg.num_layers)]
        )
        self.vid_out = NaPatchOut(cfg.vid_out_channels, cfg.patch_size, cfg.vid_dim, operations, dtype=dtype, device=device)

        # Engine helpers read latent geometry off ``.params``.
        self.params = SimpleNamespace(in_channels=cfg.vid_in_channels, out_channels=cfg.vid_out_channels)

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "SeedVR27B":
        return cls(SeedVR27BConfig.from_detect_config(config), operations=operations)

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

        vid_h, vid_shape = self.vid_out(vid_h, vid_shape, cache)
        vids = na.unflatten(vid_h, vid_shape)
        return torch.stack([rearrange(v, "t h w c -> c t h w") for v in vids], dim=0)
