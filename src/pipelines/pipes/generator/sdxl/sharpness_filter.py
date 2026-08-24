# Derived from: Fooocus sharpness/anisotropic technique (GPL-3.0); filter imported from vendor/gpl/
"""
Anisotropic Sharpness Filter for SDXL

This module implements the Fooocus anisotropic sharpness technique for edge enhancement
during the denoising process. Unlike traditional post-processing sharpness filters,
this technique operates during generation by manipulating predictions in x-space
(predicted clean image space) rather than noise space.

The key innovation is converting noise predictions to x-space, applying the anisotropic
filter, then converting back to noise space with progressive blending that strengthens
as generation progresses. This produces sharper edges without artifacts.
"""

import torch
from typing import Optional

from src.pipelines.pipes._shared.models.sdxl.kdiff_math import alpha_for_timestep, eps_to_x0, x0_to_eps


class AnisotropicSharpness:
    """
    Fooocus anisotropic sharpness filter for edge enhancement during denoising.

    This filter operates on the predicted clean image (x-space) rather than the noise
    prediction directly. The process:

    1. Convert noise prediction to x-space using: x0 = (latent - sigma * noise_pred) / sqrt(alpha)
    2. Apply anisotropic edge-preserving filter to x0
    3. Blend filtered result with original based on progress: x0_enhanced = x0 + blend * (filtered - x0)
    4. Convert back to noise space: noise_pred = (latent - sqrt(alpha) * x0_enhanced) / sigma

    The blend factor increases with generation progress, making sharpness stronger near the end.
    This progressive application prevents artifacts in early generation stages.

    Attributes:
        strength: Sharpness strength multiplier (0.0 = disabled)
        base_multiplier: Fooocus base constant for blend calculation (default: 0.001)
    """

    def __init__(self, strength: float = 0.0):
        """
        Initialize the anisotropic sharpness filter.

        Args:
            strength: Sharpness strength (0.0 = disabled, higher = stronger effect)
                     Typical range: 0.0 to 10.0
        """
        self.strength = strength
        self.base_multiplier = 0.001  # Fooocus constant

    def is_enabled(self) -> bool:
        """
        Check if sharpness filter is enabled.

        Returns:
            True if strength > 0.0, False otherwise
        """
        return self.strength > 0.0

    def apply_during_denoising(
        self,
        noise_pred: torch.Tensor,
        latent: torch.Tensor,
        timestep: torch.Tensor,
        alphas_cumprod: torch.Tensor,
        progress: float
    ) -> torch.Tensor:
        """
        Apply sharpness filter during denoising using the Fooocus method.

        This method implements the Fooocus approach: filter eps (error in x-space)
        using x0 (predicted clean image) as guidance, then blend progressively.
        This produces sharper edges without artifacts.

        The process:
        1. Convert noise_pred to x0 (predicted clean image)
        2. Calculate eps in x-space: eps = latent - x0
        3. Apply anisotropic filter to eps with x0 as guidance
        4. Blend filtered eps with original eps based on progressive alpha
        5. Convert back to noise prediction

        Args:
            noise_pred: Current noise prediction from UNet [B, C, H, W]
            latent: Current latent representation [B, C, H, W]
            timestep: Current timestep tensor (used to look up alpha)
            alphas_cumprod: Cumulative product of alphas from scheduler (noise schedule)
            progress: Generation progress from 0.0 (start) to 1.0 (end)

        Returns:
            Enhanced noise prediction with sharpness applied [B, C, H, W]
        """
        if not self.is_enabled():
            return noise_pred

        # Edge case: no progress means no blending
        if progress <= 0.0:
            return noise_pred

        # Get alpha and sigma for current timestep
        alpha_t = alpha_for_timestep(timestep, alphas_cumprod).to(noise_pred.device)
        sqrt_one_minus_alpha_t = torch.sqrt(1.0 - alpha_t)

        # Prevent division by zero
        if sqrt_one_minus_alpha_t.abs().max() < 1e-8:
            return noise_pred

        # Convert noise prediction to x0 (predicted clean image)
        # Formula: x0 = (latent - sqrt(1 - alpha) * noise_pred) / sqrt(alpha)
        x0_pred = eps_to_x0(latent, noise_pred, alpha_t)

        # Calculate eps in x-space (Fooocus method)
        # eps = latent - x0
        eps = latent - x0_pred

        # Calculate progressive alpha (blend factor increases with progress)
        # Formula: alpha = progress * strength * base_multiplier
        alpha = progress * self.strength * self.base_multiplier

        # Only apply filtering if alpha > 0 (skips very early steps)
        if alpha > 0:
            # Apply anisotropic filter to eps with x0 as guidance (Fooocus way)
            filtered_eps = self._apply_anisotropic_filter(eps, x0_pred)

            # Blend filtered and original eps using progressive alpha
            # Formula: blended_eps = filtered_eps * alpha + eps * (1 - alpha)
            eps_blended = filtered_eps * alpha + eps * (1.0 - alpha)

            # Convert back to noise prediction
            # new_x0 = latent - eps_blended
            # noise_pred = (latent - sqrt(alpha) * new_x0) / sqrt(1 - alpha)
            new_x0 = latent - eps_blended
            noise_pred_enhanced = x0_to_eps(latent, new_x0, alpha_t)

            return noise_pred_enhanced

        return noise_pred

    def _apply_anisotropic_filter(self, x: torch.Tensor, g: torch.Tensor = None) -> torch.Tensor:
        """
        Apply Fooocus anisotropic filter for edge-preserving enhancement.

        The anisotropic filter enhances edges while preserving smooth regions,
        avoiding the artifacts that traditional sharpening can introduce.

        In the Fooocus method, we filter eps (error in x-space) using x0
        (predicted clean image) as guidance for better edge detection.

        Args:
            x: Tensor to filter (typically eps in x-space) [B, C, H, W]
            g: Optional guidance tensor (typically x0 predicted clean image) [B, C, H, W]

        Returns:
            Filtered tensor [B, C, H, W]
        """
        # Import the Fooocus implementation
        from vendor.gpl.fooocus.anisotropic import adaptive_anisotropic_filter
        return adaptive_anisotropic_filter(x=x, g=g)
