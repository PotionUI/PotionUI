from __future__ import annotations

import functools
import logging
import weakref
from itertools import chain
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import torch
from PIL import Image
from torch.utils._python_dispatch import TorchDispatchMode
from torch.utils._pytree import tree_flatten

from src.platform.runtime.model_lifecycle.lifecycle import get_model_lifecycle
from vendor.taehv.taehv import TAEHV, apply_model_with_memblocks

logger = logging.getLogger(__name__)

PREVIEW_DECODER_SLOT = "native/preview_decoder"
WINDOW_CONTEXT_TOKENS = 2
WINDOW_LOOKAHEAD_TOKENS = 1
MAX_SPACE_UPSCALES = 3

ARCH_BY_LATENT_CHANNELS = {
    128: "taeltx",
    48: "taew2_2",
    32: "taehv1_5",
    24: "taeh3",
    16: "taew2_1",
}


def arch_name_from_state_dict(sd: Mapping[str, torch.Tensor]) -> str:
    is_super = "decoder.0.weight" in sd
    first = sd["decoder.0.weight" if is_super else "decoder.1.weight"]
    channels = int(first.shape[1])
    arch = ARCH_BY_LATENT_CHANNELS.get(channels)
    if arch is None:
        raise ValueError(f"not a TAEHV decoder: no known architecture for {channels} latent channels")
    return f"{arch}_super" if is_super else arch


def build_preview_decoder(sd: Mapping[str, torch.Tensor]) -> TAEHV:
    decoder = TAEHV(arch_name_from_state_dict(sd))
    decoder.load_weights(sd)
    decoder.eval().requires_grad_(False)
    return decoder


def _read_state_dict(path: Path) -> dict[str, torch.Tensor]:
    if path.suffix.lower() in (".safetensors", ".sft"):
        from safetensors.torch import load_file

        return load_file(str(path))
    return torch.load(str(path), map_location="cpu", weights_only=True)


def load_preview_decoder(path: str | Path) -> TAEHV:
    return build_preview_decoder(_read_state_dict(Path(path)))


def _fingerprint(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return f"{path}|missing"
    return f"{path}|{stat.st_size}|{stat.st_mtime_ns}"


def acquire_preview_decoder(path: str | Path) -> TAEHV:
    resolved = Path(path)
    lifecycle = get_model_lifecycle()
    if lifecycle is None:
        return load_preview_decoder(resolved)
    return lifecycle.acquire(
        f"{PREVIEW_DECODER_SLOT}/{resolved}",
        _fingerprint(resolved),
        lambda: load_preview_decoder(resolved),
        estimated_vram_gb=0.05,
    )


def compute_dtype(device: torch.device) -> torch.dtype:
    return torch.float16 if device.type == "cuda" else torch.float32


def place(decoder: TAEHV, device: torch.device) -> TAEHV:
    dtype = compute_dtype(device)
    current = next(decoder.parameters())
    if current.device != device or current.dtype != dtype:
        decoder.to(device=device, dtype=dtype)
    return decoder


def set_space_upscales(decoder: TAEHV, count: int) -> None:
    upsamples = [m for m in decoder.decoder if isinstance(m, torch.nn.Upsample)]
    for index, module in enumerate(upsamples):
        module.scale_factor = 2.0 if index < count else 1.0


def pick_space_upscales(decoder: TAEHV, latent_height: int, latent_width: int, max_size: int) -> int:
    if not any(isinstance(m, torch.nn.Upsample) for m in decoder.decoder):
        return MAX_SPACE_UPSCALES
    base = max(latent_height, latent_width) * decoder.patch_size
    for count in range(MAX_SPACE_UPSCALES + 1):
        if base * (2 ** count) >= max_size:
            return count
    return MAX_SPACE_UPSCALES


def latent_to_ntchw(x0: torch.Tensor, latent_channels: int) -> torch.Tensor:
    t = x0.detach()
    if t.ndim == 5:
        t = t[0].permute(1, 0, 2, 3)
    elif t.ndim == 4:
        t = t[0].unsqueeze(0)
    elif t.ndim == 3:
        t = t.unsqueeze(0)
    else:
        raise ValueError(f"preview decoder expects a 3/4/5D latent, got {t.ndim}D")
    if t.shape[1] != latent_channels:
        raise ValueError(f"latent has {t.shape[1]} channels, decoder expects {latent_channels}")
    return t.unsqueeze(0)


def _clip_on_device(
    decoder: TAEHV, latent: torch.Tensor, parallel: bool, space_upscales: int | None,
) -> torch.Tensor:
    set_space_upscales(decoder, MAX_SPACE_UPSCALES if space_upscales is None else space_upscales)
    with torch.inference_mode():
        frames = decoder.decode_video(latent.to(next(decoder.parameters()).dtype), parallel=parallel)
    return frames[0].float()


def decode_clip(
    decoder: TAEHV,
    x0: torch.Tensor,
    *,
    parallel: bool = True,
    space_upscales: int | None = None,
) -> torch.Tensor:
    latent = latent_to_ntchw(x0, decoder.latent_channels)
    place(decoder, latent.device)
    return _clip_on_device(decoder, latent, parallel, space_upscales).cpu()


def _middle_frame_on_device(decoder: TAEHV, latent: torch.Tensor, max_size: int) -> torch.Tensor:
    tokens = latent.shape[1]
    center = tokens // 2
    start = max(0, center - WINDOW_CONTEXT_TOKENS)
    end = min(tokens, center + WINDOW_LOOKAHEAD_TOKENS + 1)
    set_space_upscales(decoder, pick_space_upscales(decoder, latent.shape[-2], latent.shape[-1], max_size))
    window = latent[:, start:end].to(next(decoder.parameters()).dtype)
    with torch.inference_mode():
        frames = apply_model_with_memblocks(decoder.decoder, window, True)
        frames = decoder.postprocess_output_frames(frames)
    trim = decoder.frames_to_trim if start == 0 else 0
    frames = frames[:, trim:]
    index = (center - start) * decoder.t_upscale + decoder.t_upscale // 2 - trim
    index = min(max(index, 0), frames.shape[1] - 1)
    return frames[0, index].float()


def decode_middle_frame(
    decoder: TAEHV,
    x0: torch.Tensor,
    *,
    max_size: int,
) -> torch.Tensor:
    latent = latent_to_ntchw(x0, decoder.latent_channels)
    place(decoder, latent.device)
    return _middle_frame_on_device(decoder, latent, max_size).cpu()


def frame_to_image(frame: torch.Tensor, *, max_size: int) -> Image.Image:
    array = frame.clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8).permute(1, 2, 0).numpy()
    image = Image.fromarray(array, mode="RGB")
    long_edge = max(image.width, image.height)
    if long_edge > max_size:
        scale = max_size / long_edge
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.BILINEAR)
    return image


def spec_latent_channels(spec: Any) -> int | None:
    channels = (getattr(spec, "latent_format", None) or {}).get("latent_channels")
    return int(channels) if channels else None


PARALLEL_DECODE_MAX_TOKENS = 8


def even_frame_indices(total: int, count: int) -> list[int]:
    if total <= 0:
        return []
    if total <= count:
        return list(range(total))
    return [round(i * (total - 1) / (count - 1)) for i in range(count)]


def _clip_plan(decoder: TAEHV, latent: torch.Tensor, max_size: int) -> tuple[bool, int | None]:
    upscales = (
        pick_space_upscales(decoder, latent.shape[-2], latent.shape[-1], max_size)
        if any(isinstance(m, torch.nn.Upsample) for m in decoder.decoder)
        else None
    )
    return latent.shape[1] <= PARALLEL_DECODE_MAX_TOKENS, upscales


def decode_preview_clip(
    decoder: TAEHV,
    x0: torch.Tensor,
    *,
    max_frames: int = 25,
    max_size: int = 384,
) -> tuple[list[np.ndarray], list[int]]:
    parallel, upscales = _clip_plan(decoder, latent_to_ntchw(x0, decoder.latent_channels), max_size)
    clip = decode_clip(decoder, x0, parallel=parallel, space_upscales=upscales)
    indices = even_frame_indices(clip.shape[0], max_frames)
    frames = [np.asarray(frame_to_image(clip[i], max_size=max_size)) for i in indices]
    return frames, indices


_BYTES_PER_GB = 1024 ** 3


def decoder_resident_gb(decoder: torch.nn.Module) -> float:
    elements = sum(t.numel() for t in chain(decoder.parameters(), decoder.buffers()))
    return elements * torch.finfo(compute_dtype(torch.device("cuda"))).bits / 8 / _BYTES_PER_GB


class _PeakLiveBytes(TorchDispatchMode):
    def __init__(self, external: Iterable[torch.Tensor]) -> None:
        super().__init__()
        self._external = {t.untyped_storage()._cdata for t in external}
        self._storages: dict[int, list[int]] = {}
        self.live = 0
        self.peak = 0

    def _release(self, key: int) -> None:
        entry = self._storages[key]
        entry[0] -= 1
        if entry[0] == 0:
            self.live -= entry[1]
            del self._storages[key]

    def _track(self, tensor: torch.Tensor) -> None:
        storage = tensor.untyped_storage()
        key = storage._cdata
        if key in self._external:
            return
        entry = self._storages.get(key)
        if entry is None:
            entry = self._storages[key] = [0, storage.nbytes()]
            self.live += entry[1]
            self.peak = max(self.peak, self.live)
        entry[0] += 1
        weakref.finalize(tensor, self._release, key)

    def __torch_dispatch__(self, func, types, args=(), kwargs=None):
        out = func(*args, **(kwargs or {}))
        for leaf in tree_flatten(out)[0]:
            if isinstance(leaf, torch.Tensor):
                self._track(leaf)
        return out


def _traced_peak_bytes(probe: TAEHV, latent: torch.Tensor, decode: Callable[[], torch.Tensor]) -> int:
    tracker = _PeakLiveBytes(chain((latent,), probe.parameters(), probe.buffers()))
    with tracker:
        decode()
    return tracker.peak


@functools.lru_cache(maxsize=64)
def _decode_peak_bytes(
    arch_name: str, frames: int, height: int, width: int, frame_size: int, clip_size: int | None,
) -> int:
    with torch.device("meta"):
        probe = TAEHV(arch_name)
    probe = probe.to(dtype=compute_dtype(torch.device("cuda"))).eval().requires_grad_(False)
    latent = torch.empty((1, frames, probe.latent_channels, height, width), device="meta")
    peaks = [_traced_peak_bytes(probe, latent, lambda: _middle_frame_on_device(probe, latent, frame_size))]
    if clip_size is not None and frames > 1:
        parallel, upscales = _clip_plan(probe, latent, clip_size)
        peaks.append(_traced_peak_bytes(probe, latent, lambda: _clip_on_device(probe, latent, parallel, upscales)))
    return max(peaks)


def preview_decode_transient_gb(
    decoder: TAEHV,
    latent_frames: int,
    latent_height: int,
    latent_width: int,
    *,
    frame_size: int,
    clip_size: int | None = None,
) -> float:
    peak = _decode_peak_bytes(
        str(decoder.arch_name), int(latent_frames), int(latent_height), int(latent_width),
        int(frame_size), None if clip_size is None else int(clip_size),
    )
    return peak / _BYTES_PER_GB
