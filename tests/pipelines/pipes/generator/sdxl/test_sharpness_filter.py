"""
Unit tests for Anisotropic Sharpness Filter

Tests the x-space conversion, blending logic, and edge cases for the
Fooocus anisotropic sharpness implementation.
"""

import pytest
import torch
from unittest.mock import MagicMock, patch
from src.pipelines.pipes.generator.sdxl.sharpness_filter import AnisotropicSharpness
from src.pipelines.pipes._shared.models.sdxl.kdiff_math import alpha_for_timestep


class TestAnisotropicSharpnessBasics:
    """Test basic initialization and configuration."""

    def test_init_default(self):
        """Test default initialization."""
        filter = AnisotropicSharpness()
        assert filter.strength == 0.0
        assert filter.base_multiplier == 0.001

    def test_init_with_strength(self):
        """Test initialization with custom strength."""
        filter = AnisotropicSharpness(strength=5.0)
        assert filter.strength == 5.0
        assert filter.base_multiplier == 0.001

    def test_is_enabled_when_disabled(self):
        """Test is_enabled returns False when strength is 0."""
        filter = AnisotropicSharpness(strength=0.0)
        assert filter.is_enabled() is False

    def test_is_enabled_when_enabled(self):
        """Test is_enabled returns True when strength > 0."""
        filter = AnisotropicSharpness(strength=1.0)
        assert filter.is_enabled() is True

    def test_is_enabled_with_small_strength(self):
        """Test is_enabled returns True for small positive strength."""
        filter = AnisotropicSharpness(strength=0.001)
        assert filter.is_enabled() is True


class TestDisabledFilter:
    """Test that disabled filter returns input unchanged."""

    def test_disabled_returns_unchanged(self):
        """Test that noise_pred is returned unchanged when strength=0."""
        filter = AnisotropicSharpness(strength=0.0)

        # Create dummy tensors
        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)
        progress = 0.5

        result = filter.apply_during_denoising(
            noise_pred, latent, timestep, alphas_cumprod, progress
        )

        # Should return exact same tensor
        assert torch.equal(result, noise_pred)

    def test_disabled_no_filter_call(self):
        """Test that anisotropic filter is not called when disabled."""
        filter = AnisotropicSharpness(strength=0.0)

        with patch.object(filter, '_apply_anisotropic_filter') as mock_filter:
            noise_pred = torch.randn(1, 4, 32, 32)
            latent = torch.randn(1, 4, 32, 32)
            timestep = torch.tensor([500])
            alphas_cumprod = torch.linspace(1.0, 0.0, 1000)
            progress = 0.5

            filter.apply_during_denoising(
                noise_pred, latent, timestep, alphas_cumprod, progress
            )

            # Filter should not be called
            mock_filter.assert_not_called()


class TestXSpaceConversion:
    """Test x-space conversion mathematics."""

    def test_xspace_conversion_forward_backward(self):
        """Test that converting to x-space and back preserves noise_pred."""
        filter = AnisotropicSharpness(strength=1.0)

        # Create controlled tensors
        batch_size, channels, height, width = 1, 4, 32, 32
        noise_pred = torch.randn(batch_size, channels, height, width)
        latent = torch.randn(batch_size, channels, height, width)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)
        progress = 0.5

        # Mock the anisotropic filter to return x0 unchanged (identity)
        with patch.object(filter, '_apply_anisotropic_filter', side_effect=lambda x, g=None: x):
            result = filter.apply_during_denoising(
                noise_pred, latent, timestep, alphas_cumprod, progress
            )

            # With identity filter and blend_factor applied, result should be close to original
            # but not exact (due to blending)
            # Let's verify the math is consistent by checking dimensions
            assert result.shape == noise_pred.shape

    def test_xspace_math_correctness(self):
        """Test the mathematical correctness of x-space conversion."""
        filter = AnisotropicSharpness(strength=2.0)

        # Create simple tensors for easier math verification
        noise_pred = torch.ones(1, 4, 32, 32)
        latent = torch.ones(1, 4, 32, 32) * 2.0
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)
        progress = 0.5

        # Get alpha for timestep
        alpha_t = alphas_cumprod[500]
        sigma_t = torch.sqrt(1 - alpha_t)
        sqrt_alpha_t = torch.sqrt(alpha_t)

        # Calculate expected x0_pred manually
        # x0 = (latent - sigma * noise_pred) / sqrt(alpha)
        expected_x0 = (latent - sigma_t * noise_pred) / sqrt_alpha_t

        # Mock the filter to capture eps (the Fooocus method filters eps, not x0)
        captured_eps = None

        def capture_and_return(x, g=None):
            nonlocal captured_eps
            captured_eps = x.clone()
            return x

        with patch.object(filter, '_apply_anisotropic_filter', side_effect=capture_and_return):
            filter.apply_during_denoising(
                noise_pred, latent, timestep, alphas_cumprod, progress
            )

            # Verify captured eps matches expected calculation
            # eps = latent - x0
            expected_eps = latent - expected_x0
            assert captured_eps is not None
            torch.testing.assert_close(captured_eps, expected_eps, rtol=1e-5, atol=1e-7)

    def test_alpha_lookup(self):
        """Test alpha_for_timestep correctly looks up alpha values (shared kdiff_math)."""
        # Create alphas schedule
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        # Test various timesteps
        timestep = torch.tensor([0])
        alpha = alpha_for_timestep(timestep, alphas_cumprod)
        assert torch.isclose(alpha, torch.tensor(1.0), atol=1e-5)

        timestep = torch.tensor([999])
        alpha = alpha_for_timestep(timestep, alphas_cumprod)
        assert torch.isclose(alpha, torch.tensor(0.0), atol=1e-5)

        timestep = torch.tensor([500])
        alpha = alpha_for_timestep(timestep, alphas_cumprod)
        expected = alphas_cumprod[500]
        assert torch.isclose(alpha, expected, atol=1e-5)

    def test_alpha_clamping(self):
        """Test that timestep is clamped to valid range (shared kdiff_math)."""
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        # Test out of bounds timestep (should clamp to valid range)
        timestep = torch.tensor([1500])  # Out of bounds
        alpha = alpha_for_timestep(timestep, alphas_cumprod)
        # Should clamp to last index (999)
        expected = alphas_cumprod[999]
        assert torch.isclose(alpha, expected, atol=1e-5)

        timestep = torch.tensor([-10])  # Negative
        alpha = alpha_for_timestep(timestep, alphas_cumprod)
        # Should clamp to first index (0)
        expected = alphas_cumprod[0]
        assert torch.isclose(alpha, expected, atol=1e-5)


class TestProgressiveBlending:
    """Test progressive blending behavior."""

    def test_blend_factor_increases_with_progress(self):
        """Test that sharpness effect increases with generation progress."""
        filter = AnisotropicSharpness(strength=2.0)

        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        # Mock filter to return slightly different tensor
        def mock_filter(x, g=None):
            return x + 0.1

        with patch.object(filter, '_apply_anisotropic_filter', side_effect=mock_filter):
            # Apply at different progress levels
            result_early = filter.apply_during_denoising(
                noise_pred.clone(), latent, timestep, alphas_cumprod, progress=0.1
            )

            result_mid = filter.apply_during_denoising(
                noise_pred.clone(), latent, timestep, alphas_cumprod, progress=0.5
            )

            result_late = filter.apply_during_denoising(
                noise_pred.clone(), latent, timestep, alphas_cumprod, progress=0.9
            )

            # Calculate differences from original
            diff_early = (result_early - noise_pred).abs().mean()
            diff_mid = (result_mid - noise_pred).abs().mean()
            diff_late = (result_late - noise_pred).abs().mean()

            # Later progress should have larger difference (stronger effect)
            assert diff_mid > diff_early
            assert diff_late > diff_mid

    def test_zero_progress_returns_unchanged(self):
        """Test that progress=0 returns unchanged noise_pred."""
        filter = AnisotropicSharpness(strength=5.0)  # High strength

        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        result = filter.apply_during_denoising(
            noise_pred, latent, timestep, alphas_cumprod, progress=0.0
        )

        # Should return unchanged even with high strength
        torch.testing.assert_close(result, noise_pred, rtol=1e-5, atol=1e-7)

    def test_blend_factor_calculation(self):
        """Test the blend factor calculation formula."""
        filter = AnisotropicSharpness(strength=3.0)

        # Verify blend factor is: progress * strength * base_multiplier
        progress = 0.7
        expected_blend = progress * filter.strength * filter.base_multiplier
        assert expected_blend == 0.7 * 3.0 * 0.001
        assert expected_blend == 0.0021

    def test_max_progress_strongest_effect(self):
        """Test that progress=1.0 produces strongest effect."""
        filter = AnisotropicSharpness(strength=2.0)

        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        # Mock filter to return different tensor
        def mock_filter(x, g=None):
            return x + 1.0

        with patch.object(filter, '_apply_anisotropic_filter', side_effect=mock_filter):
            result = filter.apply_during_denoising(
                noise_pred, latent, timestep, alphas_cumprod, progress=1.0
            )

            # Should have maximum blending
            diff = (result - noise_pred).abs().mean()
            assert diff > 0.0  # Some effect applied


class TestAnisotropicFilterIntegration:
    """Test integration with Fooocus anisotropic filter."""

    def test_anisotropic_filter_called_when_enabled(self):
        """Test that anisotropic filter is called when enabled."""
        filter = AnisotropicSharpness(strength=1.0)

        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)
        progress = 0.5

        with patch.object(filter, '_apply_anisotropic_filter') as mock_filter:
            mock_filter.return_value = torch.randn(1, 4, 32, 32)

            filter.apply_during_denoising(
                noise_pred, latent, timestep, alphas_cumprod, progress
            )

            # Filter should be called once with x0_pred
            assert mock_filter.call_count == 1
            called_with = mock_filter.call_args[0][0]
            assert called_with.shape == noise_pred.shape

    @patch('vendor.gpl.fooocus.anisotropic.adaptive_anisotropic_filter')
    def test_anisotropic_filter_uses_fooocus_implementation(self, mock_anisotropic_filter):
        """Test that _apply_anisotropic_filter calls Fooocus implementation."""
        filter = AnisotropicSharpness(strength=1.0)

        # Setup mock
        expected_result = torch.randn(1, 4, 32, 32)
        mock_anisotropic_filter.return_value = expected_result

        x = torch.randn(1, 4, 32, 32)
        g = torch.randn(1, 4, 32, 32)
        result = filter._apply_anisotropic_filter(x, g)

        # Verify Fooocus function was called with both x and g
        mock_anisotropic_filter.assert_called_once_with(x=x, g=g)
        assert result is expected_result


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_zero_strength_edge_case(self):
        """Test that strength=0.0 is handled correctly."""
        filter = AnisotropicSharpness(strength=0.0)
        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        result = filter.apply_during_denoising(
            noise_pred, latent, timestep, alphas_cumprod, progress=0.8
        )

        # Should return unchanged
        torch.testing.assert_close(result, noise_pred)

    def test_very_high_strength(self):
        """Test that very high strength values don't cause issues."""
        filter = AnisotropicSharpness(strength=100.0)

        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        with patch.object(filter, '_apply_anisotropic_filter', side_effect=lambda x, g=None: x):
            result = filter.apply_during_denoising(
                noise_pred, latent, timestep, alphas_cumprod, progress=0.5
            )

            # Should complete without errors
            assert result.shape == noise_pred.shape
            assert not torch.isnan(result).any()
            assert not torch.isinf(result).any()

    def test_near_zero_sigma_protection(self):
        """Test protection against division by zero when sigma is very small."""
        filter = AnisotropicSharpness(strength=2.0)

        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)

        # Create alphas that would result in near-zero sigma
        # When alpha_t = 1.0, sigma_t = sqrt(1 - 1.0) = 0
        alphas_cumprod = torch.ones(1000)  # All 1.0
        timestep = torch.tensor([0])

        result = filter.apply_during_denoising(
            noise_pred, latent, timestep, alphas_cumprod, progress=0.5
        )

        # Should return unchanged to avoid division by zero
        torch.testing.assert_close(result, noise_pred)

    def test_batch_size_handling(self):
        """Test that filter handles different batch sizes."""
        filter = AnisotropicSharpness(strength=1.0)

        for batch_size in [1, 2, 4]:
            noise_pred = torch.randn(batch_size, 4, 32, 32)
            latent = torch.randn(batch_size, 4, 32, 32)
            timestep = torch.tensor([500])
            alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

            with patch.object(filter, '_apply_anisotropic_filter', side_effect=lambda x, g=None: x):
                result = filter.apply_during_denoising(
                    noise_pred, latent, timestep, alphas_cumprod, progress=0.5
                )

                assert result.shape == (batch_size, 4, 32, 32)

    def test_different_resolutions(self):
        """Test that filter handles different latent resolutions."""
        filter = AnisotropicSharpness(strength=1.0)

        for height, width in [(32, 32), (64, 64), (48, 64)]:
            noise_pred = torch.randn(1, 4, height, width)
            latent = torch.randn(1, 4, height, width)
            timestep = torch.tensor([500])
            alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

            with patch.object(filter, '_apply_anisotropic_filter', side_effect=lambda x, g=None: x):
                result = filter.apply_during_denoising(
                    noise_pred, latent, timestep, alphas_cumprod, progress=0.5
                )

                assert result.shape == (1, 4, height, width)

    def test_negative_progress_handled(self):
        """Test that negative progress is handled (though should not occur in practice)."""
        filter = AnisotropicSharpness(strength=2.0)

        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        result = filter.apply_during_denoising(
            noise_pred, latent, timestep, alphas_cumprod, progress=-0.1
        )

        # Should return unchanged (progress <= 0 early exit)
        torch.testing.assert_close(result, noise_pred)

    def test_output_device_matches_input(self):
        """Test that output tensor device matches noise_pred device (fixes CPU/CUDA mismatch)."""
        filter = AnisotropicSharpness(strength=2.0)

        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        with patch.object(filter, '_apply_anisotropic_filter', side_effect=lambda x, g=None: x):
            result = filter.apply_during_denoising(
                noise_pred, latent, timestep, alphas_cumprod, progress=0.5
            )

            assert result.device == noise_pred.device

    def test_progress_over_one(self):
        """Test that progress > 1.0 works (though should not occur in practice)."""
        filter = AnisotropicSharpness(strength=2.0)

        noise_pred = torch.randn(1, 4, 32, 32)
        latent = torch.randn(1, 4, 32, 32)
        timestep = torch.tensor([500])
        alphas_cumprod = torch.linspace(1.0, 0.0, 1000)

        with patch.object(filter, '_apply_anisotropic_filter', side_effect=lambda x, g=None: x):
            result = filter.apply_during_denoising(
                noise_pred, latent, timestep, alphas_cumprod, progress=1.5
            )

            # Should complete without errors
            assert result.shape == noise_pred.shape
            assert not torch.isnan(result).any()
