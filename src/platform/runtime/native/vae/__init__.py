"""Native-engine VAEs: the Flux-family 2D image VAE, the Wan-2.1/2.2-shaped
causal 3D VAEs (Qwen-Image/Krea-2/Wan), and the LTX-2/2.3 causal video VAE,
plus key-layout handling and spatial tiling."""

from __future__ import annotations

from .ae_2d import LATENT_SCALE_SHIFT, AutoEncoder2D
from .causal_3d import LATENT_CHANNELS as CAUSAL3D_LATENT_CHANNELS
from .causal_3d import LATENTS_MEAN as CAUSAL3D_LATENTS_MEAN
from .causal_3d import LATENTS_STD as CAUSAL3D_LATENTS_STD
from .causal_3d import AutoEncoderCausal3D
from .causal_3d_v2 import LATENT_CHANNELS as CAUSAL3D_V2_LATENT_CHANNELS
from .causal_3d_v2 import LATENT_SCALE_FACTOR as CAUSAL3D_V2_SCALE_FACTOR
from .causal_3d_v2 import AutoEncoderCausal3D_2_2
from .loader import (
    load_causal3d_v2_vae,
    load_causal3d_vae,
    load_ltx_audio_vae,
    load_ltx_latent_upsampler,
    load_ltx_video_vae,
    load_ltx_vocoder,
    load_vae,
)
from .ltx_audio import LATENT_DOWNSAMPLE_FACTOR as LTX_AUDIO_LATENT_DOWNSAMPLE_FACTOR
from .ltx_audio import LTXAudioAutoencoder, LTXVocoder, LTXVocoderAMP, decode_audio_waveform
from .ltx_causal_video import LTXCausalVideoVAE
from .ltx_latent_upsampler import LTXLatentUpsampler
from .tiling import (
    VAE_SPATIAL_DOWNSCALE,
    auto_tile_size,
    tiled_decode,
    tiled_decode_causal3d,
    tiled_encode,
    tiled_encode_causal3d,
)

__all__ = [
    "CAUSAL3D_LATENTS_MEAN",
    "CAUSAL3D_LATENTS_STD",
    "CAUSAL3D_LATENT_CHANNELS",
    "CAUSAL3D_V2_LATENT_CHANNELS",
    "CAUSAL3D_V2_SCALE_FACTOR",
    "LATENT_SCALE_SHIFT",
    "LTX_AUDIO_LATENT_DOWNSAMPLE_FACTOR",
    "VAE_SPATIAL_DOWNSCALE",
    "AutoEncoder2D",
    "AutoEncoderCausal3D",
    "AutoEncoderCausal3D_2_2",
    "LTXAudioAutoencoder",
    "LTXCausalVideoVAE",
    "LTXLatentUpsampler",
    "LTXVocoder",
    "LTXVocoderAMP",
    "auto_tile_size",
    "decode_audio_waveform",
    "load_causal3d_v2_vae",
    "load_causal3d_vae",
    "load_ltx_audio_vae",
    "load_ltx_latent_upsampler",
    "load_ltx_video_vae",
    "load_ltx_vocoder",
    "load_vae",
    "tiled_decode",
    "tiled_decode_causal3d",
    "tiled_encode",
    "tiled_encode_causal3d",
]
