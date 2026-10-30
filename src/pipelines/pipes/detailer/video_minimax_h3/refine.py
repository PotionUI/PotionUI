from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Tuple

import numpy as np
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.media.video_encode import AudioTrack
from src.pipelines.pipes._shared.video.tubes.detailer import TubeClip
from src.pipelines.pipes.generator.video_minimax_h3.audio import AUDIO_SAMPLE_RATE
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
    PIXEL_MEAN,
    PIXEL_STD,
    _encode_dtype,
    normalize_visual_latent,
)
from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    CANVAS_MULTIPLE,
    FPS,
    FRAMES_PER_CHUNK,
    LATENTS_PER_CHUNK,
    MAX_ASPECT_RATIO,
    MAX_DURATION_S,
)
from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe, _decode_waveform_via_ffmpeg
from src.pipelines.pipes.latent_upscaler.minimax_h3.diagnostics import diagnostics_dir, latent_stats
from src.pipelines.pipes.latent_upscaler.minimax_h3.geometry import pad_frames_to_h3_grid
from src.platform.observability.profiling import get_profiler
from src.platform.util.video_stream import has_audio_stream

logger = logging.getLogger(__name__)

STRENGTH_DENOISE = {"light": 0.40, "balanced": 0.55, "strong": 0.70}
PREVIEW_FRAME = 31


def max_window_frames() -> int:
    frames = int(MAX_DURATION_S * FPS)
    while frames % FRAMES_PER_CHUNK != LATENTS_PER_CHUNK:
        frames -= 1
    return frames


MAX_WINDOW_FRAMES = max_window_frames()


def fit_h3_canvas(width: int, height: int) -> Tuple[int, int]:
    width = max(CANVAS_MULTIPLE, int(math.ceil(width / CANVAS_MULTIPLE)) * CANVAS_MULTIPLE)
    height = max(CANVAS_MULTIPLE, int(math.ceil(height / CANVAS_MULTIPLE)) * CANVAS_MULTIPLE)
    if width > height * MAX_ASPECT_RATIO:
        height = int(math.ceil(width / MAX_ASPECT_RATIO / CANVAS_MULTIPLE)) * CANVAS_MULTIPLE
    elif height > width * MAX_ASPECT_RATIO:
        width = int(math.ceil(height / MAX_ASPECT_RATIO / CANVAS_MULTIPLE)) * CANVAS_MULTIPLE
    return width, height


def load_clip_audio(clip: TubeClip) -> Tuple[np.ndarray, int]:
    if has_audio_stream(clip.source):
        waveform, sample_rate = _decode_waveform_via_ffmpeg(clip.source)
        return waveform.numpy(), int(sample_rate)
    return np.zeros((2, 0), dtype=np.float32), AUDIO_SAMPLE_RATE


def slice_audio(waveform: np.ndarray, sample_rate: int, fps: float, start_frame: int, num_frames: int) -> AudioTrack:
    start = int(round(start_frame / fps * sample_rate))
    length = max(1, int(round(num_frames / fps * sample_rate)))
    segment = waveform[:, start:start + length]
    if segment.shape[1] < length:
        channels = max(1, waveform.shape[0])
        padded = np.zeros((channels, length), dtype=np.float32)
        padded[: segment.shape[0], : segment.shape[1]] = segment
        segment = padded
    return AudioTrack(waveform=np.ascontiguousarray(segment, dtype=np.float32), sample_rate=int(sample_rate))


def encode_tube(bundle: Any, frames01: torch.Tensor, device: str) -> Tuple[torch.Tensor, int]:
    padded, n0 = pad_frames_to_h3_grid(frames01)
    pixels = padded.permute(3, 0, 1, 2).unsqueeze(0).to(device=device, dtype=torch.float32)
    mean = torch.tensor(PIXEL_MEAN, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
    std = torch.tensor(PIXEL_STD, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
    pixels = (pixels - mean) / std
    vae = bundle.video_vae
    vae.move_to(device)
    try:
        with torch.no_grad():
            latent = vae.module.encode(pixels.to(dtype=_encode_dtype(vae.module)))
    finally:
        vae.offload()
        del pixels
    return latent, n0


class TubeRefineGenerator(GeneratorMinimaxH3Pipe):
    def generate_one(self, ctx, index, seed, progress):
        raw = super().generate_one(ctx, index, seed, progress)
        vae_module = ctx.extra.bundle.video_vae.module
        latent = normalize_visual_latent(raw, latents_mean=vae_module.latents_mean, latents_std=vae_module.latents_std)
        _mark_tube_latent("refined", raw)
        return self._decode_video(ctx.extra, latent)


def _mark_tube_latent(stage: str, latent: torch.Tensor) -> None:
    if diagnostics_dir() is None:
        return
    get_profiler().mark("h3_detailer.latent_stats", stage=stage, shape=list(latent.shape), **latent_stats(latent))


def _save_tube_previews(out_dir: Path, source01: torch.Tensor, refined: np.ndarray, n0: int) -> None:
    index = min(n0, int(refined.shape[0])) - 1
    index = min(index, PREVIEW_FRAME)
    if index < 0:
        return
    tag = len(list(out_dir.glob("h3_detailer_*_source.png")))
    try:
        source = (source01[index].clamp(0.0, 1.0).numpy() * 255.0).round().astype(np.uint8)
        Image.fromarray(source).save(out_dir / f"h3_detailer_{tag}_source.png")
        Image.fromarray(np.ascontiguousarray(refined[index], dtype=np.uint8)).save(out_dir / f"h3_detailer_{tag}_refined.png")
        get_profiler().mark("h3_detailer.previews", tag=tag, frame=index)
    except Exception:
        logger.warning("[H3_DETAILER] preview dump to %s failed", out_dir, exc_info=True)


def sample_tube(
    bundle: Any, cond_model: Any, latent: torch.Tensor, audio: AudioTrack, *,
    denoise: float, steps: int, video_sigma_shift: float, sampler: str, scheduler: str,
    device: str, seed: int, generation_outputs: callable,
) -> np.ndarray:
    config = TubeRefineGenerator.get_default_config()
    config.update({
        "mode": "video", "quantity": 1, "preview": False, "decode": False,
        "audio_source": "passthrough", "audio_refine": "lock",
        "denoise": float(denoise), "steps": int(steps), "video_sigma_shift": float(video_sigma_shift),
        "sampler": sampler, "scheduler": scheduler, "device": device,
    })
    refiner = TubeRefineGenerator(config=config)
    out = refiner.process(
        PipeInput(input={
            "model": bundle, "conditioning": [cond_model], "seed": [int(seed)],
            "initial_latent": [latent], "audio": [audio],
        }),
        generation_outputs,
    )
    return out.output["latent"][0]


def refine_tube_pixels(
    bundle: Any, cond_model: Any, pixels: torch.Tensor, clip: TubeClip, *,
    strength: str, device: str, seed: int, generation_outputs: callable,
    steps: int = 6, video_sigma_shift: float = 1.0, sampler: str = "euler", scheduler: str = "simple",
) -> np.ndarray:
    denoise = STRENGTH_DENOISE.get(str(strength), STRENGTH_DENOISE["balanced"])
    frames01 = pixels[0].float().add(1.0).mul(0.5).clamp(0.0, 1.0).permute(1, 2, 3, 0).cpu()
    waveform, sample_rate = load_clip_audio(clip)
    refined = []
    for start in range(0, int(frames01.shape[0]), MAX_WINDOW_FRAMES):
        chunk = frames01[start:start + MAX_WINDOW_FRAMES]
        audio = slice_audio(waveform, sample_rate, clip.fps, clip.start_frame + start, int(chunk.shape[0]))
        latent, n0 = encode_tube(bundle, chunk, device)
        _mark_tube_latent("encoded", latent)
        frames = sample_tube(
            bundle, cond_model, latent, audio,
            denoise=denoise, steps=steps, video_sigma_shift=video_sigma_shift,
            sampler=sampler, scheduler=scheduler, device=device, seed=seed,
            generation_outputs=generation_outputs,
        )
        out_dir = diagnostics_dir()
        if out_dir is not None:
            _save_tube_previews(out_dir, chunk, np.asarray(frames), n0)
        refined.append(np.asarray(frames)[:n0])
    return np.concatenate(refined, axis=0)
