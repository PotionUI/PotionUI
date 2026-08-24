"""Tests for src.platform.runtime.tensors"""

import numpy as np
import pytest
import torch
from PIL import Image

from src.platform.runtime.tensors import (
    ensure_mask_shape,
    numpy_to_pil,
    numpy_to_torch,
    pil_to_numpy_gray,
    pil_to_numpy_rgb,
    pil_to_torch,
    torch_to_numpy,
    torch_to_pil,
)


# ---- pil_to_numpy_rgb ----

class TestPilToNumpyRgb:
    def test_rgb_image(self):
        img = Image.new("RGB", (16, 8), color=(10, 20, 30))
        arr = pil_to_numpy_rgb(img)
        assert arr.shape == (8, 16, 3)
        assert arr.dtype == np.uint8
        assert tuple(arr[0, 0]) == (10, 20, 30)

    def test_rgba_image_converted(self):
        img = Image.new("RGBA", (4, 4), color=(100, 150, 200, 255))
        arr = pil_to_numpy_rgb(img)
        assert arr.shape == (4, 4, 3)
        assert tuple(arr[0, 0]) == (100, 150, 200)

    def test_grayscale_image_converted(self):
        img = Image.new("L", (4, 4), color=128)
        arr = pil_to_numpy_rgb(img)
        assert arr.shape == (4, 4, 3)
        assert tuple(arr[0, 0]) == (128, 128, 128)


# ---- pil_to_numpy_gray ----

class TestPilToNumpyGray:
    def test_grayscale_image(self):
        img = Image.new("L", (4, 4), color=200)
        arr = pil_to_numpy_gray(img)
        assert arr.shape == (4, 4)
        assert arr.dtype == np.uint8
        assert arr[0, 0] == 200

    def test_rgb_image_converted(self):
        img = Image.new("RGB", (4, 4), color=(100, 100, 100))
        arr = pil_to_numpy_gray(img)
        assert arr.shape == (4, 4)


# ---- numpy_to_pil ----

class TestNumpyToPil:
    def test_rgb_array(self):
        arr = np.zeros((8, 16, 3), dtype=np.uint8)
        arr[0, 0] = [10, 20, 30]
        img = numpy_to_pil(arr)
        assert img.size == (16, 8)
        assert img.mode == "RGB"
        assert img.getpixel((0, 0)) == (10, 20, 30)

    def test_grayscale_array(self):
        arr = np.full((4, 4), 128, dtype=np.uint8)
        img = numpy_to_pil(arr)
        assert img.mode == "L"
        assert img.getpixel((0, 0)) == 128


# ---- numpy_to_torch ----

class TestNumpyToTorch:
    def test_basic_conversion(self):
        arr = np.full((8, 16, 3), 127, dtype=np.uint8)
        t = numpy_to_torch(arr)
        assert t.shape == (1, 3, 8, 16)
        assert t.dtype == torch.float32
        assert torch.allclose(t, torch.tensor(127.0 / 255.0), atol=1e-5)

    def test_zero_and_max(self):
        arr = np.zeros((4, 4, 3), dtype=np.uint8)
        t = numpy_to_torch(arr)
        assert t.min().item() == 0.0

        arr_max = np.full((4, 4, 3), 255, dtype=np.uint8)
        t_max = numpy_to_torch(arr_max)
        assert t_max.max().item() == pytest.approx(1.0)


# ---- torch_to_numpy ----

class TestTorchToNumpy:
    def test_basic_conversion(self):
        t = torch.full((1, 3, 8, 16), 0.5)
        arr = torch_to_numpy(t)
        assert arr.shape == (8, 16, 3)
        assert arr.dtype == np.uint8
        assert arr[0, 0, 0] == 127  # floor(0.5 * 255) = 127

    def test_clamping(self):
        t = torch.tensor([[[[2.0]], [[-1.0]], [[0.5]]]])  # shape (1,3,1,1)
        arr = torch_to_numpy(t)
        assert arr[0, 0, 0] == 255  # clamped from 2.0
        assert arr[0, 0, 1] == 0    # clamped from -1.0
        assert arr[0, 0, 2] == 127  # floor(0.5*255) = 127

    def test_roundtrip_with_numpy_to_torch(self):
        original = np.random.randint(0, 256, (32, 32, 3), dtype=np.uint8)
        t = numpy_to_torch(original)
        recovered = torch_to_numpy(t)
        assert np.allclose(original, recovered, atol=1)


# ---- pil_to_torch ----

class TestPilToTorch:
    def test_normalized(self):
        img = Image.new("RGB", (8, 8), color=(255, 0, 128))
        t = pil_to_torch(img, normalize_range=True)
        assert t.shape == (1, 3, 8, 8)
        assert t.max().item() == pytest.approx(1.0)
        assert t.min().item() == pytest.approx(0.0)

    def test_unnormalized(self):
        img = Image.new("RGB", (8, 8), color=(200, 100, 50))
        t = pil_to_torch(img, normalize_range=False)
        assert t.shape == (1, 3, 8, 8)
        assert t[0, 0, 0, 0].item() == pytest.approx(200.0)

    def test_device_and_dtype(self):
        img = Image.new("RGB", (8, 8))
        t = pil_to_torch(img, dtype=torch.float16)
        assert t.dtype == torch.float16

    def test_rgba_handled(self):
        img = Image.new("RGBA", (8, 8))
        t = pil_to_torch(img)
        assert t.shape[1] == 3  # converted to RGB


# ---- torch_to_pil ----

class TestTorchToPil:
    def test_basic(self):
        t = torch.zeros(1, 3, 16, 8)
        img = torch_to_pil(t)
        assert img.size == (8, 16)
        assert img.mode == "RGB"

    def test_roundtrip(self):
        original = Image.new("RGB", (32, 32), color=(100, 150, 200))
        t = pil_to_torch(original)
        recovered = torch_to_pil(t)
        orig_arr = np.array(original)
        rec_arr = np.array(recovered)
        assert np.allclose(orig_arr, rec_arr, atol=1)


# ---- ensure_mask_shape ----

class TestEnsureMaskShape:
    def test_expand_2d_to_4d(self):
        mask = torch.ones(8, 8)
        result = ensure_mask_shape(mask, 4)
        assert result.shape == (1, 1, 8, 8)

    def test_expand_3d_to_4d(self):
        mask = torch.ones(1, 8, 8)
        result = ensure_mask_shape(mask, 4)
        assert result.shape == (1, 1, 8, 8)

    def test_squeeze_4d_to_2d(self):
        mask = torch.ones(1, 1, 8, 8)
        result = ensure_mask_shape(mask, 2)
        assert result.shape == (8, 8)

    def test_no_change_when_matching(self):
        mask = torch.ones(1, 1, 8, 8)
        result = ensure_mask_shape(mask, 4)
        assert result.shape == (1, 1, 8, 8)

    def test_invalid_target_ndim(self):
        mask = torch.ones(8, 8)
        with pytest.raises(ValueError, match="target_ndim must be 2, 3, or 4"):
            ensure_mask_shape(mask, 5)
