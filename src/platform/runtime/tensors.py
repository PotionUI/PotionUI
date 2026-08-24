"""
Tensor Utilities - Centralized PIL/numpy/torch conversions.

Provides consistent conversion functions between PIL Images, numpy arrays,
and torch tensors used across multiple pipes in the generation pipeline.
"""

from typing import Optional

import numpy as np
import torch
from PIL import Image


def pil_to_numpy_rgb(image: Image.Image) -> np.ndarray:
    """
    Convert PIL Image to RGB numpy array.

    Args:
        image: PIL Image in any mode (RGB, RGBA, L, etc.)

    Returns:
        numpy array with shape (H, W, 3) and dtype uint8
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    return np.array(image)


def pil_to_numpy_gray(image: Image.Image) -> np.ndarray:
    """
    Convert PIL Image to grayscale numpy array.

    Args:
        image: PIL Image in any mode

    Returns:
        numpy array with shape (H, W) and dtype uint8
    """
    if image.mode != "L":
        image = image.convert("L")
    return np.array(image)


def numpy_to_pil(array: np.ndarray) -> Image.Image:
    """
    Convert numpy array to PIL Image.

    Args:
        array: numpy array with shape (H, W, C) or (H, W) and dtype uint8

    Returns:
        PIL Image
    """
    return Image.fromarray(array)


def numpy_to_torch(image: np.ndarray) -> torch.Tensor:
    """
    Convert numpy image (H, W, C) uint8 to torch tensor (1, C, H, W) float [0, 1].

    Args:
        image: Numpy array in range [0, 255]

    Returns:
        Torch tensor in range [0, 1] with shape (1, C, H, W)
    """
    image_float = image.astype(np.float32) / 255.0
    image_torch = torch.from_numpy(image_float)
    image_torch = image_torch.permute(2, 0, 1)  # HWC -> CHW
    image_torch = image_torch.unsqueeze(0)  # Add batch dimension
    return image_torch


def torch_to_numpy(tensor: torch.Tensor) -> np.ndarray:
    """
    Convert torch tensor (B, C, H, W) to numpy image (H, W, C) uint8.

    Takes the first element in the batch dimension.

    Args:
        tensor: Torch tensor in range [0, 1] with shape (B, C, H, W)

    Returns:
        Numpy array in range [0, 255] with shape (H, W, C) and dtype uint8
    """
    image = tensor[0].permute(1, 2, 0)  # CHW -> HWC
    image = (image * 255).clamp(0, 255).cpu().numpy().astype(np.uint8)
    return image


def pil_to_torch(
    image: Image.Image,
    device: Optional[torch.device] = None,
    dtype: Optional[torch.dtype] = None,
    normalize_range: bool = True,
) -> torch.Tensor:
    """
    Convert PIL Image to torch tensor.

    Args:
        image: PIL Image in any mode
        device: Target device (e.g. torch.device("cuda")). None keeps on CPU.
        dtype: Target dtype (e.g. torch.float16). None keeps as float32.
        normalize_range: If True, normalize to [0, 1]. If False, keep [0, 255].

    Returns:
        Torch tensor with shape (1, C, H, W)
    """
    array = pil_to_numpy_rgb(image)
    if normalize_range:
        tensor = numpy_to_torch(array)
    else:
        tensor = torch.from_numpy(array.astype(np.float32))
        tensor = tensor.permute(2, 0, 1).unsqueeze(0)

    if device is not None:
        tensor = tensor.to(device=device)
    if dtype is not None:
        tensor = tensor.to(dtype=dtype)
    return tensor


def torch_to_pil(tensor: torch.Tensor) -> Image.Image:
    """
    Convert torch tensor to PIL Image.

    Args:
        tensor: Torch tensor with shape (B, C, H, W) in range [0, 1]

    Returns:
        PIL Image
    """
    array = torch_to_numpy(tensor)
    return numpy_to_pil(array)


def ensure_mask_shape(mask: torch.Tensor, target_ndim: int) -> torch.Tensor:
    """
    Normalize mask tensor to the requested number of dimensions.

    Adds or removes leading dimensions as needed to match target_ndim.

    Args:
        mask: Mask tensor of any dimensionality
        target_ndim: Desired number of dimensions (2, 3, or 4)

    Returns:
        Mask tensor with exactly target_ndim dimensions

    Raises:
        ValueError: If target_ndim is not 2, 3, or 4
    """
    if target_ndim not in (2, 3, 4):
        raise ValueError(f"target_ndim must be 2, 3, or 4, got {target_ndim}")

    while mask.ndim < target_ndim:
        mask = mask.unsqueeze(0)
    while mask.ndim > target_ndim:
        mask = mask.squeeze(0)

    return mask
