"""Shared VAE-adjacent toolkit for the native-engine pipes.

The VRAM-aware LTX whole-clip/tiled encode ladder (:mod:`ltx_tiled_encode`),
extracted from ``latent_upscaler/ltx`` so ``detailer/video_ltx`` can reuse the
exact same discipline for its per-tube encodes (OOM fix) instead of duplicating
it, and its decode-side twin (:mod:`ltx_tiled_decode`), which bounds the LTX-2.5
diffusion decoder's unchunked stage-5 grid the same way.
"""
