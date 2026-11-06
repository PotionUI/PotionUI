from __future__ import annotations

import torch

Tensor = torch.Tensor


def validate_latent_mask(mask: Tensor, latents: Tensor) -> Tensor:
    if mask.ndim != latents.ndim:
        raise ValueError(f"latent mask has {mask.ndim} dims, latents have {latents.ndim}")
    for got, want in zip(mask.shape, latents.shape):
        if got not in (1, want):
            raise ValueError(f"latent mask shape {tuple(mask.shape)} does not broadcast to {tuple(latents.shape)}")
    return mask.to(device=latents.device, dtype=torch.float32).clamp(0.0, 1.0)


def free_cells(mask: Tensor, step_index: int, total_steps: int) -> Tensor:
    progress = min(max(step_index, 0), max(total_steps, 1)) / max(total_steps, 1)
    return ((mask >= 1.0 - progress) & (mask > 0)).to(torch.float32)


def restore_kept_latents(mask: Tensor, generated: Tensor, source: Tensor, total_steps: int) -> Tensor:
    freed = free_cells(mask, total_steps - 1, total_steps) > 0
    return torch.where(freed, generated.float(), source.float())


class LatentMaskGuidance:
    def __init__(self, inner, source: Tensor, mask: Tensor, total_steps: int) -> None:
        self.inner = inner
        self.source = source.float()
        self.mask = mask
        self.total_steps = int(total_steps)

    def __getattr__(self, name: str):
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index) -> Tensor:
        velocity = self.inner(model_fn, x, sigma, cond, uncond, step_index)
        s = sigma.reshape(-1)[0].float() if torch.is_tensor(sigma) else torch.tensor(float(sigma))
        s = s.clamp_min(1e-6).to(x.device)
        denoised = x - s * velocity
        free = free_cells(self.mask, step_index, self.total_steps)
        pinned = free * denoised + (1.0 - free) * self.source
        return (x - pinned) / s
