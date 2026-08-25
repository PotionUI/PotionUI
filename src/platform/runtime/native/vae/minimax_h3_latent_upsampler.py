# Architecture reconstructed from the state-dict layout of the Apache-2.0
# checkpoint `LBH-123-AI/Minimax_h3_latent_Upscaler` /
# `minimax_h3_latent_upscaler_3d_bf16.safetensors` (real header saved at
# `ai/minimax_h3/latent_upscaler_3d_bf16_header.json` -- 322 tensors, no
# `__metadata__`). Interface semantics (scale-embedding input, mid-stack
# trilinear resize, per-channel latent stats) are interoperability facts
# observed from the reference ComfyUI node repo
# (`LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler`, commit 04f7159, no
# declared license -- no code copied; re-expressed in this engine's own idiom).
"""MiniMax-H3 3D latent upsampler: an ADM-style (adaGN) conv-3D UNet stack
that resizes a latent's temporal/spatial extent in latent space, conditioned
on a single scalar "how much bigger" embedding.

**Key-layout note**: every parameterised layer routes through ``operations``
(the fp8/manual-cast seam), matching the house convention this package
already applies to the LTX latent upsampler (see ``ltx_latent_upsampler.py``'s
module docstring for why) -- purely additive, the key set below is unchanged
either way.

**Stack layout.** ``in_blocks``/``out_blocks`` are each built by walking
``range(num_res_blocks)`` and, after every ResBlock, inserting a
TemporalBlock whenever the loop index is even (``temporal_every=2``):
ResBlocks land at indices 0,2,3,5,6,8,9,11,12,14,15,17 and TemporalBlocks at
1,4,7,10,13,16 -- verified against the real header's key set exactly (a
ResBlock's marker is ``in_layers.*``, a TemporalBlock's is ``dwconv.*``).

**ResBlock (adaGN).** Same shape as the guided-diffusion/ADM ``ResBlock``:
``in_layers`` (GroupNorm, SiLU, Conv3d) computes ``h``; ``emb_layers``
(SiLU, Linear) projects the scale embedding to ``2*channels`` and splits it
into a scale/shift pair applied to ``out_norm(h)`` before ``out_layers``
(SiLU, Dropout, Conv3d). The skip path is a bare identity add (equal
in/out channels throughout -- no skip-conv key exists in the header).

**Scale embedding.** ``embed`` takes a *single scalar per batch*
(``effective_scale - 1.0``, or ``0.0`` when no scale is requested) through
Linear(1->64), SiLU, Linear(64->64) -- not a per-position/per-token
embedding. The same embedding is computed once per forward and shared by
every ResBlock in both stacks.

**Resize point.** The spatial/temporal resize happens once, between the two
block stacks, via ``F.interpolate(..., mode="trilinear")`` to the caller's
``target_size`` -- unlike the LTX upsampler's fixed-ratio pixel-shuffle, this
one targets an arbitrary caller-given ``(T, H, W)``. No global residual
(unlike a typical UNet, ``conv_in``'s output never gets added back at the end
-- there is no key in the header for one).

**Per-channel latent normalization.** ``MEAN``/``STD`` are interoperability
data (not shape-derived, not present as checkpoint tensors here) copied
verbatim from the reference ComfyUI node's measured H3 VAE latent
distribution. The sandwich itself (normalize before forward, denormalize
after) is the caller's concern (the upscaler pipe) -- these two helpers just
give the constants one home instead of being copy-pasted at every call site.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule

MEAN: tuple[float, ...] = (
    0.858090341091156, -0.9606591463088989, 1.0661640167236328, -0.5090325474739075,
    -0.2727581858634949, -1.3675414323806763, -0.2553254961967468, -0.26907554268836975,
    -0.5376840829849243, -0.0464097298681736, 0.6657370328903198, 0.19690127670764923,
    -0.5460608005523682, -0.4035342037677765, -0.23683024942874908, 0.25928452610969543,
    -0.30133944749832153, 0.211341992020607, -1.1206848621368408, 0.3581933379173279,
    -0.04225143790245056, 0.2604829967021942, 0.22864092886447906, 0.7056031823158264,
)
STD: tuple[float, ...] = (
    1.2223774194717407, 1.2767263650894165, 1.6831774711608887, 1.7549455165863037,
    1.5636216402053833, 2.194143533706665, 0.9653137922286987, 1.0569885969161987,
    0.841948926448822, 0.7729952931404114, 1.8955937623977661, 0.946841835975647,
    0.7996809482574463, 0.44988900423049927, 0.7197399735450745, 0.6936293244361877,
    2.961095094680786, 2.7694199085235596, 3.0496184825897217, 2.1088054180145264,
    3.276226282119751, 3.1627357006073, 2.2816812992095947, 2.6127843856811523,
)

_NUM_GROUPS = 32


def normalize_h3_latent(x: torch.Tensor) -> torch.Tensor:
    mean = torch.tensor(MEAN, dtype=x.dtype, device=x.device).view(1, -1, 1, 1, 1)
    std = torch.tensor(STD, dtype=x.dtype, device=x.device).view(1, -1, 1, 1, 1)
    return (x - mean) / std


def denormalize_h3_latent(x: torch.Tensor) -> torch.Tensor:
    mean = torch.tensor(MEAN, dtype=x.dtype, device=x.device).view(1, -1, 1, 1, 1)
    std = torch.tensor(STD, dtype=x.dtype, device=x.device).view(1, -1, 1, 1, 1)
    return x * std + mean


class _MiniMaxH3ResBlock(nn.Module):
    def __init__(self, channels: int, embed_dim: int, dropout: float, *, operations: Any) -> None:
        super().__init__()
        self.in_layers = nn.Sequential(
            operations.GroupNorm(_NUM_GROUPS, channels),
            nn.SiLU(),
            operations.Conv3d(channels, channels, kernel_size=3, padding=1),
        )
        self.emb_layers = nn.Sequential(
            nn.SiLU(),
            operations.Linear(embed_dim, 2 * channels),
        )
        self.out_norm = operations.GroupNorm(_NUM_GROUPS, channels)
        self.out_layers = nn.Sequential(
            nn.SiLU(),
            nn.Dropout(dropout),
            operations.Conv3d(channels, channels, kernel_size=3, padding=1),
        )

    def forward(self, x: torch.Tensor, emb: torch.Tensor) -> torch.Tensor:
        h = self.in_layers(x)
        emb_out = self.emb_layers(emb).to(h.dtype)
        emb_out = emb_out[:, :, None, None, None]
        scale, shift = emb_out.chunk(2, dim=1)
        h = self.out_norm(h) * (1 + scale) + shift
        h = self.out_layers(h)
        return x + h


class _MiniMaxH3TemporalBlock(nn.Module):
    def __init__(self, channels: int, kernel_size: int, *, operations: Any) -> None:
        super().__init__()
        self.norm = operations.GroupNorm(_NUM_GROUPS, channels)
        self.dwconv = operations.Conv3d(
            channels, channels, kernel_size=(kernel_size, 1, 1),
            padding=(kernel_size // 2, 0, 0), groups=channels,
        )
        self.pwconv = operations.Conv3d(channels, channels, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.pwconv(self.dwconv(F.silu(self.norm(x))))


def _build_block_stack(
    channels: int, num_res_blocks: int, temporal_every: int, temporal_kernel: int,
    embed_dim: int, dropout: float, operations: Any,
) -> nn.ModuleList:
    blocks = nn.ModuleList()
    for b in range(num_res_blocks):
        blocks.append(_MiniMaxH3ResBlock(channels, embed_dim, dropout, operations=operations))
        if b % temporal_every == 0:
            blocks.append(_MiniMaxH3TemporalBlock(channels, temporal_kernel, operations=operations))
    return blocks


class MiniMaxH3LatentUpsampler(NativeArchModule):
    """Resizes a MiniMax-H3 video-VAE latent's ``(T, H, W)`` extent in latent
    space, conditioned on a scalar scale embedding -- see module docstring.
    """

    def __init__(
        self, *, in_channels: int = 24, channels: int = 512, num_res_blocks: int = 12,
        temporal_every: int = 2, temporal_kernel: int = 5, embed_dim: int = 64,
        dropout: float = 0.1, operations: Any,
    ) -> None:
        super().__init__()
        self.in_channels = in_channels
        self.channels = channels
        self.num_res_blocks = num_res_blocks
        self.embed_dim = embed_dim

        self.conv_in = operations.Conv3d(in_channels, channels, kernel_size=3, padding=1)
        self.embed = nn.Sequential(
            operations.Linear(1, embed_dim),
            nn.SiLU(),
            operations.Linear(embed_dim, embed_dim),
        )
        self.in_blocks = _build_block_stack(
            channels, num_res_blocks, temporal_every, temporal_kernel, embed_dim, dropout, operations
        )
        self.out_blocks = _build_block_stack(
            channels, num_res_blocks, temporal_every, temporal_kernel, embed_dim, dropout, operations
        )
        self.norm_out = operations.GroupNorm(_NUM_GROUPS, channels)
        self.conv_out = operations.Conv3d(channels, in_channels, kernel_size=3, padding=1)

    @classmethod
    def from_config(cls, config: dict, operations: Any) -> "MiniMaxH3LatentUpsampler":
        return cls(
            in_channels=config.get("in_channels", 24),
            channels=config.get("channels", 512),
            num_res_blocks=config.get("num_res_blocks", 12),
            temporal_every=config.get("temporal_every", 2),
            temporal_kernel=config.get("temporal_kernel", 5),
            embed_dim=config.get("embed_dim", 64),
            dropout=config.get("dropout", 0.1),
            operations=operations,
        )

    def post_load(self) -> None:
        return None

    def forward(
        self, latent: torch.Tensor, *, scale: float | None = None,
        target_size: tuple[int, int, int],
    ) -> torch.Tensor:
        _, _, t, h, w = latent.shape
        target_t, target_h, target_w = target_size
        if (t, h, w) == (target_t, target_h, target_w):
            return latent

        scale_value = float(scale) - 1.0 if scale is not None else 0.0
        embed_ref = self.embed[0].weight
        scalar = torch.full((1, 1), scale_value, dtype=embed_ref.dtype, device=embed_ref.device)
        emb = self.embed(scalar).expand(latent.shape[0], -1)

        x = self.conv_in(latent)
        for block in self.in_blocks:
            x = block(x, emb) if isinstance(block, _MiniMaxH3ResBlock) else block(x)
        x = F.interpolate(x, size=(target_t, target_h, target_w), mode="trilinear", align_corners=False)
        for block in self.out_blocks:
            x = block(x, emb) if isinstance(block, _MiniMaxH3ResBlock) else block(x)
        return self.conv_out(F.silu(self.norm_out(x)))
