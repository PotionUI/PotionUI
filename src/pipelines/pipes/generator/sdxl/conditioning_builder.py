"""
SDXL Conditioning Builder

Builds SDXL-specific conditioning tensors for the diffusion process.

This module handles the construction of SDXL's unique conditioning mechanism,
which includes time embeddings for resolution conditioning and proper tensor
concatenation for classifier-free guidance (CFG).

SDXL uses additional time embeddings to condition the model on the original
image size, crop coordinates, and target size. This allows the model to
generate high-quality images at various resolutions while understanding the
intended output dimensions.
"""

import torch
from typing import Tuple, Dict, Union, List, Optional, Any
from diffusers.utils.torch_utils import randn_tensor


class SDXLConditioningBuilder:
    """
    Builds SDXL-specific conditioning tensors.

    SDXL requires additional conditioning beyond text embeddings:
    1. Time IDs: Resolution conditioning (original size, crops, target size)
    2. Pooled embeddings: Text encoder pooled outputs for aesthetic control
    3. Proper concatenation for classifier-free guidance

    This class provides static methods for building these conditioning tensors
    in the exact format expected by the SDXL UNet.
    """

    @staticmethod
    def build_time_ids(
        original_size: Tuple[int, int],
        crops_coords_top_left: Tuple[int, int],
        target_size: Tuple[int, int],
        dtype: torch.dtype,
        device: torch.device,
        batch_size: int = 1
    ) -> torch.Tensor:
        """
        Compute SDXL time embeddings for resolution conditioning.

        SDXL uses time embeddings to inform the model about the image resolution
        and cropping parameters. This allows the model to generate images at
        different resolutions while maintaining quality and understanding the
        intended output dimensions.

        Args:
            original_size: Original image dimensions (height, width) before any processing
            crops_coords_top_left: Top-left coordinates (y, x) of the crop region
            target_size: Target output dimensions (height, width)
            dtype: Tensor data type (e.g., torch.float16, torch.float32)
            device: Device to create tensor on (e.g., "cuda", "cpu")
            batch_size: Number of samples in the batch (default: 1)

        Returns:
            torch.Tensor: Time embeddings tensor of shape [batch_size, 6]
                         Values are: [orig_h, orig_w, crop_y, crop_x, target_h, target_w]

        Example:
            >>> time_ids = SDXLConditioningBuilder.build_time_ids(
            ...     original_size=(1024, 1024),
            ...     crops_coords_top_left=(0, 0),
            ...     target_size=(1024, 1024),
            ...     dtype=torch.float16,
            ...     device="cuda",
            ...     batch_size=2
            ... )
            >>> time_ids.shape
            torch.Size([2, 6])
        """
        # Concatenate all dimension information into a single list
        # Format: [original_height, original_width, crop_top, crop_left, target_height, target_width]
        add_time_ids = list(original_size + crops_coords_top_left + target_size)

        # Convert to tensor and set dtype and device
        add_time_ids = torch.tensor([add_time_ids], dtype=dtype, device=device)

        # Repeat for batch size (each sample gets identical time IDs)
        return add_time_ids.repeat(batch_size, 1)

    @staticmethod
    def prepare_for_cfg(
        prompt_embeds: torch.Tensor,
        negative_prompt_embeds: torch.Tensor,
        pooled_embeds: torch.Tensor,
        negative_pooled_embeds: torch.Tensor,
        time_ids: torch.Tensor,
        negative_time_ids: Optional[torch.Tensor] = None
    ) -> Dict[str, torch.Tensor]:
        """
        Concatenate tensors for classifier-free guidance (CFG).

        Classifier-free guidance requires processing both conditional (positive prompt)
        and unconditional (negative prompt) predictions in a single forward pass.
        This is achieved by concatenating the embeddings along the batch dimension,
        with negative (unconditional) embeddings first, followed by positive (conditional).

        The concatenation order is critical: [negative, positive]
        This allows the CFG formula to be applied correctly:
        noise_pred = noise_pred_uncond + guidance_scale * (noise_pred_text - noise_pred_uncond)

        Args:
            prompt_embeds: Text embeddings for the positive prompt [batch, seq_len, dim]
            negative_prompt_embeds: Text embeddings for the negative prompt [batch, seq_len, dim]
            pooled_embeds: Pooled text embeddings for positive prompt [batch, dim]
            negative_pooled_embeds: Pooled text embeddings for negative prompt [batch, dim]
            time_ids: Time embeddings for resolution conditioning (positive) [batch, 6]
            negative_time_ids: Time embeddings for negative conditioning [batch, 6] (optional, defaults to time_ids)

        Returns:
            Dict[str, torch.Tensor]: Dictionary containing concatenated tensors:
                - "prompt_embeds": [batch*2, seq_len, dim] (negative + positive)
                - "pooled_prompt_embeds": [batch*2, dim] (negative + positive)
                - "time_ids": [batch*2, 6] (negative + positive)

        Example:
            >>> cfg_tensors = SDXLConditioningBuilder.prepare_for_cfg(
            ...     prompt_embeds=positive_embeds,  # [1, 77, 2048]
            ...     negative_prompt_embeds=negative_embeds,  # [1, 77, 2048]
            ...     pooled_embeds=positive_pooled,  # [1, 1280]
            ...     negative_pooled_embeds=negative_pooled,  # [1, 1280]
            ...     time_ids=time_ids  # [1, 6]
            ... )
            >>> cfg_tensors["prompt_embeds"].shape
            torch.Size([2, 77, 2048])
        """
        # Use time_ids for negative if not provided separately
        if negative_time_ids is None:
            negative_time_ids = time_ids

        return {
            # Concatenate text embeddings: [negative, positive]
            "prompt_embeds": torch.cat([negative_prompt_embeds, prompt_embeds], dim=0),

            # Concatenate pooled embeddings: [negative, positive]
            "pooled_prompt_embeds": torch.cat([negative_pooled_embeds, pooled_embeds], dim=0),

            # Concatenate time IDs: [negative, positive]
            "time_ids": torch.cat([negative_time_ids, time_ids], dim=0),
        }

    @staticmethod
    def build_time_ids_with_validation(
        original_size: Tuple[int, int],
        crops_coords_top_left: Tuple[int, int],
        target_size: Tuple[int, int],
        dtype: torch.dtype,
        unet_config: Any,
        text_encoder_projection_dim: Optional[int] = None
    ) -> torch.Tensor:
        """
        Build SDXL time embeddings with UNet configuration validation.

        This method performs the same tensor construction as build_time_ids() but also
        validates that the embedding dimensions match the UNet's expected configuration.
        This validation ensures compatibility between the conditioning tensors and the
        model architecture.

        Args:
            original_size: Original image dimensions (height, width)
            crops_coords_top_left: Top-left coordinates (y, x) of the crop region
            target_size: Target output dimensions (height, width)
            dtype: Tensor data type (e.g., torch.float16, torch.float32)
            unet_config: UNet configuration object with addition_time_embed_dim attribute
            text_encoder_projection_dim: Text encoder projection dimension (default: None)

        Returns:
            torch.Tensor: Time embeddings tensor of shape [1, 6]

        Raises:
            ValueError: If embedding dimensions don't match UNet configuration

        Example:
            >>> time_ids = SDXLConditioningBuilder.build_time_ids_with_validation(
            ...     original_size=(1024, 1024),
            ...     crops_coords_top_left=(0, 0),
            ...     target_size=(1024, 1024),
            ...     dtype=torch.float16,
            ...     unet_config=unet.config,
            ...     text_encoder_projection_dim=1280
            ... )
        """
        # Concatenate all dimension information into a single list
        # Format: [original_height, original_width, crop_top, crop_left, target_height, target_width]
        add_time_ids = list(original_size + crops_coords_top_left + target_size)

        # Validate embedding dimensions against UNet configuration
        passed_add_embed_dim = (
            unet_config.addition_time_embed_dim * len(add_time_ids) + text_encoder_projection_dim
        )
        expected_add_embed_dim = unet_config.addition_time_embed_dim * len(add_time_ids) + 1280

        if expected_add_embed_dim != passed_add_embed_dim:
            raise ValueError(
                f"Model expects an added time embedding vector of length {expected_add_embed_dim}, "
                f"but a vector of {passed_add_embed_dim} was created. The model has an incorrect config. "
                f"Please check `unet.config.time_embedding_type` and `text_encoder_2.config.projection_dim`."
            )

        # Convert to tensor
        add_time_ids = torch.tensor([add_time_ids], dtype=dtype)
        return add_time_ids

    @staticmethod
    def prepare_initial_latents(
        batch_size: int,
        num_channels_latents: int,
        height: int,
        width: int,
        vae_scale_factor: int,
        dtype: torch.dtype,
        device: torch.device,
        generator: Optional[Union[torch.Generator, List[torch.Generator]]],
        latents: Optional[torch.FloatTensor],
        scheduler_init_noise_sigma: float
    ) -> torch.FloatTensor:
        """
        Prepare initial latent tensors for diffusion sampling.

        This method creates or validates latent tensors for the diffusion process.
        If latents are not provided, it generates random noise from a Gaussian distribution.
        The latents are then scaled by the scheduler's initial noise sigma to match the
        expected noise level at the start of the diffusion process.

        Args:
            batch_size: Number of samples to generate
            num_channels_latents: Number of latent channels (typically 4 for SDXL)
            height: Image height in pixels
            width: Image width in pixels
            vae_scale_factor: VAE downsampling factor (typically 8)
            dtype: Tensor data type (e.g., torch.float16, torch.float32)
            device: Device to create tensor on (e.g., "cuda", "cpu")
            generator: Random generator(s) for deterministic generation
            latents: Pre-generated latents (optional, if None will generate new)
            scheduler_init_noise_sigma: Scheduler's initial noise sigma value

        Returns:
            torch.FloatTensor: Prepared latent tensors scaled by init_noise_sigma
                              Shape: [batch_size, num_channels_latents, height//vae_scale_factor, width//vae_scale_factor]

        Raises:
            ValueError: If generator list length doesn't match batch_size

        Example:
            >>> latents = SDXLConditioningBuilder.prepare_initial_latents(
            ...     batch_size=1,
            ...     num_channels_latents=4,
            ...     height=1024,
            ...     width=1024,
            ...     vae_scale_factor=8,
            ...     dtype=torch.float16,
            ...     device="cuda",
            ...     generator=None,
            ...     latents=None,
            ...     scheduler_init_noise_sigma=1.0
            ... )
            >>> latents.shape
            torch.Size([1, 4, 128, 128])
        """
        # Calculate latent dimensions (downsampled by VAE)
        shape = (batch_size, num_channels_latents, height // vae_scale_factor, width // vae_scale_factor)

        # Validate generator list length matches batch size
        if isinstance(generator, list) and len(generator) != batch_size:
            raise ValueError(
                f"You have passed a list of generators of length {len(generator)}, but requested an effective batch"
                f" size of {batch_size}. Make sure the batch size matches the length of the generators."
            )

        # Generate or use provided latents
        if latents is None:
            latents = randn_tensor(shape, generator=generator, device=device, dtype=dtype)
        else:
            latents = latents.to(device)

        # Scale the initial noise by the standard deviation required by the scheduler
        latents = latents * scheduler_init_noise_sigma
        return latents
