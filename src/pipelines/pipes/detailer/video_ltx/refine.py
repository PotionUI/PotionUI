"""Tube refine for the LTX video detailer: re-run the SAME LTX model over a
cropped spatiotemporal tube at a light denoise strength.

Reuses the two-stage refine machinery the upscale path already uses:

  cropped tube pixels (1,3,T,H,W in [-1,1])
    -> pad T up to the causal VAE's 1+8k lattice (repeat last frame)
    -> vae.encode  (normalized latent, via the shared VRAM-aware
       whole-clip/tiled encode ladder)
    -> denoise() seeded FROM that latent with a short low-noise sigma tail
       (the img2img init mix: sigma0*noise + (1-sigma0)*latent)
    -> vae.decode
    -> trim the padding frames back off

The LTX VAE encoder has NO internal chunking (see ``vae/ltx_tiling.py``), and a
tube at working resolution spanning a long track is a multi-GB-to-tens-of-GB
encode, so ``refine_tube_pixels`` routes its encode through
``_shared/vae/ltx_tiled_encode.py``'s ``encode_with_oom_retry`` -- the same
estimate-then-choose-then-retry ladder ``latent_upscaler/ltx`` uses.

A small tube (a few thousand DiT tokens) gets a near-floor activation reserve
from :func:`place_dit_for_sequence`'s token-only formula, so the DiT goes FULLY
resident -- correct for the denoise, but leaving almost nothing free for the
tube's OWN VAE decode, whose cost scales with PIXEL dimensions, not token count.
:func:`estimate_decode_reserve_gb` derives that decode's VRAM need from the
tube's known decoded pixel dimensions and passes it as
``place_dit_for_sequence``'s ``reserve_gb``, forcing PARTIAL DiT residency so
the decode gets headroom. As a belt, the decode itself runs through the shared
``_shared/vae/ltx_tiled_decode.py``'s ``decode_with_oom_retry`` ladder, which
retries once (evicting every other GPU-resident component) on a real OOM, and
falls back to a tiled decode for the LTX-2.5 diffusion VAE.

The denoise-driving idiom (model_forward closure + ``denoise(..., sigmas=)``)
is copied minimally from ``generator/txt2vid_ltx``'s ``generate_one``. The
refine runs at CFG 1.0 with no negative pass (Lightricks' SimpleDenoiser refine
convention): a face/hand tube refine should tidy texture, never re-imagine the
subject.

Strength -> starting sigma (the fraction of noise re-injected):
  light 0.40 / balanced 0.55 / strong 0.70
each scaled onto Lightricks' STAGE_2 sigma SHAPE (its descending tail, from
``preset.vars.ltx23_stage2_sigma_recipe``) so the schedule keeps the recipe's
curve, just started lower than the 0.909 upscale-refine start. A higher start
has more noise to walk back down, so the tail LENGTHENS with strength (~4 nodes
at 0.40, ~6 at 0.70), resampled along the STAGE_2 curve so the extra steps sit
on its shape. These starts are eye-tuning constants, not measured values.
"""

from __future__ import annotations

from typing import Any, List

import numpy as np
import torch

from src.platform.runtime.native.sampling import denoise
from src.platform.runtime.native.vae.ltx_causal_video import _MAX_CHUNK_BYTES
from src.pipelines.pipes._shared.generation.dit_placement import place_dit_for_sequence
from src.pipelines.pipes._shared.vae.ltx_tiled_decode import DECODE_NOISE_SEED_OFFSET, decode_with_oom_retry
from src.pipelines.pipes._shared.vae.ltx_tiled_encode import encode_with_oom_retry
# Constants + the CPU/GPU cond-move helper, imported (not copied) -- these are
# stable module-level values/functions, not the generator pipe's own logic.
from src.pipelines.pipes.generator.txt2vid_ltx.main import _SPATIAL_DOWNSCALE, _TEMPORAL_DOWNSCALE
from src.pipelines.pipes.generator.txt2vid_wan22.main import _to_device

# Lightricks' STAGE_2_DISTILLED_SIGMA_VALUES tail (preset.vars.
# ltx23_stage2_sigma_recipe) -- the descending SHAPE reused for the tube refine.
_STAGE2_SHAPE: List[float] = [0.909375, 0.725, 0.421875, 0.0]

# -- Strength dial --
#
# Strength -> starting sigma (fraction of noise re-injected). Eye-tuning
# candidates, not measured constants: nudge them, do not treat as measured.
STRENGTH_SIGMA_START = {"light": 0.40, "balanced": 0.55, "strong": 0.70}

# Tail length grows with the start: one extra denoise node per this much added
# starting sigma above the light 0.40 floor (so 0.40->~4 nodes, 0.55->~5,
# 0.70->~6).
_TAIL_NODE_START_FLOOR = 0.40
_TAIL_NODE_SIGMA_PER_NODE = 0.15


def _tail_node_count(start: float) -> int:
    """How many sigma nodes the refine schedule gets for a given ``start``.

    A higher start has more noise to walk back down, so it earns a longer tail
    (more, finer denoise steps) rather than the same 4-node polish schedule at a
    steeper first hop. Linear in ``start`` above the light floor, clamped so the
    schedule always has at least one step (two nodes)."""
    base = len(_STAGE2_SHAPE)  # the light-floor node count (Lightricks' own tail)
    extra = (float(start) - _TAIL_NODE_START_FLOOR) / _TAIL_NODE_SIGMA_PER_NODE
    return max(2, int(round(base + extra)))

# -- tube VAE-decode VRAM reserve --------------------
#
# The LTX decoder self-chunks its OUTPUT sample to at most _MAX_CHUNK_BYTES
# (128MB, ltx_causal_video.py) per recursive step, but even ONE such chunk needs
# more than a bare 128MB in practice (~136MB observed): the up_block that
# produces it runs its own conv/norm/upsample activations on top of the
# chunk-sized output tensor, and (for a small tube whose TOTAL decoded size never
# crosses the chunking threshold) the FIRST up_block(s) process the WHOLE
# incoming latent unchunked, at a HIGHER channel count than the final 3-channel
# RGB output. This is a calibrated safety margin, not a measured constant:
#
#   floor    = _DECODE_CHUNK_SAFETY_MULTIPLE x _MAX_CHUNK_BYTES
#              (a small multiple of the decoder's OWN chunk-boundary constant --
#              safely above the observed ~136MB single-allocation failure --
#              covers tiny tubes where the assembled-output term is negligible)
#   scaling  = T_pixel * H_pixel * W_pixel * channels * bytes/element x
#              _DECODE_ASSEMBLY_SAFETY_MULTIPLE
#              (the assembled output buffer's raw byte size, inflated for
#              torch.cat's transient doubling -- the chunk list and the
#              concatenated tensor are briefly both alive -- plus the
#              un-normalize copy and other decode-path transients)
#
# reserve = max(floor, scaling).
_DECODE_CHUNK_SAFETY_MULTIPLE = 8  # floor: exactly 1.0 GiB
_DECODE_OUTPUT_CHANNELS = 3
_DECODE_OUTPUT_BYTES_PER_ELEMENT = 2  # fp16/bf16 VAE compute dtype
_DECODE_ASSEMBLY_SAFETY_MULTIPLE = 6


def estimate_decode_reserve_gb(t_pixel: int, h_pixel: int, w_pixel: int) -> float:
    """VRAM reserve for ONE tube's VAE decode -- see the module-level
    derivation above. ``t_pixel``/``h_pixel``/``w_pixel`` are the tube's
    DECODED pixel dimensions -- exactly known from the tube's own (already
    temporal-grid-padded) encode-time pixel shape, since VAE encode/decode
    round-trips T/H/W exactly."""
    output_bytes = (
        int(t_pixel) * int(h_pixel) * int(w_pixel)
        * _DECODE_OUTPUT_CHANNELS * _DECODE_OUTPUT_BYTES_PER_ELEMENT
        * _DECODE_ASSEMBLY_SAFETY_MULTIPLE
    )
    floor_bytes = _MAX_CHUNK_BYTES * _DECODE_CHUNK_SAFETY_MULTIPLE
    return max(output_bytes, floor_bytes) / (1024 ** 3)


def strength_to_refine_sigmas(strength: str) -> torch.Tensor:
    """Descending refine sigma schedule for a strength label.

    Resamples Lightricks' STAGE_2 descending curve to ``_tail_node_count(start)``
    nodes -- a longer start earns more nodes (a longer, finer denoise tail) -- then
    rescales it so the first node equals the strength's starting sigma (0.40/0.55/
    0.70) and the last stays 0.0. The extra nodes sit ON Lightricks' proven shape
    (linear interp between its points), never on an invented one. Unknown labels
    fall back to ``balanced``."""
    start = STRENGTH_SIGMA_START.get(str(strength).lower(), STRENGTH_SIGMA_START["balanced"])
    n_nodes = _tail_node_count(start)

    base = np.asarray(_STAGE2_SHAPE, dtype=np.float64)
    src_pos = np.linspace(0.0, 1.0, base.size)
    dst_pos = np.linspace(0.0, 1.0, n_nodes)
    curve = np.interp(dst_pos, src_pos, base)  # descending; curve[0]=0.909375, curve[-1]=0.0

    scale = start / curve[0]
    values = [round(float(v * scale), 6) for v in curve]
    values[0] = start  # exact, no rounding drift on the load-bearing start value
    values[-1] = 0.0   # the tail always lands on clean latent
    return torch.tensor(values, dtype=torch.float32)


def pad_pixels_to_temporal_grid(pixels: torch.Tensor, temporal_downscale: int = _TEMPORAL_DOWNSCALE) -> tuple[torch.Tensor, int]:
    """Pad ``(1, 3, T, H, W)`` up to the next valid ``1 + k*td`` frame count by
    repeating the last frame (never truncates). Returns ``(padded, original_T)``
    so the caller can trim the decoded result back. Mirrors
    ``latent_upscaler/ltx``'s ``_pad_frames_to_temporal_grid`` for the ``(n,H,W,3)``
    layout, adapted to this pipe's ``(1,3,T,H,W)`` tube tensor."""
    t0 = int(pixels.shape[2])
    td = max(1, int(temporal_downscale))
    pad = (td - (t0 - 1) % td) % td
    if pad == 0:
        return pixels, t0
    last = pixels[:, :, -1:].expand(-1, -1, pad, -1, -1)
    return torch.cat([pixels, last], dim=2), t0


def refine_tube_pixels(
    bundle: Any,
    cond_model: Any,
    pixels: torch.Tensor,
    *,
    strength: str,
    device: str,
    fps: float,
    seed: int = 0,
) -> np.ndarray:
    """Refine one cropped tube. ``pixels`` is ``(1, 3, T, H, W)`` in ``[-1, 1]``
    at working resolution (H/W already 32-multiples). Returns ``(T, H, W, 3)``
    uint8 -- exactly ``T`` frames (padding used for the VAE encode is trimmed).

    Runs on ``device`` at CFG 1.0 (no negative pass). Assumes the caller keeps
    ``bundle.vae``/``bundle.dit`` resident across the track loop and offloads
    them once at the end (tubes are processed sequentially, so re-placing the
    DiT per tube via :func:`place_dit_for_sequence` is a near-no-op once it is
    already resident and correctly sizes the activation reserve to THIS tube).

    The encode below goes through the shared ``encode_with_oom_retry`` ladder
    rather than a plain ``vae.module.encode`` -- a tube can be a
    multi-GB-to-tens-of-GB encode just like a whole clip. The decode at the end
    similarly reserves headroom via ``place_dit_for_sequence``'s ``reserve_gb``
    and goes through the shared ``decode_with_oom_retry`` ladder, seeded off
    ``seed`` -- see the module docstring."""
    refine_sigmas = strength_to_refine_sigmas(strength)
    dtype = bundle.dit.compute_dtype

    padded, orig_t = pad_pixels_to_temporal_grid(pixels)
    padded = padded.to(device=device, dtype=bundle.vae.compute_dtype)

    bundle.vae.move_to(device)
    latent = encode_with_oom_retry(
        bundle.vae, padded, device,
        profiler_mark="detailer.tube_encode", log_prefix="detailer/video_ltx",
    )  # normalized latent
    del padded

    b, c, t_lat, h_lat, w_lat = latent.shape
    # The tube's decode dimensions are exactly the padded encode-time pixel
    # dimensions (VAE encode/decode round-trips T/H/W exactly) -- derived from
    # the latent shape rather than threading
    # the now-freed `padded` tensor's shape through, so this holds even if a
    # future caller skips the encode step (e.g. a latent-only entry point).
    decode_reserve_gb = estimate_decode_reserve_gb(
        (t_lat - 1) * _TEMPORAL_DOWNSCALE + 1, h_lat * _SPATIAL_DOWNSCALE, w_lat * _SPATIAL_DOWNSCALE,
    )
    place_dit_for_sequence(
        bundle.dit, device, video_tokens=t_lat * h_lat * w_lat,
        own_models=(bundle.dit, bundle.vae), reserve_gb=decode_reserve_gb,
    )
    dit_module = bundle.dit.module

    def model_forward(x: torch.Tensor, sigma: torch.Tensor, conditioning: dict) -> torch.Tensor:
        # Copied minimal idiom from generator/txt2vid_ltx.generate_one: x wrapped
        # as a 1-element list = video-only forward; no NAG/STG/step-cache here
        # (a plain light refine).
        return dit_module([x], sigma, conditioning["context"], attention_mask=None, frame_rate=fps)

    cond = _to_device(cond_model.embeds, device, dtype)
    latent = latent.to(dtype=dtype)
    gen = torch.Generator(device=device).manual_seed(int(seed))
    noise = torch.randn(latent.shape, generator=gen, device=device, dtype=dtype)

    refined = denoise(
        model_forward, latent, cond, None,
        steps=max(1, refine_sigmas.numel() - 1), sampler_name="euler",
        sampling_settings={**bundle.spec.sampling_settings}, guidance_scale=1.0,
        seed_noise=noise, sigmas=refine_sigmas, cfg_zero_star=False,
    )

    decode_gen = torch.Generator(device=device).manual_seed(int(seed) + DECODE_NOISE_SEED_OFFSET)
    out = decode_with_oom_retry(
        bundle.vae, refined.to(dtype=bundle.vae.compute_dtype), device,
        generator=decode_gen, profiler_mark="detailer.tube_decode", log_prefix="detailer/video_ltx",
    )  # (1,3,T,H,W) [-1,1]
    out = out[0, :, :orig_t].clamp(-1.0, 1.0).float()
    out = ((out + 1.0) * 127.5).round().to(torch.uint8)  # (3,T,H,W)
    return out.permute(1, 2, 3, 0).contiguous().cpu().numpy()  # (T,H,W,3)
