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


def composite_latents(mask: Tensor, generated: Tensor, source: Tensor) -> Tensor:
    return mask * generated.float() + (1.0 - mask) * source.float()


def restore_kept_latents(mask: Tensor, generated: Tensor, source: Tensor) -> Tensor:
    return torch.where(mask > 0, generated.float(), source.float())


class LatentMaskGuidance:
    def __init__(self, inner, source: Tensor, mask: Tensor) -> None:
        self.inner = inner
        self.source = source.float()
        self.mask = mask

    def __getattr__(self, name: str):
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index) -> Tensor:
        velocity = self.inner(model_fn, x, sigma, cond, uncond, step_index)
        s = sigma.reshape(-1)[0].float() if torch.is_tensor(sigma) else torch.tensor(float(sigma))
        s = s.clamp_min(1e-6).to(x.device)
        denoised = x - s * velocity
        pinned = composite_latents(self.mask, denoised, self.source)
        return (x - pinned) / s
