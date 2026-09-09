"""The H3 video-VAE surface the generator pipe's decode path touches.

Shared by the pipe tests that need a bundle with a video VAE but not a real
one: the decode itself, the normalisation buffers, and the tiling/chunking
geometry the decode budget and its profiler marks read off the module
(``_shared/vae/minimax_h3_decode.py``). Subclasses supply whatever
``encode``/``decode`` their own test needs.
"""

from __future__ import annotations

import torch


class StubVideoVae:
    latents_mean = torch.zeros(24)
    latents_std = torch.ones(24)
    use_tiling = False
    tile_sample_min_height = 256
    tile_sample_min_width = 256
    decode_tile_batch_size = 1
    decode_chunk_observer = None

    def decode_tile_grid(self, latent_height: int, latent_width: int) -> tuple[int, int]:
        return 1, 1

    def decode_chunk_count(self, latent_frames: int) -> int:
        return 1
