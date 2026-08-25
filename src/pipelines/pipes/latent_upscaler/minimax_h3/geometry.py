"""MiniMax-H3 latent-upscale geometry: target pixel/latent size, frame padding,
temporal-chunked forward.

Two independent things this module resolves, both pure functions so the
pipe's own tests can check them without touching a real upsampler checkpoint:

1. :func:`resolve_target_geometry` -- the target pixel/latent extent a
   ``megapixels`` or ``scale`` request resolves to, from a source pixel size.
   Rounding always lands on the 32px grid MiniMax-H3's own canvas uses
   (``generator/video_minimax_h3/geometry.py``'s ``CANVAS_MULTIPLE``), same
   as the encode canvas this pipe resizes a standalone video input onto --
   the upsampler's own resize target is on the SAME grid the model was
   released against, not an arbitrary pixel count.

   ``megapixels`` mode targets a decimal-megapixel area (``megapixels *
   1_000_000`` px, not the ``1024 * 1024`` "MiB-style" megapixel some other
   image-resize fields in this codebase use, e.g. ``generator/qwen``'s
   ``EDIT_AREA_TARGET``) -- deliberately the more common "1 MP = 1,000,000 px"
   reading, since that is the convention that reproduces round target pixel
   counts on this model's own 32px canvas grid at the shipped default
   (2.1MP on a 1344x768 source resolves to 1920x1088, not 1952x1120).

2. :func:`pad_frames_to_h3_grid` -- pads a source video's frame count UP to
   the video VAE's own ``17*n+5`` lattice (``geometry.align_num_frames``,
   imported read-only from the generator package: the grid math is a single
   fact about the shared video VAE, not something this pipe re-derives) by
   repeating the last frame, mirroring ``latent_upscaler/ltx``'s own
   ``_pad_frames_to_temporal_grid`` -- never truncates, so no source content
   is dropped.

3. :func:`upsample_chunked` -- runs a ``MiniMaxH3LatentUpsampler`` over a
   latent's full temporal extent in windows of ``chunk`` latent frames
   instead of one whole-clip forward, so a long clip's activation memory
   stays bounded by the window size rather than the clip length. Windows
   step forward by ``chunk - overlap`` frames; every window but the first
   trims ``overlap`` output frames off its head before concatenating, since
   those frames are the ones the PREVIOUS window already contributed --
   windows tile the sequence exactly once, without a gap or a duplicate,
   as long as ``overlap`` is not itself changed between adjacent windows
   (proof: window ``k``'s untrimmed span starts at ``k*(chunk-overlap)`` and
   window ``k-1``'s trimmed-kept span ends at ``(k-1)*(chunk-overlap) +
   chunk``, which is exactly ``k*(chunk-overlap) + overlap`` -- the point
   window ``k``'s OWN trim resumes from). A single whole-clip call when
   ``T <= chunk`` -- there is nothing to chunk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple

import torch

from src.pipelines.pipes.generator.video_minimax_h3.geometry import align_num_frames

_MEGAPIXEL_AREA = 1_000_000.0


def _round_to_multiple(value: float, multiple: int) -> int:
    return max(multiple, round(value / multiple) * multiple)


@dataclass(frozen=True)
class TargetGeometry:
    height: int
    width: int
    latent_height: int
    latent_width: int
    effective_scale: float


def resolve_target_geometry(
    source_height: int, source_width: int, *, mode: str, megapixels: float = 2.1, scale: float = 2.0,
    canvas_multiple: int = 32, latent_downscale: int = 16,
) -> TargetGeometry:
    """Resolve a ``megapixels`` or ``scale`` upscale request against a
    ``source_height x source_width`` PIXEL size (module docstring).

    Raises when the request would not actually upscale
    (``effective_scale < 1.0``) -- this model has no downscale mode, and a
    silent no-op/shrink would be a confusing way to fail.
    """
    if source_height <= 0 or source_width <= 0:
        raise ValueError(f"source size must be positive, got {source_width}x{source_height}")

    aspect = source_width / source_height
    if mode == "megapixels":
        target_h = (megapixels * _MEGAPIXEL_AREA / aspect) ** 0.5
        target_w = target_h * aspect
    elif mode == "scale":
        target_h = source_height * scale
        target_w = source_width * scale
    else:
        raise ValueError(f"latent_upscaler/minimax_h3: unknown target_mode {mode!r} (expected 'megapixels' or 'scale')")

    height_px = _round_to_multiple(target_h, canvas_multiple)
    width_px = _round_to_multiple(target_w, canvas_multiple)
    effective_scale = ((height_px / source_height) + (width_px / source_width)) / 2.0

    if effective_scale < 1.0:
        raise ValueError(
            f"latent_upscaler/minimax_h3: requested target {width_px}x{height_px} is smaller than the "
            f"source {source_width}x{source_height} (effective_scale={effective_scale:.3f}) -- this model "
            "only upscales; raise 'megapixels' or 'scale'"
        )

    return TargetGeometry(
        height=height_px, width=width_px,
        latent_height=height_px // latent_downscale, latent_width=width_px // latent_downscale,
        effective_scale=effective_scale,
    )


def pad_frames_to_h3_grid(frames: torch.Tensor) -> Tuple[torch.Tensor, int]:
    """Pad ``frames`` (``(n, H, W, 3)``) up to the video VAE's ``17*n+5``
    lattice by repeating its last frame -- never truncates. Returns
    ``(padded_frames, n0)``, ``n0`` being the pre-pad count."""
    n0 = int(frames.shape[0])
    target = align_num_frames(n0)
    if target == n0:
        return frames, n0
    last = frames[-1:].expand(target - n0, *frames.shape[1:])
    return torch.cat([frames, last], dim=0), n0


def upsample_chunked(
    module: Any, latent: torch.Tensor, scale: float, target_hw: Tuple[int, int],
    *, chunk: int = 16, overlap: int = 2,
) -> torch.Tensor:
    """Spatially upsample ``latent`` (``(1, C, T, H, W)``) to ``target_hw`` in
    windows of ``chunk`` latent frames (module docstring). Frame count is
    unchanged -- only ``(H, W)`` moves, to ``target_hw``."""
    if chunk <= overlap:
        raise ValueError(f"chunk ({chunk}) must be greater than overlap ({overlap})")
    target_h, target_w = target_hw
    t = int(latent.shape[2])
    if t <= chunk:
        return module(latent, scale=scale, target_size=(t, target_h, target_w))

    stride = chunk - overlap
    outputs: List[torch.Tensor] = []
    start = 0
    while start < t:
        end = min(start + chunk, t)
        window = latent[:, :, start:end]
        out = module(window, scale=scale, target_size=(end - start, target_h, target_w))
        keep_from = 0 if start == 0 else overlap
        outputs.append(out[:, :, keep_from:])
        start += stride
    return torch.cat(outputs, dim=2)
