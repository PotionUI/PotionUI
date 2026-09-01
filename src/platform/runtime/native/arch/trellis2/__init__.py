"""TRELLIS.2 sparse-structure arch package: the dense flow DiT + VAE decoder."""

from .config import SS_FLOW_PRODUCTION, SS_VAE_DECODER_PRODUCTION, SSFlowConfig, SSVAEDecoderConfig
from .ss_flow import SSFlowDiT
from .ss_vae import SSVAEDecoder

__all__ = [
    "SSFlowDiT",
    "SSFlowConfig",
    "SS_FLOW_PRODUCTION",
    "SSVAEDecoder",
    "SSVAEDecoderConfig",
    "SS_VAE_DECODER_PRODUCTION",
]
