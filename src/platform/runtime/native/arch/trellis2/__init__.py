"""TRELLIS.2 arch package: the flow DiTs, the VAE decoders, the DINOv3 image
conditioner, and the loaders that build them from the Comfy-Org depot files."""

from .conditioner import DinoV3ImageConditioner, build_dino_v3, load_dino_v3
from .config import (
    DINO_CONDITION_SIZES,
    DINO_V3_VIT_L16,
    OCTREE_VAE_DECODER_TORSO_PRODUCTION,
    SHAPE_SLAT_FLOW_512,
    SHAPE_SLAT_FLOW_1024,
    SHAPE_SLAT_NORMALIZATION,
    SS_FLOW_PRODUCTION,
    SS_VAE_DECODER_PRODUCTION,
    STAGE_SAMPLING,
    TEX_SLAT_FLOW_512,
    TEX_SLAT_FLOW_1024,
    TEX_SLAT_NORMALIZATION,
    DinoV3Config,
    OctreeVaeDecoderConfig,
    SlatNormalization,
    SLatFlowConfig,
    SSFlowConfig,
    SSVAEDecoderConfig,
    StageSampling,
)
from .detect import (
    FLOW_BUNDLE,
    IMAGE_ENCODER,
    SHAPE_VAE,
    TEXTURE_VAE,
    TRELLIS2_ROLES,
    detect_trellis2_role,
    detect_trellis2_role_from_filename,
    trellis2_role_of_file,
)
from .load import (
    load_dino_conditioner,
    load_shape_slat_decoder,
    load_shape_slat_flow,
    load_ss_flow,
    load_ss_vae_decoder,
    load_tex_slat_decoder,
    load_tex_slat_flow,
)
from .octree_vae import FdgDecoderOutput, FlexiDualGridVaeDecoder, SparseUnetVaeDecoder
from .postprocess import PBR_ATTR_LAYOUT, build_textured_mesh, postprocess_to_glb
from .slat_flow import SLatFlowModel
from .ss_flow import SSFlowDiT
from .ss_vae import SSVAEDecoder

__all__ = [
    # models
    "SSFlowDiT",
    "SLatFlowModel",
    "SSVAEDecoder",
    "SparseUnetVaeDecoder",
    "FlexiDualGridVaeDecoder",
    "FdgDecoderOutput",
    "DinoV3ImageConditioner",
    "build_dino_v3",
    "load_dino_v3",
    # configs
    "SSFlowConfig",
    "SS_FLOW_PRODUCTION",
    "SSVAEDecoderConfig",
    "SS_VAE_DECODER_PRODUCTION",
    "SLatFlowConfig",
    "SHAPE_SLAT_FLOW_512",
    "SHAPE_SLAT_FLOW_1024",
    "TEX_SLAT_FLOW_512",
    "TEX_SLAT_FLOW_1024",
    "OctreeVaeDecoderConfig",
    "OCTREE_VAE_DECODER_TORSO_PRODUCTION",
    "DinoV3Config",
    "DINO_V3_VIT_L16",
    "DINO_CONDITION_SIZES",
    "SlatNormalization",
    "SHAPE_SLAT_NORMALIZATION",
    "TEX_SLAT_NORMALIZATION",
    "StageSampling",
    "STAGE_SAMPLING",
    # depot files
    "TRELLIS2_ROLES",
    "FLOW_BUNDLE",
    "SHAPE_VAE",
    "TEXTURE_VAE",
    "IMAGE_ENCODER",
    "detect_trellis2_role",
    "detect_trellis2_role_from_filename",
    "trellis2_role_of_file",
    "load_ss_flow",
    "load_shape_slat_flow",
    "load_tex_slat_flow",
    "load_ss_vae_decoder",
    "load_shape_slat_decoder",
    "load_tex_slat_decoder",
    "load_dino_conditioner",
    # mesh post-processing
    "postprocess_to_glb",
    "build_textured_mesh",
    "PBR_ATTR_LAYOUT",
]
