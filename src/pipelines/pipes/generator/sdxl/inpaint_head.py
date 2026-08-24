# Derived from: Fooocus modules/inpaint_worker.py (GPL-3.0)
"""
Fooocus InpaintHead Model

This module implements the InpaintHead model from Fooocus, which is a small convolutional
neural network that helps the UNet understand masked regions during inpainting.

Based on: https://github.com/lllyasviel/Fooocus/blob/main/modules/inpaint_worker.py (InpaintHead)
"""

import torch
import os
from pathlib import Path
from typing import Optional, Union
from diffusers.utils import logging

logger = logging.get_logger(__name__)

# The asset this module loads, as the depot-relative coordinates the ASSETS
# service takes. `generator/sdxl` fetches it with these; the k-diffusion
# pipeline derives the load path from the same two, so the fetch destination
# and the load path cannot drift apart.
INPAINT_HEAD_URL = "https://huggingface.co/lllyasviel/fooocus_inpaint/resolve/main/fooocus_inpaint_head.pth"
INPAINT_HEAD_SUBDIR = "inpaint"
INPAINT_HEAD_FILENAME = "fooocus_inpaint_head.pth"


def inpaint_head_path(models_dir: Union[str, Path]) -> Path:
    """Where the inpaint head lives under the configured model depot."""
    return (Path(models_dir) / INPAINT_HEAD_SUBDIR / INPAINT_HEAD_FILENAME).resolve()


class InpaintHead(torch.nn.Module):
    """
    Small convolutional head for inpainting that processes mask + latent information.

    Architecture:
    - Input: 5 channels (1 mask channel + 4 VAE latent channels)
    - Output: 320 channels (matches UNet's first input block channel count)
    - Kernel: 3x3 convolution with replicate padding

    This head is injected into the UNet's first input block to provide explicit
    mask and image information, improving inpainting quality especially at mask boundaries.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Convolutional weights: (out_channels=320, in_channels=5, kernel_h=3, kernel_w=3)
        self.head = torch.nn.Parameter(torch.empty(size=(320, 5, 3, 3), device='cpu'))

    def __call__(self, x):
        """
        Apply the inpaint head convolution.

        Args:
            x: Input tensor of shape (batch, 5, height, width)
               - 1 channel: mask (1=inpaint, 0=keep)
               - 4 channels: VAE-encoded init_latent

        Returns:
            Feature tensor of shape (batch, 320, height, width)
        """
        # Apply replicate padding (1 pixel on each side for 3x3 kernel)
        x = torch.nn.functional.pad(x, (1, 1, 1, 1), "replicate")
        # Apply convolution
        return torch.nn.functional.conv2d(input=x, weight=self.head)


class InpaintHeadLoader:
    """Helper class for loading and managing the InpaintHead model."""

    # Global singleton instance to avoid reloading
    _instance: Optional[InpaintHead] = None
    _loaded_path: Optional[str] = None

    @classmethod
    def load_inpaint_head(cls, model_path: str) -> InpaintHead:
        """
        Load the InpaintHead model from a checkpoint file.

        Loads only. The fetch is the caller's pre-flight: `generator/sdxl`
        ensures this file exists through the injected ASSETS service before it
        starts a masked generation, so the weights arrive through the download
        manager with history, containment and progress like every other model.
        A pipe cannot fetch anything here - `src/pipelines/` may not import
        `src.features.downloads` (`tests/architecture/test_layering.py`) - and
        this call sits deep inside `SDXLModelWrapper` construction, far from
        anywhere a service is in scope.

        Args:
            model_path: Path to the fooocus_inpaint_head.pth file

        Returns:
            Loaded InpaintHead model instance

        Raises:
            FileNotFoundError: If the model file doesn't exist
            RuntimeError: If model loading fails
        """
        # Return cached instance if same model is already loaded
        if cls._instance is not None and cls._loaded_path == model_path:
            logger.debug(f"[INPAINT HEAD] Using cached model from {model_path}")
            return cls._instance

        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"InpaintHead model not found at {model_path}. It should have been "
                f"fetched before generation started; download it from "
                f"{INPAINT_HEAD_URL} if you are loading this outside a pipeline."
            )

        try:
            # Create model instance
            model = InpaintHead()

            # Load state dict from checkpoint
            logger.info(f"[INPAINT HEAD] Loading model from {model_path}")
            state_dict = torch.load(model_path, map_location='cpu', weights_only=True)
            model.load_state_dict(state_dict)

            # Cache the loaded model
            cls._instance = model
            cls._loaded_path = model_path

            logger.info(f"[INPAINT HEAD] Model loaded successfully. Shape: {model.head.shape}")
            return model

        except Exception as e:
            logger.error(f"[INPAINT HEAD] Failed to load model from {model_path}: {e}")
            raise RuntimeError(f"Failed to load InpaintHead model: {e}")

    @classmethod
    def clear_cache(cls):
        """Clear the cached model instance."""
        cls._instance = None
        cls._loaded_path = None
        logger.info("[INPAINT HEAD] Cleared cached model")


def prepare_inpaint_head_input(
    mask_latents: torch.Tensor,
    init_latents: torch.Tensor,
    vae_encoder = None
) -> torch.Tensor:
    """
    Prepare 5-channel input for InpaintHead from mask and init image.

    Args:
        mask_latents: Mask tensor in latent space, shape (batch, 1, h, w)
                     Values: 1.0 = inpaint, 0.0 = keep original
        init_latents: Encoded init image latents, shape (batch, 4, h, w)
        vae_encoder: Optional VAE encoder (not used, for future compatibility)

    Returns:
        5-channel tensor ready for InpaintHead, shape (batch, 5, h, w)
    """
    # Ensure mask has correct shape
    if mask_latents.ndim == 3:
        mask_latents = mask_latents.unsqueeze(1)  # Add channel dimension
    elif mask_latents.shape[1] != 1:
        mask_latents = mask_latents[:, :1, :, :]  # Take first channel only

    # Concatenate mask (1 channel) + init_latents (4 channels) = 5 channels
    inpaint_input = torch.cat([mask_latents, init_latents], dim=1)

    return inpaint_input
