import random

from PIL import Image
from PIL.Image import Resampling


def latents_to_rgb(latents, width=None, height=None):
    import torch

    with torch.no_grad():
        weights = (
            (60, -60, 25, -70),
            (60, -5, 15, -50),
            (60, 10, -5, -35),
        )

        weights_tensor = torch.t(torch.tensor(weights, dtype=latents.dtype).to(latents.device))
        biases_tensor = torch.tensor((150, 140, 130), dtype=latents.dtype).to(latents.device)
        rgb_tensor = torch.einsum("...lxy,lr -> ...rxy", latents, weights_tensor) + biases_tensor.unsqueeze(
            -1
        ).unsqueeze(-1)
        image_array = rgb_tensor.clamp(0, 255).byte().cpu().numpy().transpose(1, 2, 0)
        image = Image.fromarray(image_array)

    if width is not None and height is not None:
        image = image.resize((width, height), Resampling.LANCZOS)

    return image


def generate_seed(seed: int = -1) -> int:
    """Pick a random 32-bit seed, or pass an explicit one through unchanged.

    Uses Python's own RNG rather than torch's: this only needs an arbitrary
    starting seed value, not a draw from torch's global generator state, and
    doing it in stdlib keeps ``import torch`` out of every module that just
    wants a seed (e.g. prompt expansion) instead of real tensor ops.
    """
    if seed == -1:
        return random.randint(0, 2 ** 32 - 1)

    return seed
