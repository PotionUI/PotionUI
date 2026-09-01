"""TRELLIS.2 sparse-structure arch package: the dense flow DiT + VAE decoder."""

from .config import (
    OCTREE_VAE_DECODER_TORSO_PRODUCTION,
    SS_FLOW_PRODUCTION,
    SS_VAE_DECODER_PRODUCTION,
    OctreeVaeDecoderConfig,
    SSFlowConfig,
    SSVAEDecoderConfig,
)
from .octree_vae import FdgDecoderOutput, FlexiDualGridVaeDecoder, SparseUnetVaeDecoder
from .ss_flow import SSFlowDiT
from .ss_vae import SSVAEDecoder

__all__ = [
    "SSFlowDiT",
    "SSFlowConfig",
    "SS_FLOW_PRODUCTION",
    "SSVAEDecoder",
    "SSVAEDecoderConfig",
    "SS_VAE_DECODER_PRODUCTION",
    "OctreeVaeDecoderConfig",
    "OCTREE_VAE_DECODER_TORSO_PRODUCTION",
    "SparseUnetVaeDecoder",
    "FlexiDualGridVaeDecoder",
    "FdgDecoderOutput",
]
