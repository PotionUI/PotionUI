from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import torch
from PIL import Image

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


def decode_clip(
    decoder: TAEHV,
    x0: torch.Tensor,
    *,
    parallel: bool = True,
    space_upscales: int | None = None,
) -> torch.Tensor:
    latent = latent_to_ntchw(x0, decoder.latent_channels)
    place(decoder, latent.device)
    set_space_upscales(decoder, MAX_SPACE_UPSCALES if space_upscales is None else space_upscales)
    with torch.inference_mode():
        frames = decoder.decode_video(latent.to(next(decoder.parameters()).dtype), parallel=parallel)
    return frames[0].float().cpu()


def decode_middle_frame(
    decoder: TAEHV,
    x0: torch.Tensor,
    *,
    max_size: int,
) -> torch.Tensor:
    latent = latent_to_ntchw(x0, decoder.latent_channels)
    place(decoder, latent.device)
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
    return frames[0, index].float().cpu()


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


def decode_preview_clip(
    decoder: TAEHV,
    x0: torch.Tensor,
    *,
    max_frames: int = 25,
    max_size: int = 384,
) -> tuple[list[np.ndarray], list[int]]:
    latent = latent_to_ntchw(x0, decoder.latent_channels)
    upscales = (
        pick_space_upscales(decoder, latent.shape[-2], latent.shape[-1], max_size)
        if any(isinstance(m, torch.nn.Upsample) for m in decoder.decoder)
        else None
    )
    clip = decode_clip(
        decoder,
        x0,
        parallel=latent.shape[1] <= PARALLEL_DECODE_MAX_TOKENS,
        space_upscales=upscales,
    )
    indices = even_frame_indices(clip.shape[0], max_frames)
    frames = [np.asarray(frame_to_image(clip[i], max_size=max_size)) for i in indices]
    return frames, indices
