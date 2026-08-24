"""Tests for the shared temporal-chunk sizing primitive
(``vae/tiling.py::causal3d_chunk_frames``) -- the decision behind
``chunked_decode_causal3d`` (see test_chunked_decode.py for the decode math
itself), reused by both ``NativeGenerator._causal3d_chunk_frames`` (engine.py)
and the Wan/LTX generator pipes' own ``_decode_video``.
"""

from __future__ import annotations

from types import SimpleNamespace

import torch

from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.causal_3d import AutoEncoderCausal3D
from src.platform.runtime.native.vae.tiling import causal3d_chunk_frames


def _tiny_wan_vae() -> AutoEncoderCausal3D:
    module = AutoEncoderCausal3D.from_config({}, disable_weight_init)
    module.eval()
    return module


def test_none_without_new_feat_cache():
    # A VAE module with no new_feat_cache (e.g. LTX's) never chunks.
    fake_vae = SimpleNamespace()
    latents = torch.zeros(1, 16, 9, 4, 4)
    assert causal3d_chunk_frames(fake_vae, latents, free_vram_gb_value=1.0) is None


def test_none_when_self_normalizing():
    vae = _tiny_wan_vae()  # has new_feat_cache
    latents = torch.zeros(1, 16, 9, 4, 4)
    assert causal3d_chunk_frames(vae, latents, free_vram_gb_value=1.0, is_self_normalizing=True) is None


def test_none_for_single_frame_latent():
    vae = _tiny_wan_vae()
    latents = torch.zeros(1, 16, 1, 4, 4)
    assert causal3d_chunk_frames(vae, latents, free_vram_gb_value=1.0) is None


def test_none_for_4d_still_image_latent():
    vae = _tiny_wan_vae()
    latents = torch.zeros(1, 16, 4, 4)  # 4D -> frames=1 by convention
    assert causal3d_chunk_frames(vae, latents, free_vram_gb_value=1.0) is None


def test_none_when_free_vram_unknown():
    vae = _tiny_wan_vae()
    latents = torch.zeros(1, 16, 9, 4, 4)
    assert causal3d_chunk_frames(vae, latents, free_vram_gb_value=None) is None


def test_none_when_whole_clip_already_fits():
    vae = _tiny_wan_vae()
    latents = torch.zeros(1, 16, 4, 4, 4)  # tiny latent, tiny decode spike
    # A generous VRAM budget should conclude the whole clip fits -> no chunking.
    result = causal3d_chunk_frames(vae, latents, free_vram_gb_value=1000.0)
    assert result is None


def test_none_when_even_one_frame_does_not_fit():
    vae = _tiny_wan_vae()
    latents = torch.zeros(1, 16, 9, 256, 256)  # large spatial extent
    # Starved budget: not even one frame's spike fits -> spatial-tiling territory.
    result = causal3d_chunk_frames(vae, latents, free_vram_gb_value=0.01)
    assert result is None


def test_returns_chunk_smaller_than_total_frames_when_partially_constrained():
    vae = _tiny_wan_vae()
    latents = torch.zeros(1, 16, 32, 64, 64)
    # Pick a budget between "one frame fits" and "whole clip fits" by probing:
    # start large (whole fits -> None) and shrink until we get a concrete chunk.
    chunk = None
    for free_gb in (50.0, 20.0, 10.0, 5.0, 2.0, 1.0, 0.5, 0.3, 0.2):
        result = causal3d_chunk_frames(vae, latents, free_vram_gb_value=free_gb)
        if result is not None:
            chunk = result
            break
    assert chunk is not None
    assert 1 <= chunk < 32


def test_vae_resident_gb_reduces_available_budget():
    vae = _tiny_wan_vae()
    latents = torch.zeros(1, 16, 32, 64, 64)
    # Find a free_gb where chunking engages with vae_resident_gb=0...
    free_gb = None
    baseline_chunk = None
    for candidate in (10.0, 5.0, 3.0, 2.0, 1.0):
        result = causal3d_chunk_frames(vae, latents, free_vram_gb_value=candidate, vae_resident_gb=0.0)
        if result is not None:
            free_gb = candidate
            baseline_chunk = result
            break
    assert free_gb is not None
    # ...then charging a large vae_resident_gb against the SAME budget must
    # never increase the chunk size (less room -> smaller or equal chunk, or
    # tips over into "doesn't even fit" == None).
    charged = causal3d_chunk_frames(vae, latents, free_vram_gb_value=free_gb, vae_resident_gb=free_gb * 0.9)
    assert charged is None or charged <= baseline_chunk


def test_defaults_match_engine_py_constants():
    # engine.py passes its own _CAUSAL3D_DECODE_MB_PER_LATENT_PX /
    # _DECODE_TILE_VRAM_FRACTION explicitly (no drift risk there), but a pipe
    # caller relying on causal3d_chunk_frames' own defaults must get identical
    # sizing behavior to the engine path -- pin the two constant sets equal so
    # any future edit to one without the other fails CI immediately.
    from src.platform.runtime.native import engine as engine_mod
    import inspect

    defaults = inspect.signature(causal3d_chunk_frames).parameters
    assert defaults["decode_mb_per_latent_px"].default == engine_mod._CAUSAL3D_DECODE_MB_PER_LATENT_PX
    assert defaults["vram_fraction"].default == engine_mod._DECODE_TILE_VRAM_FRACTION
