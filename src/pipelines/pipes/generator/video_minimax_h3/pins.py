from __future__ import annotations

from dataclasses import dataclass

import torch

from src.pipelines.pipes.generator.video_minimax_h3.audio import pack_audio_rows
from src.platform.runtime.native.arch.minimax_h3.packing import patchify_video_latents

Tensor = torch.Tensor


@dataclass(frozen=True)
class RowPins:
    video_index: Tensor
    video_values: Tensor
    audio_index: Tensor
    audio_values: Tensor


def video_pin(
    latent: Tensor, *, start: int, rows_per_frame: int, patch_size: tuple[int, int, int],
) -> tuple[Tensor, Tensor]:
    if start < 0:
        raise ValueError(f"a pinned span must start at a latent frame >= 0, got {start}")
    values = patchify_video_latents(latent.float(), patch_size)
    first = start * rows_per_frame
    return torch.arange(first, first + values.shape[0], device=latent.device), values


def audio_pin(latents: Tensor, *, start: int, num_audio_latents: int) -> tuple[Tensor, Tensor]:
    channels, _, count = latents.shape
    if start < 0 or start + count > num_audio_latents:
        raise ValueError(
            f"a pinned audio span of {count} latent(s) from {start} does not fit {num_audio_latents} audio latents"
        )
    index = torch.cat([
        torch.arange(channel * num_audio_latents + start, channel * num_audio_latents + start + count,
                     device=latents.device)
        for channel in range(channels)
    ])
    return index, pack_audio_rows(latents.float(), audio_channels=channels)


def merge_pins(video: list[tuple[Tensor, Tensor]], audio: list[tuple[Tensor, Tensor]], *, video_width: int,
               audio_width: int, device) -> RowPins:
    def join(parts: list[tuple[Tensor, Tensor]], width: int) -> tuple[Tensor, Tensor]:
        if not parts:
            return (torch.zeros(0, dtype=torch.long, device=device),
                    torch.zeros(0, width, dtype=torch.float32, device=device))
        index = torch.cat([part[0].to(device) for part in parts])
        if index.unique().numel() != index.numel():
            raise ValueError("pinned spans overlap")
        return index, torch.cat([part[1].to(device=device, dtype=torch.float32) for part in parts])

    video_index, video_values = join(video, video_width)
    audio_index, audio_values = join(audio, audio_width)
    return RowPins(video_index=video_index, video_values=video_values, audio_index=audio_index,
                   audio_values=audio_values)


def pinned_velocity(velocity: Tensor, sample: Tensor, timestep: float, index: Tensor, values: Tensor) -> Tensor:
    if index.numel() == 0:
        return velocity
    pinned = velocity.clone()
    pinned[index] = (values.to(device=sample.device, dtype=sample.dtype) - sample[index]) / (1.0 - float(timestep))
    return pinned


def restore_pinned(rows: Tensor, offset: int, index: Tensor, values: Tensor) -> None:
    if index.numel():
        rows[offset + index.to(rows.device)] = values.to(device=rows.device, dtype=rows.dtype)
