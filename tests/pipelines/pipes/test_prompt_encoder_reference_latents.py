from __future__ import annotations

import numpy as np
import torch

from src.pipelines.pipes._shared.generation.refmods import RefMod
from src.pipelines.pipes.prompt_encoder.main import _decode_refmod_media
from src.platform.runtime.native.kernels import disable_weight_init
from src.platform.runtime.native.vae.minimax_h3_video import MiniMaxH3VideoVAE

TINY_VAE = dict(
    latent_channels=24,
    block_out_channels=(8, 16),
    layers_per_block=1,
    spatial_downsample_factors=(2, 1),
    temporal_downsample_factors=(1, 2),
    norm_num_groups=2,
    decoder_num_layers=1,
    decoder_num_attention_heads=2,
    decoder_attention_head_dim=8,
    decoder_num_register_tokens=2,
    decoder_ffn_mult=2,
    clip_length=17,
    token_drop=3,
    tile_sample_min_height=64,
    tile_sample_min_width=64,
    tile_sample_min_overlap_height=16,
    tile_sample_min_overlap_width=16,
)


class TinyVae:
    def __init__(self):
        self.module = MiniMaxH3VideoVAE.from_config(TINY_VAE, disable_weight_init)
        with torch.no_grad():
            for parameter in self.module.parameters():
                if parameter.is_floating_point():
                    parameter.normal_(std=0.02)
        self.compute_dtype = torch.float32
        self.moves = []
        self.offloaded = False

    def move_to(self, device):
        self.moves.append(device)
        self.module.to(device)

    def offload(self):
        self.offloaded = True


def _mod(kind, frames):
    latent = torch.randn(1, 24, frames, 4, 6, generator=torch.Generator().manual_seed(frames))
    return RefMod(kind=kind, name="ref", latent=latent, strength=1.0, source="ref.safetensors")


def test_an_image_reference_latent_decodes_to_one_picture_for_the_text_encoder():
    vae = TinyVae()
    picture = _decode_refmod_media(_mod("image", 1), vae=vae)
    assert isinstance(picture, np.ndarray)
    assert picture.shape == (4 * 2, 6 * 2, 3) and picture.dtype == np.uint8
    assert vae.offloaded


def test_a_two_frame_video_reference_latent_decodes_to_frames():
    frames = _decode_refmod_media(_mod("video", 2), vae=TinyVae())
    assert frames.ndim == 4 and frames.shape[0] >= 1 and frames.shape[1:] == (8, 12, 3)
