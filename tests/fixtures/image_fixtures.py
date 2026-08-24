"""
Test fixtures for image-related data.

Provides fixtures for creating fake images, image bytes,
and other image-related test data.
"""

import pytest
import io
from PIL import Image


@pytest.fixture
def fake_image() -> Image.Image:
    """
    Create a fake PIL Image for testing.

    Generates a simple 512x512 RGB image with a gradient pattern.
    This is useful for testing image processing and saving operations
    without requiring actual model-generated images.

    Returns:
        Image.Image: 512x512 PIL Image instance
    """
    # Create a simple gradient image
    img = Image.new('RGB', (512, 512))
    pixels = img.load()

    for y in range(512):
        for x in range(512):
            # Create a simple diagonal gradient
            r = int((x / 512) * 255)
            g = int((y / 512) * 255)
            b = 128
            pixels[x, y] = (r, g, b)

    return img


@pytest.fixture
def fake_image_bytes(fake_image) -> bytes:
    """
    Create fake image bytes in PNG format.

    Converts a fake PIL Image to PNG bytes for testing
    file uploads and binary image handling.

    Args:
        fake_image: Fake PIL Image fixture

    Returns:
        bytes: PNG-encoded image bytes
    """
    buffer = io.BytesIO()
    fake_image.save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.fixture
def fake_image_1024() -> Image.Image:
    """
    Create a larger fake PIL Image (1024x1024) for testing.

    Returns:
        Image.Image: 1024x1024 PIL Image instance
    """
    img = Image.new('RGB', (1024, 1024))
    pixels = img.load()

    for y in range(1024):
        for x in range(1024):
            # Create a radial gradient
            center_x, center_y = 512, 512
            distance = ((x - center_x) ** 2 + (y - center_y) ** 2) ** 0.5
            max_distance = (512 ** 2 + 512 ** 2) ** 0.5

            intensity = int((1 - distance / max_distance) * 255)
            pixels[x, y] = (intensity, intensity, intensity)

    return img


@pytest.fixture
def fake_small_image() -> Image.Image:
    """
    Create a small fake PIL Image (256x256) for testing.

    Useful for tests that need quick image operations or minimal
    memory usage.

    Returns:
        Image.Image: 256x256 PIL Image instance
    """
    img = Image.new('RGB', (256, 256), color='red')
    return img


@pytest.fixture
def fake_image_rgba() -> Image.Image:
    """
    Create a fake PIL Image with alpha channel (RGBA).

    Returns:
        Image.Image: 512x512 RGBA PIL Image instance
    """
    img = Image.new('RGBA', (512, 512))
    pixels = img.load()

    for y in range(512):
        for x in range(512):
            r = int((x / 512) * 255)
            g = int((y / 512) * 255)
            b = 128
            # Create alpha gradient
            a = int(((x + y) / 1024) * 255)
            pixels[x, y] = (r, g, b, a)

    return img


@pytest.fixture
def fake_image_batch(fake_image, fake_image_1024, fake_small_image) -> list[Image.Image]:
    """
    Create a batch of fake images with different sizes.

    Args:
        fake_image: 512x512 image fixture
        fake_image_1024: 1024x1024 image fixture
        fake_small_image: 256x256 image fixture

    Returns:
        list[Image.Image]: List of PIL Images with varying sizes
    """
    return [fake_small_image, fake_image, fake_image_1024]


@pytest.fixture
def fake_latent() -> dict:
    """
    Create fake latent representation for testing.

    Simulates a latent tensor structure used in diffusion models.

    Returns:
        dict: Fake latent dictionary with shape info
    """
    import random

    # Simulate latent tensor data (typically 4 channels, 64x64 for 512x512 image)
    latent_data = [
        [[random.random() for _ in range(64)] for _ in range(64)]
        for _ in range(4)
    ]

    return {
        'samples': latent_data,
        'shape': [1, 4, 64, 64],
        'dtype': 'float32'
    }
