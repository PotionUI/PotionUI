"""Tests for shared k-diffusion math (src/pipelines/pipes/_shared/models/sdxl/kdiff_math.py)."""
import torch

from src.pipelines.pipes._shared.models.sdxl.kdiff_math import (
    alpha_for_timestep,
    eps_to_x0,
    x0_to_eps,
    timestep_progress,
)


def _alphas_cumprod(num_train_timesteps=1000):
    betas = torch.linspace(1e-4, 2e-2, num_train_timesteps)
    alphas = 1.0 - betas
    return torch.cumprod(alphas, dim=0)


class TestAlphaForTimestep:
    def test_returns_alpha_at_index(self):
        alphas_cumprod = _alphas_cumprod()
        result = alpha_for_timestep(torch.tensor(500), alphas_cumprod)
        assert torch.isclose(result, alphas_cumprod[500])

    def test_clamps_to_valid_range(self):
        alphas_cumprod = _alphas_cumprod()
        high = alpha_for_timestep(torch.tensor(5000), alphas_cumprod)
        low = alpha_for_timestep(torch.tensor(-5), alphas_cumprod)
        assert torch.isclose(high, alphas_cumprod[-1])
        assert torch.isclose(low, alphas_cumprod[0])

    def test_batched_timestep(self):
        alphas_cumprod = _alphas_cumprod()
        timestep = torch.tensor([100, 200, 300])
        result = alpha_for_timestep(timestep, alphas_cumprod)
        assert torch.allclose(result, alphas_cumprod[[100, 200, 300]])


class TestEpsX0RoundTrip:
    def test_round_trip_is_identity(self):
        alphas_cumprod = _alphas_cumprod()
        alpha_t = alpha_for_timestep(torch.tensor(400), alphas_cumprod)
        latent = torch.randn(1, 4, 8, 8)
        eps = torch.randn(1, 4, 8, 8)

        x0 = eps_to_x0(latent, eps, alpha_t)
        eps_reconstructed = x0_to_eps(latent, x0, alpha_t)

        assert torch.allclose(eps, eps_reconstructed, atol=1e-5)

    def test_x0_to_eps_is_inverse_of_eps_to_x0(self):
        alphas_cumprod = _alphas_cumprod()
        alpha_t = alpha_for_timestep(torch.tensor(700), alphas_cumprod)
        latent = torch.randn(2, 4, 16, 16)
        x0 = torch.randn(2, 4, 16, 16)

        eps = x0_to_eps(latent, x0, alpha_t)
        x0_reconstructed = eps_to_x0(latent, eps, alpha_t)

        assert torch.allclose(x0, x0_reconstructed, atol=1e-5)


class TestTimestepProgress:
    def test_progress_at_start_of_schedule(self):
        alphas_cumprod = _alphas_cumprod(1000)
        progress = timestep_progress(torch.tensor(999.0), alphas_cumprod)
        assert progress == 0.0

    def test_progress_at_end_of_schedule(self):
        alphas_cumprod = _alphas_cumprod(1000)
        progress = timestep_progress(torch.tensor(0.0), alphas_cumprod)
        assert progress == 1.0

    def test_progress_mid_schedule(self):
        alphas_cumprod = _alphas_cumprod(1001)
        progress = timestep_progress(torch.tensor(500.0), alphas_cumprod)
        assert torch.isclose(torch.tensor(progress), torch.tensor(0.5), atol=1e-6)

    def test_batched_timestep_uses_first_element(self):
        alphas_cumprod = _alphas_cumprod(1000)
        batched = timestep_progress(torch.tensor([999.0, 0.0]), alphas_cumprod)
        scalar = timestep_progress(torch.tensor(999.0), alphas_cumprod)
        assert batched == scalar
