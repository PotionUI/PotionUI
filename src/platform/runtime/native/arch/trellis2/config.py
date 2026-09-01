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

from dataclasses import asdict, dataclass
from typing import Any, Dict, Tuple


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
# `num_res_blocks` is 2, not the 1 this port first carried: the Comfy-Org shape
# VAE's `struct_dec.` slice holds 74 tensors, which is what 2 produces (1 gives
# 50, 3 gives 98) — see `tests/.../trellis2/test_load.py`'s key-coverage parity.
SS_VAE_DECODER_PRODUCTION = SSVAEDecoderConfig(
    out_channels=1,
    latent_channels=8,
    num_res_blocks=2,
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


@dataclass(frozen=True)
class SLatFlowConfig:
    """``slat_flow.SLatFlowModel`` hyper-parameters. Unlike the dense
    sparse-structure DiT, ``SLatFlowModel`` takes flat kwargs, so this config
    exists to name the four production variants — ``as_kwargs()`` is what the
    constructor is called with."""

    resolution: int
    in_channels: int
    out_channels: int
    model_channels: int = 1536
    cond_channels: int = 1024
    num_blocks: int = 30
    num_heads: int = 12
    mlp_ratio: float = 5.3334
    pe_mode: str = "rope"
    share_mod: bool = True
    qk_rms_norm: bool = True
    qk_rms_norm_cross: bool = True

    def __post_init__(self) -> None:
        if self.model_channels % self.num_heads != 0:
            raise ValueError(
                f"model_channels {self.model_channels} not divisible by num_heads {self.num_heads}"
            )

    def as_kwargs(self) -> Dict[str, Any]:
        return asdict(self)


# The four SLat flow variants the Comfy-Org checkpoint carries, verbatim from
# the matching `ckpts/<name>.json` in `microsoft/TRELLIS.2-4B`. The texture flow
# takes 64 in-channels because the shape latent is concatenated onto the noise.
SHAPE_SLAT_FLOW_512 = SLatFlowConfig(resolution=32, in_channels=32, out_channels=32)
SHAPE_SLAT_FLOW_1024 = SLatFlowConfig(resolution=64, in_channels=32, out_channels=32)
TEX_SLAT_FLOW_512 = SLatFlowConfig(resolution=32, in_channels=64, out_channels=32)
TEX_SLAT_FLOW_1024 = SLatFlowConfig(resolution=64, in_channels=64, out_channels=32)


@dataclass(frozen=True)
class DinoV3Config:
    """DINOv3 ViT-L/16 construction values for the image conditioner.

    Upstream builds this encoder with
    ``DINOv3ViTModel.from_pretrained("facebook/dinov3-vitl16-pretrain-lvd1689m")``,
    a **gated** repo that downloads on first use. The Comfy-Org single file
    carries the same weights and no config, so these values are derived from the
    checkpoint's own tensor shapes: 415 tensors, every key and shape identical to
    ``DINOv3ViTModel(config).state_dict()`` after the block remap.
    ``num_attention_heads`` is the one value shapes cannot pin down (q/k/v are
    square); 16 is ViT-L's convention, head_dim 64."""

    hidden_size: int = 1024
    num_hidden_layers: int = 24
    num_attention_heads: int = 16
    intermediate_size: int = 4096
    patch_size: int = 16
    num_register_tokens: int = 4

    def __post_init__(self) -> None:
        if self.hidden_size % self.num_attention_heads != 0:
            raise ValueError(
                f"hidden_size {self.hidden_size} not divisible by "
                f"num_attention_heads {self.num_attention_heads}"
            )

    def as_kwargs(self) -> Dict[str, Any]:
        return asdict(self)

    def num_tokens(self, image_size: int) -> int:
        """Sequence length the encoder emits for a square ``image_size`` input:
        one patch per 16x16 cell, plus the CLS token and the register tokens."""
        if image_size % self.patch_size != 0:
            raise ValueError(
                f"image_size {image_size} is not a multiple of patch_size {self.patch_size}"
            )
        return (image_size // self.patch_size) ** 2 + 1 + self.num_register_tokens


DINO_V3_VIT_L16 = DinoV3Config()

#: The two square input sizes TRELLIS.2 conditions on. The 512 tier runs the
#: first only; the cascades condition each stage on the size it was trained at.
DINO_CONDITION_SIZES = (512, 1024)


@dataclass(frozen=True)
class SlatNormalization:
    """Per-channel mean/std a SLat latent is normalised by before its flow model
    sees it, and denormalised by before the decoder does."""

    mean: Tuple[float, ...]
    std: Tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.mean) != len(self.std):
            raise ValueError(
                f"mean ({len(self.mean)}) and std ({len(self.std)}) must have the same length"
            )
        if any(s == 0 for s in self.std):
            raise ValueError("std has a zero channel; normalization would divide by zero")

    @property
    def channels(self) -> int:
        return len(self.mean)


# Verbatim from the released `pipeline.json` (via the trellis2 generator plugin's
# SHAPE_SLAT_NORMALIZATION / TEX_SLAT_NORMALIZATION).
SHAPE_SLAT_NORMALIZATION = SlatNormalization(
    mean=(
        0.781296, 0.018091, -0.495192, -0.558457, 1.06053, 0.093252, 1.518149,
        -0.933218, -0.732996, 2.604095, -0.118341, -2.143904, 0.495076, -2.179512,
        -2.130751, -0.996944, 0.261421, -2.217463, 1.260067, -0.150213, 3.790713,
        1.481266, -1.046058, -1.523667, -0.059621, 2.22078, 1.621212, 0.87723,
        0.567247, -3.175944, -3.186688, 1.578665,
    ),
    std=(
        5.972266, 4.706852, 5.44501, 5.209927, 5.32022, 4.547237, 5.020802,
        5.444004, 5.226681, 5.683095, 4.831436, 5.286469, 5.652043, 5.367606,
        5.525084, 4.730578, 4.805265, 5.124013, 5.530808, 5.619001, 5.10393,
        5.41767, 5.269677, 5.547194, 5.634698, 5.235274, 6.110351, 5.511298,
        6.237273, 4.879207, 5.347008, 5.405691,
    ),
)

TEX_SLAT_NORMALIZATION = SlatNormalization(
    mean=(
        3.501659, 2.212398, 2.226094, 0.251093, -0.026248, -0.687364, 0.439898,
        -0.928075, 0.029398, -0.339596, -0.869527, 1.038479, -0.972385, 0.126042,
        -1.129303, 0.455149, -1.209521, 2.069067, 0.544735, 2.569128, -0.323407,
        2.293, -1.925608, -1.217717, 1.213905, 0.971588, -0.023631, 0.10675,
        2.021786, 0.250524, -0.662387, -0.768862,
    ),
    std=(
        2.665652, 2.743913, 2.765121, 2.595319, 3.037293, 2.291316, 2.144656,
        2.911822, 2.969419, 2.501689, 2.154811, 3.163343, 2.621215, 2.381943,
        3.186697, 3.021588, 2.295916, 3.234985, 3.233086, 2.26014, 2.874801,
        2.810596, 3.29272, 2.674999, 2.680878, 2.372054, 2.451546, 2.353556,
        2.995195, 2.379849, 2.786195, 2.77519,
    ),
)


@dataclass(frozen=True)
class StageSampling:
    """One cascade stage's flow-Euler sampler settings.

    ``guidance_interval`` is the ``[start, end]`` fraction of the schedule over
    which guidance is applied at all — outside it the stage runs a single
    conditional forward. ``rescale_t`` reshapes the timestep grid.
    """

    steps: int
    guidance_strength: float
    guidance_rescale: float
    guidance_interval: Tuple[float, float]
    rescale_t: float
    sigma_min: float = 1e-05


# Verbatim from the released `pipeline.json`. All three stages use
# `FlowEulerGuidanceIntervalSampler`; only the parameters differ.
STAGE_SAMPLING = {
    "sparse_structure": StageSampling(
        steps=12, guidance_strength=7.5, guidance_rescale=0.7,
        guidance_interval=(0.6, 1.0), rescale_t=5.0,
    ),
    "shape": StageSampling(
        steps=12, guidance_strength=7.5, guidance_rescale=0.5,
        guidance_interval=(0.6, 1.0), rescale_t=3.0,
    ),
    "texture": StageSampling(
        steps=12, guidance_strength=1.0, guidance_rescale=0.0,
        guidance_interval=(0.6, 0.9), rescale_t=3.0,
    ),
}
