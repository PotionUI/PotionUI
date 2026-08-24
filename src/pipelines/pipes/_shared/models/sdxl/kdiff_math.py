"""Shared k-diffusion-style noise-schedule math for SDXL denoising hooks.

Canonical implementation of the eps <-> x0 conversions and timestep-progress
helpers shared by sharpness_filter.py, sag/sdxl/hook.py, adm_guidance/sdxl/hook.py,
and sharpness/sdxl/hook.py.
"""
import torch


def alpha_for_timestep(timestep: torch.Tensor, alphas_cumprod: torch.Tensor) -> torch.Tensor:
    """Map a (possibly continuous) timestep to alphas_cumprod[timestep], clamped to range."""
    timestep_int = timestep.long().clamp(0, len(alphas_cumprod) - 1).cpu()
    return alphas_cumprod[timestep_int]


def eps_to_x0(latent: torch.Tensor, eps: torch.Tensor, alpha_t: torch.Tensor) -> torch.Tensor:
    """x0 = (latent - sqrt(1 - alpha) * eps) / sqrt(alpha)."""
    sqrt_alpha_t = torch.sqrt(alpha_t)
    sqrt_one_minus_alpha_t = torch.sqrt(1.0 - alpha_t)
    return (latent - sqrt_one_minus_alpha_t * eps) / sqrt_alpha_t


def x0_to_eps(latent: torch.Tensor, x0: torch.Tensor, alpha_t: torch.Tensor) -> torch.Tensor:
    """Inverse of eps_to_x0: eps = (latent - sqrt(alpha) * x0) / sqrt(1 - alpha)."""
    sqrt_alpha_t = torch.sqrt(alpha_t)
    sqrt_one_minus_alpha_t = torch.sqrt(1.0 - alpha_t)
    return (latent - sqrt_alpha_t * x0) / sqrt_one_minus_alpha_t


def timestep_progress(timestep: torch.Tensor, alphas_cumprod: torch.Tensor) -> float:
    """Fraction of the noise schedule elapsed: 1 - t / (num_train_timesteps - 1).

    Uses the first element when `timestep` is batched (all hooks assume a
    shared timestep across the batch).
    """
    num_train_timesteps = len(alphas_cumprod)
    t_value = float(timestep[0].item() if timestep.ndim > 0 else timestep.item())
    return 1.0 - (t_value / (num_train_timesteps - 1))
