# Derived from: ByteDance's SeedVR models/video_vae_v3/modules/attn_video_vae.py
# (Apache-2.0). The block-level building blocks (ResnetBlock3D, AttentionBlock3D,
# Down/Upsample3D, Down/UpDecoderBlock3D, UNetMidBlock3D, Encoder3D, Decoder3D,
# and the causal-conv/group-norm helpers) moved to vendor/seedvr2/vae.py
# verbatim. This class stays in src because it extends
# NativeArchModule (PotionUI's own loader contract), owns the config constants
# + latent scaling, and delegates spatial tiling to PotionUI's own shared
# `tiling` module — none of which is ByteDance-derived.

"""SeedVR2 causal video VAE, ported from ByteDance's SeedVR
``models/video_vae_v3/modules/attn_video_vae.py`` (the
``VideoAutoencoderKLWrapper`` -> ``VideoAutoencoderKL`` stack: diffusers
``AutoencoderKL`` blocks *inflated* from 2D to causal-3D -- ``ResnetBlock3D``,
``DownEncoderBlock3D``/``UpDecoderBlock3D``, ``UNetMidBlock3D`` with a diffusers
``Attention`` mid-block, and ``InflatedCausalConv3d`` in place of every conv).
Config: ``s8_c16_t4_inflation_sd3.yaml`` -- ``block_out_channels
[128,256,512,512]``, ``layers_per_block 2``, ``latent_channels 16``,
``norm_num_groups 32``, 8x spatial / 4x temporal downsample, ``use_quant_conv
False``, ``use_post_quant_conv False``, latent ``scaling_factor 0.9152`` /
``shift 0``. Everything is Apache-2.0. The block-level building blocks live in
``vendor/seedvr2/vae.py``; this module keeps the top-level
``SeedVR2CausalVideoVAE`` class, the config constants, and latent scaling.

**Key layout is diffusers ``AutoencoderKL`` verbatim.** The real checkpoint
(``models/vae/ema_vae_fp16.safetensors``: 250 tensors, ``encoder.*``/``decoder.*``
only, no ``quant_conv``/``post_quant_conv``) stores *already-inflated 5D* conv
weights (e.g. ``encoder.conv_in.weight`` is ``[128,3,3,3,3]``). SeedVR's
load-time 2D->3D inflation only fires for 4D source weights, so for this
checkpoint it is a no-op -- we build native 3D convs and load the 5D weights
strict, no rename map (mirrors ``causal_3d.py``'s "Key parity" arrangement:
the causal conv IS a plain ``operations.Conv3d`` whose params sit at the exact
state-dict path, with the causal-padding amount stashed as a plain attribute
and applied by a module-level forward helper -- there is no wrapper submodule).

**Causal padding is first-frame REPLICATE, not zeros** (this is the one real
math difference from ``causal_3d.py``, which zero-left-pads). SeedVR's
``extend_head`` prepends ``2 * temporal_padding`` copies of the first frame
before a temporal conv with its temporal padding removed; ``GroupNorm`` is
applied *per frame* (reshape ``(B,C,T,H,W) -> (B*T,C,H,W)``), i.e. frames are
normalized independently -- both replicated faithfully in ``vendor/seedvr2/vae.py``
(see ``_causal_conv3d_forward`` / ``_causal_group_norm`` there). For a still
image (``T=1``) these reduce to a plain conv/GroupNorm, so the image path is
exact.

**Temporal streaming (SeedVR's ``MemoryState``/``set_causal_slicing`` chunked
conv cache) is intentionally NOT ported** -- this vendor pass targets images
(``T=1``) and short whole-clips processed in one shot, which corresponds to
SeedVR's ``MemoryState.DISABLED`` path (no cross-call memory). Long-clip
temporal slicing (``slicing_sample_min_size 4``) is deferred to the video
phase; ``encode``/``decode`` run the whole temporal axis at once.

**Latent scaling lives INSIDE this module** (like ``ltx_causal_video.py``'s
``per_channel_statistics``, unlike ``causal_3d.py``/Flux which defer per-channel
constants to the engine's ``latent_format``). The reason: SeedVR's scale/shift
is a fixed scalar (``0.9152``/``0``) that is *not* in the checkpoint and has no
per-channel ``latent_format`` table; keeping it here makes the module
self-contained so the generator pipe need not know the magic constant.
``encode`` returns the scaled distribution **mode** (the mean -- deterministic,
matching SeedVR inference's ``use_sample=False`` / ``posterior.mode()`` path,
which is what a deterministic upscaler wants); ``decode`` inverts the scaling.

**Spatial tiling** reuses ``tiling.tiled_encode_causal3d`` /
``tiled_decode_causal3d`` unchanged (SeedVR2 is 8x spatial over a 5D
``(B,C,T,H,W)`` tensor -- exactly their contract), exposed as the
``tiled_encode``/``tiled_decode`` convenience methods.
"""

from __future__ import annotations

import logging
from typing import Any

import torch

from vendor.seedvr2.vae import Decoder3D, Encoder3D

from ..base import NativeArchModule
from .tiling import tiled_decode_causal3d, tiled_encode_causal3d

logger = logging.getLogger(__name__)

# SeedVR2 s8_c16_t4 config (from s8_c16_t4_inflation_sd3.yaml). Detection
# (owned by detect/) may pass a partial config; from_config fills these in.
SEEDVR2_VAE_CONFIG: dict[str, Any] = {
    "in_channels": 3,
    "out_channels": 3,
    "block_out_channels": (128, 256, 512, 512),
    "layers_per_block": 2,
    "latent_channels": 16,
    "norm_num_groups": 32,
    "temporal_scale_num": 2,  # 2 temporal down/up stages -> 4x temporal
}

# Latent scaling (configs_3b/main.yaml + configs_7b/main.yaml: scaling_factor
# 0.9152, shifting_factor defaults to 0.0). Applied inside encode/decode.
SCALING_FACTOR = 0.9152
SHIFT_FACTOR = 0.0

LATENT_CHANNELS = SEEDVR2_VAE_CONFIG["latent_channels"]
SPATIAL_DOWNSCALE = 8
TEMPORAL_DOWNSCALE = 4


class SeedVR2CausalVideoVAE(NativeArchModule):
    """SeedVR2 causal video VAE. ``encode``/``decode`` accept either a 5D
    ``(B,3,T,H,W)`` clip or a 4D ``(B,3,H,W)`` still image (auto-``unsqueeze``/
    ``squeeze`` on the temporal axis, matching SeedVR's ``VideoAutoencoderKL
    Wrapper``). ``encode`` returns the scaled distribution mode (mean); latent
    scaling (``0.9152``/``0``) is applied here (see module docstring)."""

    def __init__(self, *, config: dict[str, Any], operations: Any) -> None:
        super().__init__()
        cfg = {**SEEDVR2_VAE_CONFIG, **(config or {})}
        block_out_channels = tuple(cfg["block_out_channels"])
        self.latent_channels = cfg["latent_channels"]

        self.encoder = Encoder3D(
            in_channels=cfg["in_channels"], latent_channels=cfg["latent_channels"],
            block_out_channels=block_out_channels, layers_per_block=cfg["layers_per_block"],
            norm_num_groups=cfg["norm_num_groups"], temporal_down_num=cfg["temporal_scale_num"],
            operations=operations,
        )
        self.decoder = Decoder3D(
            out_channels=cfg["out_channels"], latent_channels=cfg["latent_channels"],
            block_out_channels=block_out_channels, layers_per_block=cfg["layers_per_block"],
            norm_num_groups=cfg["norm_num_groups"], temporal_up_num=cfg["temporal_scale_num"],
            operations=operations,
        )

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "SeedVR2CausalVideoVAE":
        return cls(config=config, operations=operations)

    def post_load(self) -> None:
        # No computed/derived buffers: every parameter is a loaded weight, and
        # the causal replicate-padding is recomputed per forward from each
        # conv's stashed temporal-pad count (not a persisted buffer).
        return None

    # -- core video-shaped API ---------------------------------------------- #

    def encode(self, pixels: torch.Tensor) -> torch.Tensor:
        """``pixels``: ``(B,3,T,H,W)`` or ``(B,3,H,W)`` in [-1, 1]. Returns the
        scaled latent mode -- ``(B,16,T',H/8,W/8)`` (or ``(B,16,H/8,W/8)`` for a
        4D input)."""
        squeeze = pixels.ndim == 4
        if squeeze:
            pixels = pixels.unsqueeze(2)
        moments = self.encoder(pixels)
        mean, _logvar = torch.chunk(moments, 2, dim=1)
        latent = (mean - SHIFT_FACTOR) * SCALING_FACTOR
        return latent.squeeze(2) if squeeze else latent

    def decode(self, latent: torch.Tensor) -> torch.Tensor:
        """``latent``: scaled ``(B,16,T,H,W)`` or ``(B,16,H,W)``. Returns pixels
        ``(B,3,T',H*8,W*8)`` (or ``(B,3,H*8,W*8)`` for a 4D input) in [-1, 1]."""
        squeeze = latent.ndim == 4
        if squeeze:
            latent = latent.unsqueeze(2)
        z = latent / SCALING_FACTOR + SHIFT_FACTOR
        pixels = self.decoder(z)
        return pixels.squeeze(2) if squeeze else pixels

    # -- image-shaped convenience aliases ----------------------------------- #

    def encode_image(self, pixels: torch.Tensor) -> torch.Tensor:
        """``pixels``: ``(B,3,H,W)`` in [-1, 1]. Returns ``(B,16,H/8,W/8)``."""
        return self.encode(pixels if pixels.ndim == 4 else pixels.squeeze(2))

    def decode_image(self, latent: torch.Tensor) -> torch.Tensor:
        """``latent``: ``(B,16,H,W)``. Returns ``(B,3,H*8,W*8)`` in [-1, 1]."""
        return self.decode(latent if latent.ndim == 4 else latent.squeeze(2))

    # -- spatial tiling (reuses the causal-3D tiling utilities) ------------- #

    def tiled_encode(self, pixels: torch.Tensor, tile_size: int = 512, overlap: int = 64) -> torch.Tensor:
        """Memory-bounded spatial tiled encode (8x spatial, 5D) -- delegates to
        the shared ``tiled_encode_causal3d`` (SeedVR2 matches its contract)."""
        return tiled_encode_causal3d(self, pixels, tile_size=tile_size, overlap=overlap)

    def tiled_decode(self, latent: torch.Tensor, tile_size: int = 256, overlap: int = 32) -> torch.Tensor:
        """Memory-bounded spatial tiled decode (8x spatial, 5D) -- delegates to
        the shared ``tiled_decode_causal3d``."""
        return tiled_decode_causal3d(self, latent, tile_size=tile_size, overlap=overlap)
