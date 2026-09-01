# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/models/sparse_structure_flow.py
# (SparseStructureFlowModel.__init__ kwargs) and trellis2/models/sparse_structure_vae.py
# (SparseStructureDecoder.__init__ kwargs). The decoder's channel schedule/block counts
# are not present in this TRELLIS.2 checkout (it loads the decoder from a pretrained
# TRELLIS-v1 checkpoint, `ss_dec_conv3d_16l8_fp16`, rather than shipping its own config) —
# SS_VAE_DECODER_PRODUCTION below reproduces the published TRELLIS-v1 values.
"""``SSFlowConfig``/``SSVAEDecoderConfig`` — construction configs for TRELLIS.2's dense
sparse-structure flow DiT (``SparseStructureFlowModel``) and its VAE decoder
(``SparseStructureDecoder``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class SSFlowConfig:
    """``SparseStructureFlowModel`` hyper-parameters."""

    resolution: int
    in_channels: int
    model_channels: int
    cond_channels: int
    out_channels: int
    num_blocks: int
    num_heads: int
    mlp_ratio: float = 4.0
    pe_mode: str = "rope"
    rope_freq: Tuple[float, float] = (1.0, 10000.0)
    share_mod: bool = False
    qk_rms_norm: bool = False
    qk_rms_norm_cross: bool = False

    def __post_init__(self) -> None:
        if self.model_channels % self.num_heads != 0:
            raise ValueError(
                f"model_channels {self.model_channels} not divisible by num_heads {self.num_heads}"
            )
        if self.pe_mode not in ("ape", "rope"):
            raise ValueError(f"unsupported pe_mode {self.pe_mode!r}")

    @property
    def head_dim(self) -> int:
        return self.model_channels // self.num_heads

    @property
    def num_tokens(self) -> int:
        return self.resolution**3


@dataclass(frozen=True)
class SSVAEDecoderConfig:
    """``SparseStructureDecoder`` hyper-parameters."""

    out_channels: int
    latent_channels: int
    num_res_blocks: int
    channels: Tuple[int, ...]
    num_res_blocks_middle: int = 2
    norm_type: str = "layer"


# Comfy-Org checkpoint hyperparameters (`model.structure_model.*`), matching
# configs/gen/ss_flow_img_dit_1_3B_64_bf16.json in the vendored tree.
SS_FLOW_PRODUCTION = SSFlowConfig(
    resolution=16,
    in_channels=8,
    model_channels=1536,
    cond_channels=1024,
    out_channels=8,
    num_blocks=30,
    num_heads=12,
    mlp_ratio=5.3334,
    pe_mode="rope",
    share_mod=True,
    qk_rms_norm=True,
    qk_rms_norm_cross=True,
)

# TRELLIS-v1 `ss_dec_conv3d_16l8_fp16` schedule: 16^3 latent -> 64^3 occupancy
# (two UpsampleBlock3d, factor 2 each), channels 512 -> 128 -> 32.
SS_VAE_DECODER_PRODUCTION = SSVAEDecoderConfig(
    out_channels=1,
    latent_channels=8,
    num_res_blocks=1,
    channels=(512, 128, 32),
    num_res_blocks_middle=2,
    norm_type="layer",
)


@dataclass(frozen=True)
class OctreeVaeDecoderConfig:
    """Shared torso hyper-parameters for both octree sparse U-Net VAE decoders
    (``octree_vae.SparseUnetVaeDecoder`` / ``octree_vae.FlexiDualGridVaeDecoder``).
    Mirrors upstream's shared ``_SC_VAE_DECODER_BASE`` dict — ``out_channels``/
    ``pred_subdiv`` differ per decoder and are passed to the decoder directly,
    not carried on this config."""

    model_channels: Tuple[int, ...]
    latent_channels: int
    num_blocks: Tuple[int, ...]
    mlp_ratio: float = 4.0

    def __post_init__(self) -> None:
        if len(self.model_channels) != len(self.num_blocks):
            raise ValueError(
                f"model_channels ({len(self.model_channels)} levels) and num_blocks "
                f"({len(self.num_blocks)} levels) must have the same length"
            )


# `_SC_VAE_DECODER_BASE` in the trellis2 generator plugin's
# `pipes/generator_trellis2/main.py`, shared by `shape_slat_decoder` and
# `tex_slat_decoder`.
OCTREE_VAE_DECODER_TORSO_PRODUCTION = OctreeVaeDecoderConfig(
    model_channels=(1024, 512, 256, 128, 64),
    latent_channels=32,
    num_blocks=(4, 16, 8, 4, 0),
)
