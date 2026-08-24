"""
SDXL Input Validator

Validates and preprocesses inputs for SDXL generation pipeline.

This module handles:
- Resolution validation (divisible by 8)
- Image preprocessing (PIL → tensor conversion, normalization)
- Mask preprocessing (grayscale conversion, latent space resizing)
- Conditioning validation (required attributes check)

All validation and preprocessing logic extracted from diffusers pipeline
to ensure generator pipe controls input quality before pipeline execution.
"""

from typing import Tuple

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from src.platform.util.dimensions import validate_resolution as _validate_resolution


class SDXLInputValidator:
    """Validates and preprocesses inputs for SDXL generation"""

    @staticmethod
    def validate_resolution(width: int, height: int) -> None:
        """
        Ensure resolution is valid for SDXL (divisible by 8).

        SDXL operates on latent space with 8x downsampling, so input
        dimensions must be divisible by 8 to avoid dimension mismatches.

        Args:
            width: Image width in pixels
            height: Image height in pixels

        Raises:
            ValueError: If width or height is not divisible by 8

        Examples:
            >>> SDXLInputValidator.validate_resolution(1024, 1024)  # Valid
            >>> SDXLInputValidator.validate_resolution(1023, 1024)  # Raises ValueError
        """
        try:
            _validate_resolution(width, height)
        except ValueError:
            raise ValueError(
                f"Resolution must be divisible by 8 for SDXL, got {width}x{height}. "
                f"Use dimensions like 512, 768, 1024, 1536, 2048, etc."
            )

    @staticmethod
    def preprocess_image(
        image: Image.Image,
        target_device: torch.device,
        target_dtype: torch.dtype
    ) -> torch.Tensor:
        """
        Convert PIL image to tensor for SDXL pipeline.

        Preprocessing steps:
        1. Convert to RGB mode if needed (handles RGBA, L, etc.)
        2. Normalize pixel values from [0, 255] to [-1, 1] range
        3. Convert numpy array to torch tensor
        4. Rearrange dimensions from HWC to CHW format
        5. Add batch dimension (NCHW format)
        6. Move to target device and dtype

        Args:
            image: PIL Image to preprocess
            target_device: Target device (cuda/cpu) for tensor
            target_dtype: Target dtype (float16/float32/bfloat16) for tensor

        Returns:
            Preprocessed image tensor with shape (1, C, H, W) in range [-1, 1]

        Examples:
            >>> from PIL import Image
            >>> img = Image.new("RGB", (1024, 1024))
            >>> tensor = SDXLInputValidator.preprocess_image(
            ...     img, torch.device("cuda"), torch.float16
            ... )
            >>> tensor.shape
            torch.Size([1, 3, 1024, 1024])
            >>> tensor.min(), tensor.max()
            (tensor(-1.), tensor(1.))
        """
        # Convert to RGB if needed (handles RGBA, L, P, etc.)
        if image.mode != "RGB":
            image = image.convert("RGB")

        # Normalize to [-1, 1] range (diffusers standard)
        # Formula: (pixel / 127.5) - 1.0
        # Maps [0, 255] → [0, 2] → [-1, 1]
        image_array = np.array(image).astype(np.float32) / 127.5 - 1.0

        # Convert to torch tensor and rearrange dimensions
        # NumPy: (H, W, C) → PyTorch: (C, H, W)
        image_tensor = torch.from_numpy(image_array).permute(2, 0, 1).unsqueeze(0)

        # Move to target device and dtype
        return image_tensor.to(device=target_device, dtype=target_dtype)

    @staticmethod
    def preprocess_mask(
        mask: Image.Image,
        latent_shape: Tuple[int, int, int, int],
        target_device: torch.device,
        target_dtype: torch.dtype
    ) -> torch.Tensor:
        """
        Convert mask to tensor and resize to latent dimensions.

        SDXL VAE downsamples images by 8x to latent space. Masks must be
        resized to match latent dimensions (H/8, W/8) for proper masking.

        Preprocessing steps:
        1. Convert to grayscale (L mode) if needed
        2. Normalize pixel values from [0, 255] to [0, 1] range
        3. Convert to torch tensor with shape (1, 1, H, W)
        4. Resize to latent dimensions using nearest-neighbor interpolation
        5. Move to target device and dtype

        Args:
            mask: PIL Image mask (white = keep, black = regenerate)
            latent_shape: Shape of latent tensor (B, C, H/8, W/8)
            target_device: Target device (cuda/cpu) for tensor
            target_dtype: Target dtype (float16/float32/bfloat16) for tensor

        Returns:
            Preprocessed mask tensor with shape (1, 1, H/8, W/8) in range [0, 1]

        Notes:
            - Uses nearest-neighbor interpolation to preserve binary mask edges
            - Mask values: 1.0 = keep original, 0.0 = regenerate
            - Latent shape format: (batch, channels, height/8, width/8)

        Examples:
            >>> from PIL import Image
            >>> mask = Image.new("L", (1024, 1024), 255)
            >>> latent_shape = (1, 4, 128, 128)  # 1024/8 = 128
            >>> mask_tensor = SDXLInputValidator.preprocess_mask(
            ...     mask, latent_shape, torch.device("cuda"), torch.float16
            ... )
            >>> mask_tensor.shape
            torch.Size([1, 1, 128, 128])
            >>> mask_tensor.min(), mask_tensor.max()
            (tensor(0.), tensor(1.))
        """
        # Convert to grayscale if needed (handles RGB, RGBA, etc.)
        if mask.mode != "L":
            mask = mask.convert("L")

        # Normalize to [0, 1] range
        # Formula: pixel / 255.0
        # Maps [0, 255] → [0, 1]
        mask_array = np.array(mask).astype(np.float32) / 255.0

        # Convert to torch tensor with batch and channel dimensions
        # Shape: (H, W) → (1, 1, H, W)
        mask_tensor = torch.from_numpy(mask_array).unsqueeze(0).unsqueeze(0)

        # Resize to latent dimensions (H/8, W/8)
        # Use nearest interpolation to preserve mask edges
        # latent_shape[2:] extracts (H/8, W/8) from (B, C, H/8, W/8)
        mask_tensor = F.interpolate(
            mask_tensor,
            size=latent_shape[2:],
            mode="nearest"
        )

        # Move to target device and dtype
        return mask_tensor.to(device=target_device, dtype=target_dtype)

    @staticmethod
    def validate_conditioning(conditioning) -> None:
        """
        Ensure conditioning tensors have correct structure.

        SDXL requires role-keyed embeds/n_embeds dicts, each carrying:
        - "embeds": prompt text embeddings (CLIP output)
        - "pooled": pooled embeddings (CLIP pooler output)

        These are computed by the prompt encoder pipe and must be present
        for SDXL generation to work correctly.

        Args:
            conditioning: Conditioning object with `embeds`/`n_embeds` dict attributes

        Raises:
            ValueError: If `embeds`/`n_embeds` or any required role is missing

        Notes:
            - SDXL uses dual text encoders (CLIP ViT-L and OpenCLIP ViT-bigG)
            - Pooled embeddings are used for time embedding conditioning
            - All embeddings must be torch tensors on the correct device

        Examples:
            >>> class Conditioning:
            ...     def __init__(self):
            ...         self.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
            ...         self.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
            >>>
            >>> cond = Conditioning()
            >>> SDXLInputValidator.validate_conditioning(cond)  # Valid
            >>>
            >>> class BadConditioning:
            ...     pass
            >>>
            >>> bad_cond = BadConditioning()
            >>> SDXLInputValidator.validate_conditioning(bad_cond)  # Raises ValueError
        """
        required_roles = ["embeds", "pooled"]

        missing_attrs = []
        for side_attr in ("embeds", "n_embeds"):
            side = getattr(conditioning, side_attr, None)
            if side is None:
                missing_attrs.append(side_attr)
                continue
            for role in required_roles:
                if role not in side:
                    missing_attrs.append(f"{side_attr}.{role}")

        if missing_attrs:
            raise ValueError(
                f"Conditioning missing required attributes: {', '.join(missing_attrs)}. "
                f"Ensure prompt encoder pipe ran successfully and produced all embeddings."
            )

    @staticmethod
    def validate_pipeline_inputs(
        callback_tensor_inputs,
        prompt,
        prompt_2,
        height,
        width,
        callback_steps,
        negative_prompt=None,
        negative_prompt_2=None,
        prompt_embeds=None,
        negative_prompt_embeds=None,
        pooled_prompt_embeds=None,
        negative_pooled_prompt_embeds=None,
        callback_on_step_end_tensor_inputs=None,
    ) -> None:
        """
        Validate all pipeline inputs (extracted from diffusers check_inputs method).

        This method performs comprehensive validation of all inputs to the SDXL
        K-diffusion pipeline, ensuring they meet the requirements before generation.

        Validation checks:
        1. Height and width are divisible by 8 (VAE requirement)
        2. Callback steps is a positive integer if provided
        3. Callback tensor inputs are valid if provided
        4. Prompt/prompt_embeds parameters are mutually exclusive
        5. Negative prompt parameters are correctly paired
        6. Embedding shapes match when both positive and negative are provided
        7. Pooled embeddings are provided when embeddings are provided

        Args:
            callback_tensor_inputs: Valid callback tensor names from pipeline
            prompt: Primary text prompt or list of prompts
            prompt_2: Secondary text prompt for SDXL's second text encoder
            height: Output image height in pixels
            width: Output image width in pixels
            callback_steps: Number of steps between callbacks
            negative_prompt: Negative prompt(s) for guidance
            negative_prompt_2: Secondary negative prompt for second encoder
            prompt_embeds: Precomputed prompt embeddings
            negative_prompt_embeds: Precomputed negative prompt embeddings
            pooled_prompt_embeds: Precomputed pooled prompt embeddings
            negative_pooled_prompt_embeds: Precomputed negative pooled embeddings
            callback_on_step_end_tensor_inputs: List of tensors to include in callbacks

        Raises:
            ValueError: If any validation check fails

        Examples:
            >>> # Valid basic inputs
            >>> SDXLInputValidator.validate_pipeline_inputs(
            ...     callback_tensor_inputs=["latents"],
            ...     prompt="a cat",
            ...     prompt_2="a cat",
            ...     height=1024,
            ...     width=1024,
            ...     callback_steps=10
            ... )
            >>>
            >>> # Invalid: height not divisible by 8
            >>> SDXLInputValidator.validate_pipeline_inputs(
            ...     callback_tensor_inputs=["latents"],
            ...     prompt="a cat",
            ...     prompt_2="a cat",
            ...     height=1023,  # Not divisible by 8!
            ...     width=1024,
            ...     callback_steps=10
            ... )  # Raises ValueError
        """
        try:
            _validate_resolution(width, height)
        except ValueError:
            raise ValueError(f"`height` and `width` have to be divisible by 8 but are {height} and {width}.")

        if callback_steps is not None and (not isinstance(callback_steps, int) or callback_steps <= 0):
            raise ValueError(
                f"`callback_steps` has to be a positive integer but is {callback_steps} of type"
                f" {type(callback_steps)}."
            )

        if callback_on_step_end_tensor_inputs is not None and not all(
            k in callback_tensor_inputs for k in callback_on_step_end_tensor_inputs
        ):
            raise ValueError(
                f"`callback_on_step_end_tensor_inputs` has to be in {callback_tensor_inputs}, but found {[k for k in callback_on_step_end_tensor_inputs if k not in callback_tensor_inputs]}"
            )

        if prompt is not None and prompt_embeds is not None:
            raise ValueError(
                f"Cannot forward both `prompt`: {prompt} and `prompt_embeds`: {prompt_embeds}. Please make sure to"
                " only forward one of the two."
            )
        elif prompt_2 is not None and prompt_embeds is not None:
            raise ValueError(
                f"Cannot forward both `prompt_2`: {prompt_2} and `prompt_embeds`: {prompt_embeds}. Please make sure to"
                " only forward one of the two."
            )
        elif prompt is None and prompt_embeds is None:
            raise ValueError(
                "Provide either `prompt` or `prompt_embeds`. Cannot leave both `prompt` and `prompt_embeds` undefined."
            )
        elif prompt is not None and (not isinstance(prompt, str) and not isinstance(prompt, list)):
            raise ValueError(f"`prompt` has to be of type `str` or `list` but is {type(prompt)}")
        elif prompt_2 is not None and (not isinstance(prompt_2, str) and not isinstance(prompt_2, list)):
            raise ValueError(f"`prompt_2` has to be of type `str` or `list` but is {type(prompt_2)}")

        if negative_prompt is not None and negative_prompt_embeds is not None:
            raise ValueError(
                f"Cannot forward both `negative_prompt`: {negative_prompt} and `negative_prompt_embeds`:"
                f" {negative_prompt_embeds}. Please make sure to only forward one of the two."
            )
        elif negative_prompt_2 is not None and negative_prompt_embeds is not None:
            raise ValueError(
                f"Cannot forward both `negative_prompt_2`: {negative_prompt_2} and `negative_prompt_embeds`:"
                f" {negative_prompt_embeds}. Please make sure to only forward one of the two."
            )

        if prompt_embeds is not None and negative_prompt_embeds is not None:
            if prompt_embeds.shape != negative_prompt_embeds.shape:
                raise ValueError(
                    "`prompt_embeds` and `negative_prompt_embeds` must have the same shape when passed directly, but"
                    f" got: `prompt_embeds` {prompt_embeds.shape} != `negative_prompt_embeds`"
                    f" {negative_prompt_embeds.shape}."
                )

        if prompt_embeds is not None and pooled_prompt_embeds is None:
            raise ValueError(
                "If `prompt_embeds` are provided, `pooled_prompt_embeds` also have to be passed. Make sure to generate `pooled_prompt_embeds` from the same text encoder that was used to generate `prompt_embeds`."
            )

        if negative_prompt_embeds is not None and negative_pooled_prompt_embeds is None:
            raise ValueError(
                "If `negative_prompt_embeds` are provided, `negative_pooled_prompt_embeds` also have to be passed. Make sure to generate `negative_pooled_prompt_embeds` from the same text encoder that was used to generate `negative_prompt_embeds`."
            )
