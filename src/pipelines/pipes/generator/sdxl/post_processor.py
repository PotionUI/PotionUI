"""
SDXL post-processing utilities for converting latents to images.

This module handles the final stage of SDXL generation:
1. VAE decoding (latents → images)
2. Watermark application (optional)
3. Image format conversion (tensor → numpy/PIL)
"""

import logging as std_logging

import torch
from typing import Optional, Any
from diffusers.utils import logging

from src.platform.runtime.device import clear_gpu_memory

logger = logging.get_logger(__name__)


class SDXLPostProcessor:
    """Handles post-processing of SDXL generation outputs."""

    @staticmethod
    def decode_latents(
        vae: Any,
        latents: torch.Tensor,
        output_type: str = "pil",
        watermark: Optional[Any] = None,
        image_processor: Optional[Any] = None,
        upcast_vae_func: Optional[callable] = None,
    ) -> Any:
        """
        Decode latents to images using VAE.

        Args:
            vae: The VAE model for decoding
            latents: Latent tensors to decode [batch, 4, h, w]
            output_type: Output format ("pil", "np", or "latent")
            watermark: Optional watermark object with apply_watermark method
            image_processor: Image processor for final conversion
            upcast_vae_func: Optional function to upcast VAE to float32

        Returns:
            Images in requested format (PIL, numpy array, or latents)
        """
        # If output_type is latent, return latents as-is
        if output_type == "latent":
            return latents

        # Ensure latents match VAE dtype to prevent type mismatch errors.
        # The VAE may be in float32/bfloat16 (for precision) while latents are in float16 (pipeline dtype).
        vae_dtype = vae.dtype
        latents = latents.to(dtype=vae_dtype)

        # Diagnostic tensor stats gated on DEBUG — each reduction forces a GPU sync.
        _debug = logger.isEnabledFor(std_logging.DEBUG)
        if _debug:
            logger.debug(f"[VAE DIAG] Raw latents: min={latents.min():.4f}, max={latents.max():.4f}, "
                         f"mean={latents.mean():.4f}, std={latents.std():.4f}")

        # Unscale/denormalize the latents
        latents = SDXLPostProcessor._unscale_latents(vae, latents)

        if _debug:
            logger.debug(f"[VAE DIAG] Unscaled latents (/ {vae.config.scaling_factor}): "
                         f"min={latents.min():.4f}, max={latents.max():.4f}")

        # Decode latents to image tensors
        try:
            image = vae.decode(latents, return_dict=False)[0]
        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                logger.warning("[VAE] OOM during decode, retrying with tiled decoding")
                from src.platform.runtime.model_lifecycle.lifecycle import get_model_lifecycle
                models = get_model_lifecycle()
                if models is not None:
                    models.cleanup(aggressive=True)
                else:
                    clear_gpu_memory()
                if hasattr(vae, 'enable_tiling'):
                    vae.enable_tiling()
                image = vae.decode(latents, return_dict=False)[0]
                if hasattr(vae, 'disable_tiling'):
                    vae.disable_tiling()
            else:
                raise

        if _debug:
            logger.debug(f"[VAE DIAG] Decoded image tensor: min={image.min():.4f}, max={image.max():.4f}, "
                         f"mean={image.mean():.4f}, std={image.std():.4f}")

        # Keep the out-of-range warning at default log level (single reduction).
        # The standard VaeImageProcessor.denormalize() does (x+1)/2 then clamps to [0,1],
        # which handles minor out-of-range values gracefully via hard clamp.
        out_of_range = ((image < -1.0) | (image > 1.0)).float().mean() * 100
        if out_of_range > 10.0:
            logger.warning(f"[VAE DIAG] {out_of_range:.1f}% pixels outside [-1,1] after decode. "
                           f"Image range: [{image.min():.4f}, {image.max():.4f}]")

        # Apply watermark if available
        if watermark is not None:
            image = watermark.apply_watermark(image)

        # Convert to requested output format
        if image_processor is not None:
            image = image_processor.postprocess(image, output_type=output_type)

        return image

    @staticmethod
    def _unscale_latents(vae: Any, latents: torch.Tensor) -> torch.Tensor:
        """
        Unscale/denormalize the latents before VAE decoding.

        Args:
            vae: The VAE model
            latents: Latent tensors to unscale

        Returns:
            Unscaled latents
        """
        # Check if VAE has custom latents_mean and latents_std
        has_latents_mean = (
            hasattr(vae.config, "latents_mean") and vae.config.latents_mean is not None
        )
        has_latents_std = (
            hasattr(vae.config, "latents_std") and vae.config.latents_std is not None
        )

        if has_latents_mean and has_latents_std:
            # Use custom normalization parameters if available
            latents_mean = (
                torch.tensor(vae.config.latents_mean)
                .view(1, 4, 1, 1)
                .to(latents.device, latents.dtype)
            )
            latents_std = (
                torch.tensor(vae.config.latents_std)
                .view(1, 4, 1, 1)
                .to(latents.device, latents.dtype)
            )
            latents = latents * latents_std / vae.config.scaling_factor + latents_mean
        else:
            # Standard SDXL scaling
            logger.debug(f"[VAE] Using scaling_factor: {vae.config.scaling_factor}")
            latents = latents / vae.config.scaling_factor

        return latents
