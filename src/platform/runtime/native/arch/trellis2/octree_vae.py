# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/models/sc_vaes/sparse_unet_vae.py
# (SparseConvNeXtBlock3d, SparseResBlockC2S3d, SparseUnetVaeDecoder)
# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/models/sc_vaes/fdg_vae.py
# (FlexiDualGridVaeDecoder head math)
"""Octree sparse U-Net VAE decoders: shape (FlexiDualGrid) + texture.

Both decoders share one octree-growth torso (``SparseUnetVaeDecoder``): a
latent ``SparseTensor`` is projected up through a channel schedule, each level
running ``SparseConvNeXtBlock3d`` residual blocks in place, then (all but the
last level) an octree-growth transition (``SparseResBlockC2S3d``) that
predicts, or is handed, an 8-way subdivision mask per active voxel and grows
the active set into its children via ``SparseChannel2Spatial``.

The texture decoder IS this torso (``out_channels=6``, ``pred_subdiv=False`` —
it consumes the shape decoder's subdivision decisions via ``guide_subs`` so
both decoders grow onto the identical coordinate grid). The shape decoder
(``FlexiDualGridVaeDecoder``) subclasses it to add the 7-channel FDG head math
(vertex offsets / edge-intersection logits / quad-lerp weight) and to predict
its own subdivisions (``pred_subdiv=True``), which the texture decoder then
guides on. The ``o_voxel`` dual-grid-to-mesh conversion that upstream's shape
decoder calls next is out of scope here — this module exposes the head's
structured output (coords, vertices, intersected, quad_lerp, subs) instead.

Dtype-agnostic like the other native arch modules here: no ``use_fp16``/
``.type()`` torso-precision plumbing, no ``operations`` seam — the module runs
in whatever dtype its parameters and input already are.
"""

from __future__ import annotations

from typing import List, NamedTuple, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from ...sparse3d import SparseChannel2Spatial, SparseConv3d, SparseLinear, SparseTensor
from .config import OctreeVaeDecoderConfig

__all__ = ["SparseUnetVaeDecoder", "FlexiDualGridVaeDecoder", "FdgDecoderOutput"]


def _zero_module(module: nn.Module) -> nn.Module:
    for p in module.parameters():
        p.detach().zero_()
    return module


class _SparseConvNeXtBlock3d(nn.Module):
    """In-place (no coord change) residual block: conv -> norm -> channel MLP."""

    def __init__(self, channels: int, mlp_ratio: float = 4.0) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(channels, eps=1e-6)
        self.conv = SparseConv3d(channels, channels, 3)
        hidden = int(channels * mlp_ratio)
        self.mlp = nn.Sequential(
            nn.Linear(channels, hidden),
            nn.SiLU(),
            _zero_module(nn.Linear(hidden, channels)),
        )

    def forward(self, x: SparseTensor) -> SparseTensor:
        h = self.conv(x)
        h = h.replace(self.norm(h.feats))
        h = h.replace(self.mlp(h.feats))
        return h + x


class _SparseResBlockC2S3d(nn.Module):
    """Octree-growth transition: predicts (or is handed) an 8-way subdivision
    mask per active voxel and scatters ``channels -> channels // 8`` features
    into the surviving children via ``SparseChannel2Spatial``."""

    def __init__(self, channels: int, out_channels: int, pred_subdiv: bool = True) -> None:
        super().__init__()
        self.channels = channels
        self.out_channels = out_channels
        self.pred_subdiv = pred_subdiv

        self.norm1 = nn.LayerNorm(channels, eps=1e-6)
        self.norm2 = nn.LayerNorm(out_channels, eps=1e-6, elementwise_affine=False)
        self.conv1 = SparseConv3d(channels, out_channels * 8, 3)
        self.conv2 = _zero_module(SparseConv3d(out_channels, out_channels, 3))
        if pred_subdiv:
            self.to_subdiv = SparseLinear(channels, 8)
        self.updown = SparseChannel2Spatial(2)

    def forward(
        self, x: SparseTensor, subdiv: Optional[SparseTensor] = None
    ) -> Tuple[SparseTensor, Optional[SparseTensor]]:
        if self.pred_subdiv:
            subdiv = self.to_subdiv(x)
        h = x.replace(self.norm1(x.feats))
        h = h.replace(F.silu(h.feats))
        h = self.conv1(h)
        subdiv_binarized = subdiv.replace(subdiv.feats > 0) if subdiv is not None else None
        h = self.updown(h, subdiv_binarized)
        x = self.updown(x, subdiv_binarized)
        h = h.replace(self.norm2(h.feats))
        h = h.replace(F.silu(h.feats))
        h = self.conv2(h)
        skip = x.feats.repeat_interleave(self.out_channels // (self.channels // 8), dim=1)
        h = h + x.replace(skip)
        return h, subdiv


class SparseUnetVaeDecoder(nn.Module):
    """Octree sparse U-Net VAE decoder torso — used directly as the texture
    decoder (``out_channels=6``, ``pred_subdiv=False``); subclassed by
    ``FlexiDualGridVaeDecoder`` for the shape decoder."""

    def __init__(self, config: OctreeVaeDecoderConfig, out_channels: int, pred_subdiv: bool = True) -> None:
        super().__init__()
        self.config = config
        self.out_channels = out_channels
        self.pred_subdiv = pred_subdiv

        model_channels = config.model_channels
        num_blocks = config.num_blocks

        self.from_latent = SparseLinear(config.latent_channels, model_channels[0])
        self.output_layer = SparseLinear(model_channels[-1], out_channels)

        levels: List[nn.ModuleList] = []
        for i in range(len(num_blocks)):
            level = nn.ModuleList(
                _SparseConvNeXtBlock3d(model_channels[i], mlp_ratio=config.mlp_ratio)
                for _ in range(num_blocks[i])
            )
            if i < len(num_blocks) - 1:
                level.append(
                    _SparseResBlockC2S3d(model_channels[i], model_channels[i + 1], pred_subdiv=pred_subdiv)
                )
            levels.append(level)
        self.blocks = nn.ModuleList(levels)

    def forward(
        self,
        x: SparseTensor,
        guide_subs: Optional[List[Optional[SparseTensor]]] = None,
        return_subs: bool = False,
    ) -> SparseTensor | Tuple[SparseTensor, List[Optional[SparseTensor]]]:
        assert guide_subs is None or not self.pred_subdiv, (
            "guide_subs can only be passed to a decoder built with pred_subdiv=False"
        )
        assert not return_subs or self.pred_subdiv, (
            "return_subs can only be requested from a decoder built with pred_subdiv=True"
        )

        h = self.from_latent(x)
        subs: List[Optional[SparseTensor]] = []
        for i, level in enumerate(self.blocks):
            is_last_level = i == len(self.blocks) - 1
            for j, block in enumerate(level):
                is_transition = not is_last_level and j == len(level) - 1
                if is_transition:
                    if self.pred_subdiv:
                        h, sub = block(h)
                        subs.append(sub)
                    else:
                        guide = guide_subs[i] if guide_subs is not None else None
                        h, _ = block(h, subdiv=guide)
                else:
                    h = block(h)
        h = h.replace(F.layer_norm(h.feats, h.feats.shape[-1:]))
        h = self.output_layer(h)

        if return_subs:
            return h, subs
        return h

    def upsample(self, x: SparseTensor, upsample_times: int) -> torch.Tensor:
        """Coords-only fast path: grow the octree ``upsample_times`` levels
        without running the output head, for the shape-decode cascade."""
        assert self.pred_subdiv, "upsample() requires a decoder built with pred_subdiv=True"

        h = self.from_latent(x)
        for i, level in enumerate(self.blocks):
            if i == upsample_times:
                return h.coords
            is_last_level = i == len(self.blocks) - 1
            for j, block in enumerate(level):
                is_transition = not is_last_level and j == len(level) - 1
                if is_transition:
                    h, _ = block(h)
                else:
                    h = block(h)
        return h.coords


class FdgDecoderOutput(NamedTuple):
    """Structured shape-decoder output. Mesh conversion (``o_voxel``'s
    ``flexible_dual_grid_to_mesh``) is a downstream consumer's job, not this
    module's — it needs only ``coords``/``vertices``/``intersected``/
    ``quad_lerp`` (all sharing the same active-voxel coords) plus ``subs`` to
    guide the paired texture decoder."""

    coords: torch.Tensor
    vertices: SparseTensor
    intersected: SparseTensor
    quad_lerp: SparseTensor
    subs: Optional[List[Optional[SparseTensor]]]


def _fdg_head(h: SparseTensor, voxel_margin: float) -> Tuple[SparseTensor, SparseTensor, SparseTensor]:
    """The FDG decoder's 7-channel output head: ``0:3`` a per-vertex offset
    (sigmoid, scaled/shifted so a vertex can land up to ``voxel_margin``
    outside its cell), ``3:6`` per-axis edge-intersection logits (thresholded
    at 0), ``6:7`` the quad interpolation weight (softplus, always >= 0)."""
    vertices = h.replace((1 + 2 * voxel_margin) * torch.sigmoid(h.feats[..., 0:3]) - voxel_margin)
    intersected = h.replace(h.feats[..., 3:6] > 0)
    quad_lerp = h.replace(F.softplus(h.feats[..., 6:7]))
    return vertices, intersected, quad_lerp


class FlexiDualGridVaeDecoder(SparseUnetVaeDecoder):
    """Shape decoder: the ``SparseUnetVaeDecoder`` torso fixed at
    ``out_channels=7``, ``pred_subdiv=True``, plus the FDG head math."""

    def __init__(self, config: OctreeVaeDecoderConfig, resolution: int, voxel_margin: float = 0.5) -> None:
        self.resolution = resolution
        self.voxel_margin = voxel_margin
        super().__init__(config, out_channels=7, pred_subdiv=True)

    def set_resolution(self, resolution: int) -> None:
        self.resolution = resolution

    def forward(self, x: SparseTensor, return_subs: bool = False) -> FdgDecoderOutput:
        if return_subs:
            h, subs = super().forward(x, return_subs=True)
        else:
            h = super().forward(x)
            subs = None
        vertices, intersected, quad_lerp = _fdg_head(h, self.voxel_margin)
        return FdgDecoderOutput(coords=h.coords, vertices=vertices, intersected=intersected, quad_lerp=quad_lerp, subs=subs)
