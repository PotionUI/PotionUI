"""Color correction for SeedVR2 restoration output.

Ported from ByteDance's ComfyUI-SeedVR2_VideoUpscaler ``src/utils/color_fix.py``
(Apache-2.0), trimmed to the two modes we expose — ``wavelet`` (frequency-based
transfer that keeps the restored high-frequency detail while adopting the input's
low-frequency color) and ``adain`` (channel mean/variance match) — dropping the
lab/hsv variants and the half-precision ``safe_*`` wrappers (replaced with plain
float32 ``F.pad(mode="replicate")`` / ``F.interpolate``).

A one-step restoration DiT can drift the global color/brightness of its output;
matching it back to the (area-resized) low-res input removes that drift without
touching the recovered detail. The public :func:`color_correct` takes and returns
uint8 ``HWC`` arrays so the generator pipe can hand it the decoded output and the
resized input directly; ``source`` is bilinearly resized to ``target`` when their
resolutions differ (they match in the normal image path).
"""

from __future__ import annotations

import time
from typing import List, Optional

import numpy as np
import torch
import torch.nn.functional as F

from src.platform.observability.profiling import get_profiler, profiling_enabled

Tensor = torch.Tensor


def _to_tensor(arr: np.ndarray) -> Tensor:
    """uint8 ``HWC`` -> float32 ``(1, C, H, W)`` in ``[0, 1]``."""
    t = torch.from_numpy(np.ascontiguousarray(arr)).float() / 255.0
    return t.permute(2, 0, 1).unsqueeze(0)


def _to_uint8(t: Tensor) -> np.ndarray:
    """float32 ``(1, C, H, W)`` in ``[0, 1]`` -> uint8 ``HWC``."""
    t = t.squeeze(0).clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8)
    return t.permute(1, 2, 0).contiguous().cpu().numpy()


def _stack_to_tensor(arrs: List[np.ndarray], device: torch.device) -> Tensor:
    """List of uint8 ``HWC`` (identical H,W) -> float32 ``(B, C, H, W)`` in ``[0, 1]``."""
    stacked = np.stack([np.ascontiguousarray(a) for a in arrs], axis=0)   # (B,H,W,C)
    t = torch.from_numpy(stacked).to(device=device, dtype=torch.float32) / 255.0
    return t.permute(0, 3, 1, 2).contiguous()                             # (B,C,H,W)


def _unstack_to_uint8(t: Tensor) -> List[np.ndarray]:
    """float32 ``(B, C, H, W)`` in ``[0, 1]`` -> list of uint8 ``HWC``."""
    t = t.clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8)
    t = t.permute(0, 2, 3, 1).contiguous().cpu().numpy()                  # (B,H,W,C)
    return [t[i] for i in range(t.shape[0])]


def _calc_mean_std(feat: Tensor, eps: float = 1e-5) -> tuple[Tensor, Tensor]:
    """Channel-wise mean/std of a ``(B, C, H, W)`` tensor -> ``(B, C, 1, 1)``."""
    b, c = feat.shape[:2]
    var = feat.reshape(b, c, -1).var(dim=2) + eps
    std = var.sqrt().reshape(b, c, 1, 1)
    mean = feat.reshape(b, c, -1).mean(dim=2).reshape(b, c, 1, 1)
    return mean, std


def adaptive_instance_normalization(content: Tensor, style: Tensor) -> Tensor:
    """Transfer ``style``'s channel mean/variance onto ``content`` (AdaIN)."""
    style_mean, style_std = _calc_mean_std(style)
    content_mean, content_std = _calc_mean_std(content)
    normalized = (content - content_mean) / content_std
    return normalized * style_std + style_mean


def _wavelet_blur(image: Tensor, radius: int) -> Tensor:
    """Gaussian-approximation blur via a dilated 3x3 grouped conv (per channel)."""
    max_safe_radius = max(1, min(image.shape[-2:]) // 8)
    radius = min(radius, max_safe_radius)
    num_channels = image.shape[1]
    kernel_vals = [
        [0.0625, 0.125, 0.0625],
        [0.125, 0.25, 0.125],
        [0.0625, 0.125, 0.0625],
    ]
    kernel = torch.tensor(kernel_vals, dtype=image.dtype, device=image.device)
    kernel = kernel[None, None].repeat(num_channels, 1, 1, 1)
    image = F.pad(image, (radius, radius, radius, radius), mode="replicate")
    return F.conv2d(image, kernel, groups=num_channels, dilation=radius)


def _wavelet_decomposition(image: Tensor, levels: int = 5) -> tuple[Tensor, Tensor]:
    """Split into (high-freq detail, low-freq color) via an iterative pyramid."""
    high_freq = torch.zeros_like(image)
    low_freq = image
    for i in range(levels):
        radius = 2 ** i
        low_freq = _wavelet_blur(image, radius)
        high_freq = high_freq + (image - low_freq)
        image = low_freq
    return high_freq, low_freq


def wavelet_reconstruction(content: Tensor, style: Tensor) -> Tensor:
    """Keep ``content``'s detail (high freq), adopt ``style``'s color (low freq)."""
    content_high, _ = _wavelet_decomposition(content)
    _, style_low = _wavelet_decomposition(style)
    return content_high + style_low


def color_correct(target: np.ndarray, source: np.ndarray, mode: str = "wavelet") -> np.ndarray:
    """Match ``target`` (restored output) colors to ``source`` (resized input).

    ``target`` / ``source`` are uint8 ``HWC`` arrays; ``mode`` is ``wavelet`` |
    ``adain`` | ``none``. Returns a uint8 ``HWC`` array. ``source`` is bilinearly
    resized to ``target``'s resolution when they differ.
    """
    if mode == "none":
        return target
    with torch.no_grad():
        tgt = _to_tensor(target)
        src = _to_tensor(source)
        if src.shape[-2:] != tgt.shape[-2:]:
            src = F.interpolate(src, size=tgt.shape[-2:], mode="bilinear", align_corners=False)
        if mode == "adain":
            out = adaptive_instance_normalization(tgt, src)
        elif mode == "wavelet":
            out = wavelet_reconstruction(tgt, src)
        else:
            raise ValueError(f"unknown color correction mode: {mode!r}")
        return _to_uint8(out)


def _resolve_device(device: "Optional[str | torch.device]") -> torch.device:
    """Resolve the requested color-fix device, downgrading a CUDA request to CPU
    when CUDA isn't available (so an off-GPU caller — tests, CPU runs — never
    trips over a device that isn't there)."""
    if device is None:
        return torch.device("cpu")
    dev = torch.device(device)
    if dev.type == "cuda" and not torch.cuda.is_available():
        return torch.device("cpu")
    return dev


def _sync_if_profiling(device: torch.device) -> None:
    """Block on the CUDA stream so a wall-clock delta below measures actual
    kernel execution, not just async-launch time — only when profiling is on
    (the sync itself has a cost we don't want on the normal path). Mirrors
    ``generator/seedvr2/main.py``'s ``_sync_if_profiling``; duplicated rather
    than imported to avoid a circular import (``main.py`` imports this module)."""
    if profiling_enabled() and device.type == "cuda":
        try:
            torch.cuda.synchronize(device)
        except Exception:  # noqa: BLE001 — timing accuracy is never worth a crash
            pass


def _correct_chunk(
    targets: List[np.ndarray], sources: List[np.ndarray], mode: str, device: torch.device,
    timing: Optional[dict] = None,
) -> List[np.ndarray]:
    """Color-correct a chunk of frames in ONE batched pass on ``device``.

    ``wavelet_reconstruction``/``adaptive_instance_normalization`` treat every
    ``(B, C, H, W)`` batch element independently (grouped per-channel convs and
    per-``(B, C)`` mean/std), so a batched pass is bit-identical to correcting
    each frame alone on the SAME device — the batching is purely a throughput
    win, not a numerical change.

    When ``timing`` is given (profiling on — see :func:`color_correct_batch`),
    accumulates ``stage_seconds`` (host stack + H2D transfer + resize),
    ``compute_seconds`` (the wavelet/adain math) and ``unstage_seconds`` (D2H
    transfer + uint8 conversion) into it, each bounded by a profiling-gated
    ``_sync_if_profiling`` so the split reflects real kernel time rather than
    async-launch time. No-op (no extra syncs, no timing) when ``timing`` is
    ``None`` — the normal, unprofiled path pays nothing for this."""
    t0 = time.perf_counter() if timing is not None else 0.0
    tgt = _stack_to_tensor(targets, device)
    src = _stack_to_tensor(sources, device)
    if src.shape[-2:] != tgt.shape[-2:]:
        src = F.interpolate(src, size=tgt.shape[-2:], mode="bilinear", align_corners=False)
    if timing is not None:
        _sync_if_profiling(device)
        t1 = time.perf_counter()
        timing["stage_seconds"] += t1 - t0
    if mode == "adain":
        out = adaptive_instance_normalization(tgt, src)
    elif mode == "wavelet":
        out = wavelet_reconstruction(tgt, src)
    else:
        raise ValueError(f"unknown color correction mode: {mode!r}")
    if timing is not None:
        _sync_if_profiling(device)
        t2 = time.perf_counter()
        timing["compute_seconds"] += t2 - t1
    result = _unstack_to_uint8(out)  # already syncs (``.cpu()``)
    if timing is not None:
        timing["unstage_seconds"] += time.perf_counter() - t2
    return result


def color_correct_batch(
    targets: List[np.ndarray],
    sources: List[np.ndarray],
    mode: str = "wavelet",
    *,
    device: "Optional[str | torch.device]" = None,
    max_chunk: int = 8,
) -> List[np.ndarray]:
    """Batched :func:`color_correct` for a whole video clip.

    ``targets``/``sources`` are equal-length lists of uint8 ``HWC`` arrays (each
    ``targets[i]`` matched against ``sources[i]``). Runs the correction on
    ``device`` (a CUDA device for the video path — the per-frame CPU wavelet is
    the dominant cost of SeedVR2 video's post-decode tail) in
    chunks of at most ``max_chunk`` frames so peak memory stays bounded on a
    full-HD clip. The result is byte-identical to calling :func:`color_correct`
    per frame on the same device (see :func:`_correct_chunk`).

    A CUDA OOM never escapes: the chunk is halved and retried, and a single
    frame that still won't fit falls back to CPU — so this can be called inside
    the video path's temporal-batch OOM ladder without a color-fix OOM being
    mistaken for a batch-size OOM.

    When profiling is on (see ``profiling_enabled``), emits one
    ``seedvr2.color_fix.breakdown`` profiler event with the aggregate
    ``stage_seconds`` (host stack + H2D + resize) / ``compute_seconds`` (the
    wavelet/adain math) / ``unstage_seconds`` (D2H + uint8 conversion) split
    across every chunk, plus ``cpu_fallback_frames`` — so a slow run can be
    attributed to real on-device compute vs. host-side overhead vs. a silent
    per-frame CPU degrade, none of which the plain ``seedvr2.color_fix``
    duration alone (``device`` there is the REQUESTED device, not necessarily
    what every chunk actually ran on) can tell apart."""
    if mode == "none" or not targets:
        return list(targets)
    if len(sources) != len(targets):
        raise ValueError(
            f"color_correct_batch: {len(targets)} targets vs {len(sources)} sources"
        )

    dev = _resolve_device(device)
    out: List[np.ndarray] = []
    i = 0
    chunk = max(1, int(max_chunk))
    n = len(targets)
    profiling = profiling_enabled()
    timing = {"stage_seconds": 0.0, "compute_seconds": 0.0, "unstage_seconds": 0.0} if profiling else None
    cpu_fallback_frames = 0
    while i < n:
        j = min(n, i + chunk)
        try:
            out.extend(_correct_chunk(targets[i:j], sources[i:j], mode, dev, timing))
            i = j
        except torch.cuda.OutOfMemoryError:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if chunk > 1:
                chunk = max(1, chunk // 2)
                continue
            # Single frame still won't fit on-device — correct it on CPU and move on.
            out.extend(_correct_chunk(targets[i:i + 1], sources[i:i + 1], mode, torch.device("cpu"), timing))
            cpu_fallback_frames += 1
            i += 1
    if timing is not None:
        get_profiler().mark(
            "seedvr2.color_fix.breakdown", frames=n, mode=mode, device=str(dev),
            cpu_fallback_frames=cpu_fallback_frames,
            **{k: round(v, 4) for k, v in timing.items()},
        )
    return out
