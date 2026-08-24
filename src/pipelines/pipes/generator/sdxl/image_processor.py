"""
SDXL image and mask preprocessing utilities.

This module handles preprocessing of input images and masks for SDXL generation,
including PIL/tensor conversion, VAE encoding, resizing, and batch expansion.
"""

import torch
import numpy as np
from typing import Optional, Union
import logging

logger = logging.getLogger(__name__)


class SDXLImageProcessor:
    """Handles preprocessing of images and masks for SDXL generation."""

    @staticmethod
    def preprocess_image_for_img2img(
        image,
        vae,
        image_processor,
        height: int,
        width: int,
        device,
        dtype,
        generator,
        batch_size: int,
        num_images_per_prompt: int
    ):
        """
        Preprocess input image for img2img generation.

        Handles PIL/tensor conversion, VAE encoding, and batch expansion.
        Automatically handles VAE upcasting for float16 models.

        Args:
            image: Input image (PIL or tensor)
            vae: VAE model for encoding
            image_processor: Image processor for preprocessing
            height: Target height in pixels
            width: Target width in pixels
            device: Target device (cuda/cpu)
            dtype: Target dtype (typically torch.float16)
            generator: Random generator for VAE sampling
            batch_size: Batch size for generation
            num_images_per_prompt: Number of images to generate per prompt

        Returns:
            Encoded latent tensor ready for img2img [batch_size * num_images_per_prompt, 4, H//8, W//8]
        """
        # Preprocess image
        if hasattr(image, 'convert'):  # PIL Image
            image = image_processor.preprocess(image, height=height, width=width)
        image = image.to(device=device, dtype=dtype)

        # Encode image to latents
        if image.shape[1] == 4:
            # Image is already in latent space
            init_latents = image
        else:
            # Encode image through VAE - match image dtype to VAE dtype
            image = image.to(dtype=vae.dtype)

            init_latents = vae.encode(image).latent_dist.sample(generator)
            init_latents = init_latents * vae.config.scaling_factor

        # Ensure init_latents has the same dtype as prompt_embeds to avoid dtype mismatch
        init_latents = init_latents.to(dtype=dtype)

        # Expand init_latents for batch
        if init_latents.shape[0] < batch_size * num_images_per_prompt:
            init_latents = init_latents.repeat(batch_size * num_images_per_prompt, 1, 1, 1)

        return init_latents

    @staticmethod
    def preprocess_mask_for_inpainting(
        mask_image,
        height: int,
        width: int,
        vae_scale_factor: int,
        device,
        dtype,
        batch_size: int,
        num_images_per_prompt: int
    ):
        """
        Preprocess mask image for inpainting.

        Handles PIL/tensor conversion, resizing to latent space dimensions,
        normalization, and batch expansion. The mask is resized to match
        the latent space resolution (image dimensions / vae_scale_factor).

        Args:
            mask_image: Input mask (PIL Image or tensor)
            height: Target image height in pixels
            width: Target image width in pixels
            vae_scale_factor: VAE scaling factor (typically 8 for SDXL)
            device: Target device (cuda/cpu)
            dtype: Target dtype (typically torch.float16)
            batch_size: Batch size for generation
            num_images_per_prompt: Number of images to generate per prompt

        Returns:
            Preprocessed mask tensor in latent space [batch_size * num_images_per_prompt, 1, H//8, W//8]
            with values in [0, 1] range where 1 = masked area (to inpaint), 0 = keep original
        """
        # Preprocess mask to tensor if it's a PIL Image
        if hasattr(mask_image, 'convert'):
            # Convert to grayscale and then to tensor [0, 1]
            mask_pil = mask_image.convert('L')
            mask_array = np.array(mask_pil).astype(np.float32) / 255.0
            mask_tensor = torch.from_numpy(mask_array).unsqueeze(0).unsqueeze(0)  # [1, 1, H, W]
        elif isinstance(mask_image, torch.Tensor):
            # Already a tensor, ensure correct shape
            if mask_image.ndim == 2:
                mask_tensor = mask_image.unsqueeze(0).unsqueeze(0)  # [H, W] -> [1, 1, H, W]
            elif mask_image.ndim == 3:
                mask_tensor = mask_image.unsqueeze(0)  # [1, H, W] -> [1, 1, H, W]
            else:
                mask_tensor = mask_image
        else:
            raise ValueError(f"[INPAINTING] Unsupported mask type: {type(mask_image)}")

        # Resize mask to match latent dimensions
        # Latent dimensions are image dimensions / vae_scale_factor (typically /8)
        latent_height = height // vae_scale_factor
        latent_width = width // vae_scale_factor

        mask_latents = torch.nn.functional.interpolate(
            mask_tensor.to(device=device, dtype=dtype),
            size=(latent_height, latent_width),
            mode='bilinear',
            align_corners=False
        )

        # Ensure mask is in range [0, 1] and expand for batch
        mask_latents = torch.clamp(mask_latents, 0.0, 1.0)
        if mask_latents.shape[0] < batch_size * num_images_per_prompt:
            mask_latents = mask_latents.repeat(batch_size * num_images_per_prompt, 1, 1, 1)

        logger.debug(f"[INPAINTING] Processed mask to latent space: mask shape={mask_latents.shape}, "
                   f"range=[{mask_latents.min():.3f}, {mask_latents.max():.3f}]")

        return mask_latents
