"""Temporal batching math for SeedVR2 *video* upscale.

Faithful port of the batching semantics in ByteDance/numz
``ComfyUI-SeedVR2_VideoUpscaler`` (Apache-2.0, ``src/core/generation_phases.py``):
a video is upscaled in temporal batches, each
of which the causal-video VAE requires to be **4n+1 frames** (``1 + 4k`` frames ->
``1 + k`` latent frames). Batches slide with an optional ``temporal_overlap`` and
the overlapped region is cross-faded on reassembly; short runs are extended with
*reversed* frames (the reference's ``pad_video_temporal``) rather than black/edge
padding, and an optional ``prepend`` of reversed head frames reduces clip-start
artifacts (auto-removed from the output).

This module is intentionally dependency-light (numpy only, frames as ``(H,W,3)``
uint8 arrays) so the geometry -- 4n+1 snapping, batch windows, reversed padding,
overlap blend weights -- is unit-testable without torch or a GPU. The generator
pipe owns the encode/DiT/decode of each planned batch; this module owns only the
frame bookkeeping around it.
"""

from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np

Frame = np.ndarray  # (H, W, 3) uint8


# --------------------------------------------------------------------------- #
# 4n+1 geometry
# --------------------------------------------------------------------------- #

def snap_batch_size(batch_size: int) -> int:
    """Snap ``batch_size`` to the nearest valid 4n+1 (``1, 5, 9, 13, ...``).

    The causal-video VAE encodes ``1 + 4k`` frames to ``1 + k`` latent frames, so
    every batch fed to it must satisfy ``T % 4 == 1``. ``1`` (k=0) is the
    degenerate still-image batch.
    """
    bs = max(1, int(batch_size))
    n = round((bs - 1) / 4)
    return max(1, 4 * n + 1)


def pad_to_4n1_count(t: int) -> int:
    """Number of frames to append so ``t`` reaches the next ``1 + 4k`` (0 if already)."""
    t = int(t)
    if t <= 0:
        return 1
    if t % 4 == 1:
        return 0
    return ((t - 1) // 4 + 1) * 4 + 1 - t


# --------------------------------------------------------------------------- #
# Batch windows
# --------------------------------------------------------------------------- #

def plan_batches(
    total_frames: int, batch_size: int, temporal_overlap: int
) -> Tuple[List[Tuple[int, int]], int]:
    """Sliding ``[start, end)`` windows over ``total_frames`` and the effective overlap.

    ``step = batch_size - overlap`` (an ``overlap >= batch_size`` is reset to 0, as
    in the reference). A trailing window that would only re-cover the overlap
    region (``end - start <= overlap``) is dropped -- it carries no new frames.
    Mirrors the reference's ``range(0, total_frames, step)`` loop.
    """
    total = int(total_frames)
    batch_size = max(1, int(batch_size))
    overlap = max(0, int(temporal_overlap))
    if overlap >= batch_size:
        overlap = 0
    step = batch_size - overlap if overlap > 0 else batch_size

    batches: List[Tuple[int, int]] = []
    idx = 0
    while idx < total:
        end = min(idx + batch_size, total)
        if idx > 0 and end - idx <= overlap:
            break
        batches.append((idx, end))
        if end >= total:
            break
        idx += step
    return batches, overlap


# --------------------------------------------------------------------------- #
# Reversed-frame padding (the reference's pad_video_temporal, list form)
# --------------------------------------------------------------------------- #

def pad_reversed(frames: List[Frame], count: int, *, prepend: bool) -> List[Frame]:
    """Extend ``frames`` by ``count`` *reversed* frames at the head or tail.

    Reversed padding (rather than repeat/edge) preserves temporal continuity: the
    added frames mirror the sequence just inside the boundary. When ``count``
    exceeds the available interior frames the reference repeats the boundary frame
    for the remainder -- reproduced here so large pads never raise.
    """
    count = int(count)
    if count <= 0 or not frames:
        return list(frames)
    t = len(frames)

    if count >= t:
        # Overflow: repeat the boundary frame, then the (reversed) interior.
        repeat_count = count - t + 1
        if prepend:
            boundary = [frames[0].copy() for _ in range(repeat_count)]
            interior = list(reversed(frames[1:])) if t > 1 else []
            return boundary + interior + list(frames)
        boundary = [frames[-1].copy() for _ in range(repeat_count)]
        interior = list(reversed(frames[:-1])) if t > 1 else []
        return list(frames) + interior + boundary

    if prepend:
        rev = list(reversed(frames[1:count + 1]))
        return rev + list(frames)
    rev = list(reversed(frames[-count - 1:-1]))
    return list(frames) + rev


def pad_batch(frames: List[Frame], batch_size: int, *, uniform: bool) -> Tuple[List[Frame], int]:
    """Pad a batch's frames to a VAE-legal length, returning ``(padded, true_len)``.

    ``true_len`` is the original count (what to keep after decode). With
    ``uniform`` the batch is padded up to the full ``batch_size`` (the reference's
    ``uniform_batch_size`` -- a short final batch otherwise causes artifacts);
    otherwise only to the next ``1 + 4k``. ``batch_size`` is assumed already
    snapped to 4n+1 (see :func:`snap_batch_size`), so uniform padding is also
    4n+1-legal.
    """
    true_len = len(frames)
    target = batch_size if (uniform and true_len < batch_size) else true_len + pad_to_4n1_count(true_len)
    pad = target - true_len
    if pad <= 0:
        return list(frames), true_len
    return pad_reversed(frames, pad, prepend=False), true_len


# --------------------------------------------------------------------------- #
# Overlap crossfade
# --------------------------------------------------------------------------- #

def overlap_blend_weights(overlap: int) -> np.ndarray:
    """Per-frame weight for the *previous* batch across an ``overlap``-frame join.

    ``overlap >= 3`` uses a raised-cosine (Hann) ramp confined to the middle third
    of the window (matching the reference); smaller overlaps use a plain linear
    ramp ``1 -> 0``. The current batch is weighted ``1 - w``.
    """
    overlap = int(overlap)
    if overlap <= 0:
        return np.zeros((0,), dtype=np.float32)
    if overlap >= 3:
        t = np.linspace(0.0, 1.0, num=overlap, dtype=np.float32)
        blend_start, blend_end = 1.0 / 3.0, 2.0 / 3.0
        u = np.clip((t - blend_start) / (blend_end - blend_start), 0.0, 1.0)
        return 0.5 + 0.5 * np.cos(math.pi * u)
    return np.linspace(1.0, 0.0, num=overlap, dtype=np.float32)


def blend_overlap(prev_tail: List[Frame], cur_head: List[Frame], overlap: int) -> List[Frame]:
    """Cross-fade the ``overlap`` boundary frames of two consecutive batches.

    ``prev_tail`` / ``cur_head`` are the last / first ``overlap`` frames (uint8
    ``(H,W,3)``). Returns ``overlap`` blended uint8 frames.
    """
    w_prev = overlap_blend_weights(overlap)
    blended: List[Frame] = []
    for i in range(overlap):
        wp = float(w_prev[i])
        a = prev_tail[i].astype(np.float32)
        b = cur_head[i].astype(np.float32)
        blended.append(np.clip(a * wp + b * (1.0 - wp), 0.0, 255.0).round().astype(np.uint8))
    return blended


def stitch_batches(
    batch_frames: List[List[Frame]], overlap: int
) -> List[Frame]:
    """Reassemble decoded, padding-trimmed batches into one frame sequence.

    With ``overlap == 0`` this is a plain concatenation. With overlap, the first
    ``overlap`` frames of each non-first batch are cross-faded against the last
    ``overlap`` already-written frames (and consume them), so the shared region
    appears once -- the reference's ``decode_all_batches`` blend.
    """
    if overlap <= 0:
        out: List[Frame] = []
        for fb in batch_frames:
            out.extend(fb)
        return out

    out: List[Frame] = []
    for bi, fb in enumerate(batch_frames):
        if bi == 0 or not out:
            out.extend(fb)
            continue
        k = min(overlap, len(out), len(fb))
        if k > 0:
            prev_tail = out[len(out) - k:]
            blended = blend_overlap(prev_tail, fb[:k], k)
            out[len(out) - k:] = blended
            out.extend(fb[k:])
        else:
            out.extend(fb)
    return out
