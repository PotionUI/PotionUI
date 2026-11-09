from __future__ import annotations

from typing import Any, Callable, Sequence

import torch

WAN_TI2V_IN_DIM = 48


def is_ti2v_expert(dit: Any) -> bool:
    return dit is not None and int(dit.module.in_dim) == WAN_TI2V_IN_DIM


def require_single_ti2v_expert(low_dit: Any, *, generator: str, variant: str) -> None:
    if low_dit is not None:
        raise ValueError(
            f"{generator}: loaded model '{variant}' is the Wan 2.2 TI2V-5B, a single dense model; "
            f"leave the low-noise picker empty."
        )


def refuse_ti2v_last_frame(*, generator: str, variant: str) -> None:
    raise ValueError(
        f"{generator}: loaded model '{variant}' is the Wan 2.2 TI2V-5B, which conditions on a first "
        f"frame only; first+last-frame (flf) needs a Wan 2.2 A14B i2v checkpoint (in_dim=36)."
    )


def encode_ti2v_start_latent(
    start_frames: torch.Tensor,
    vae_encode: Callable[[torch.Tensor], torch.Tensor],
    *,
    latents_mean: Sequence[float],
    latents_std: Sequence[float],
) -> torch.Tensor:
    pixels = (start_frames[:1] * 2.0 - 1.0).permute(3, 0, 1, 2).unsqueeze(0)
    z = vae_encode(pixels)[:, :, :1]
    mean = torch.tensor(latents_mean, device=z.device, dtype=z.dtype).view(1, -1, 1, 1, 1)
    std = torch.tensor(latents_std, device=z.device, dtype=z.dtype).view(1, -1, 1, 1, 1)
    return (z - mean) / std


def pin_start_latent(latent: torch.Tensor, start_latent: torch.Tensor) -> torch.Tensor:
    start = start_latent.to(device=latent.device, dtype=latent.dtype).expand(latent.shape[0], -1, -1, -1, -1)
    return torch.cat([start, latent[:, :, 1:]], dim=2)


def first_frame_sigmas(sigma: torch.Tensor, batch: int, latent_frames: int) -> torch.Tensor:
    per_frame = sigma.reshape(-1)[:1].reshape(1, 1).expand(batch, latent_frames).clone()
    per_frame[:, 0] = 0.0
    return per_frame


class TI2VForward:

    def __init__(self, router: Any, start_latent: torch.Tensor) -> None:
        self.router = router
        self.start_latent = start_latent

    def __call__(self, x: torch.Tensor, sigma: torch.Tensor, conditioning: dict) -> torch.Tensor:
        x = pin_start_latent(x, self.start_latent)
        velocity = self.router(x, first_frame_sigmas(sigma, x.shape[0], x.shape[2]), conditioning)
        return torch.cat([torch.zeros_like(velocity[:, :, :1]), velocity[:, :, 1:]], dim=2)

    def cache_identity(self, sigma_val: float) -> tuple:
        return self.router.cache_identity(sigma_val)
