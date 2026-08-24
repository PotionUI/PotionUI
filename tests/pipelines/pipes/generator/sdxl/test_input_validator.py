"""
Tests for SDXL Input Validator

Tests validation and preprocessing logic for SDXL generation inputs.
"""

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.pipes.generator.sdxl.input_validator import SDXLInputValidator


class TestResolutionValidation:
    """Tests for resolution validation"""

    def test_valid_resolutions(self):
        """Test that valid resolutions (divisible by 8) pass validation"""
        valid_resolutions = [
            (512, 512),
            (768, 768),
            (1024, 1024),
            (1024, 768),
            (768, 1024),
            (1536, 1024),
            (2048, 2048),
            (512, 768),
            (1280, 720),  # 720p is divisible by 8
        ]

        for width, height in valid_resolutions:
            # Should not raise any exception
            SDXLInputValidator.validate_resolution(width, height)

    def test_invalid_resolutions(self):
        """Test that invalid resolutions (not divisible by 8) raise ValueError"""
        invalid_resolutions = [
            (513, 512),  # Width not divisible by 8
            (512, 513),  # Height not divisible by 8
            (1023, 1024),  # Width not divisible by 8
            (1024, 1023),  # Height not divisible by 8
            (1001, 1001),  # Neither divisible by 8
            (100, 100),  # Small but not divisible by 8
        ]

        for width, height in invalid_resolutions:
            with pytest.raises(ValueError, match="Resolution must be divisible by 8"):
                SDXLInputValidator.validate_resolution(width, height)

    def test_error_message_includes_dimensions(self):
        """Test that error message includes the invalid dimensions"""
        with pytest.raises(ValueError, match="got 1023x1024"):
            SDXLInputValidator.validate_resolution(1023, 1024)


class TestImagePreprocessing:
    """Tests for image preprocessing"""

    @pytest.fixture
    def rgb_image(self):
        """Create a simple RGB test image"""
        return Image.new("RGB", (512, 512), color=(128, 128, 128))

    @pytest.fixture
    def rgba_image(self):
        """Create an RGBA test image"""
        return Image.new("RGBA", (512, 512), color=(128, 128, 128, 255))

    @pytest.fixture
    def grayscale_image(self):
        """Create a grayscale test image"""
        return Image.new("L", (512, 512), color=128)

    def test_preprocess_rgb_image(self, rgb_image):
        """Test preprocessing of RGB image"""
        tensor = SDXLInputValidator.preprocess_image(
            rgb_image,
            torch.device("cpu"),
            torch.float32
        )

        # Check shape: (1, 3, H, W)
        assert tensor.shape == (1, 3, 512, 512)

        # Check dtype and device
        assert tensor.dtype == torch.float32
        assert tensor.device.type == "cpu"

        # Check value range: should be normalized to [-1, 1]
        # 128/127.5 - 1.0 ≈ 0.003921568627451
        assert tensor.min() >= -1.0
        assert tensor.max() <= 1.0
        assert torch.allclose(tensor, torch.full_like(tensor, 0.003921568627451), atol=1e-5)

    def test_preprocess_rgba_image(self, rgba_image):
        """Test that RGBA image is converted to RGB"""
        tensor = SDXLInputValidator.preprocess_image(
            rgba_image,
            torch.device("cpu"),
            torch.float32
        )

        # Should have 3 channels (RGB), not 4 (RGBA)
        assert tensor.shape == (1, 3, 512, 512)

    def test_preprocess_grayscale_image(self, grayscale_image):
        """Test that grayscale image is converted to RGB"""
        tensor = SDXLInputValidator.preprocess_image(
            grayscale_image,
            torch.device("cpu"),
            torch.float32
        )

        # Should have 3 channels (RGB), not 1 (grayscale)
        assert tensor.shape == (1, 3, 512, 512)

    def test_normalization_black_image(self):
        """Test normalization for black image (pixel value 0)"""
        black_image = Image.new("RGB", (512, 512), color=(0, 0, 0))
        tensor = SDXLInputValidator.preprocess_image(
            black_image,
            torch.device("cpu"),
            torch.float32
        )

        # 0/127.5 - 1.0 = -1.0
        assert torch.allclose(tensor, torch.full_like(tensor, -1.0), atol=1e-5)

    def test_normalization_white_image(self):
        """Test normalization for white image (pixel value 255)"""
        white_image = Image.new("RGB", (512, 512), color=(255, 255, 255))
        tensor = SDXLInputValidator.preprocess_image(
            white_image,
            torch.device("cpu"),
            torch.float32
        )

        # 255/127.5 - 1.0 = 1.0
        assert torch.allclose(tensor, torch.full_like(tensor, 1.0), atol=1e-5)

    def test_dtype_conversion(self):
        """Test that image is converted to target dtype"""
        image = Image.new("RGB", (512, 512), color=(128, 128, 128))

        # Test float16
        tensor_fp16 = SDXLInputValidator.preprocess_image(
            image,
            torch.device("cpu"),
            torch.float16
        )
        assert tensor_fp16.dtype == torch.float16

        # Test float32
        tensor_fp32 = SDXLInputValidator.preprocess_image(
            image,
            torch.device("cpu"),
            torch.float32
        )
        assert tensor_fp32.dtype == torch.float32

    @pytest.mark.requires_gpu
    def test_device_placement(self):
        """Test that image is moved to target device"""
        image = Image.new("RGB", (512, 512), color=(128, 128, 128))

        # Test CPU placement
        tensor_cpu = SDXLInputValidator.preprocess_image(
            image,
            torch.device("cpu"),
            torch.float32
        )
        assert tensor_cpu.device.type == "cpu"

        # Test CUDA placement
        tensor_cuda = SDXLInputValidator.preprocess_image(
            image,
            torch.device("cuda"),
            torch.float32
        )
        assert tensor_cuda.device.type == "cuda"

    def test_dimension_order(self):
        """Test that dimensions are correctly reordered from HWC to CHW"""
        # Create image with distinctive RGB pattern
        image = Image.new("RGB", (64, 64))
        pixels = image.load()
        # Set top-left pixel to red (255, 0, 0)
        pixels[0, 0] = (255, 0, 0)

        tensor = SDXLInputValidator.preprocess_image(
            image,
            torch.device("cpu"),
            torch.float32
        )

        # Check that red channel has value ~1.0 at [0, 0, 0]
        # 255/127.5 - 1.0 = 1.0
        assert torch.allclose(tensor[0, 0, 0, 0], torch.tensor(1.0), atol=1e-5)
        # Green and blue channels should be ~-1.0
        assert torch.allclose(tensor[0, 1, 0, 0], torch.tensor(-1.0), atol=1e-5)
        assert torch.allclose(tensor[0, 2, 0, 0], torch.tensor(-1.0), atol=1e-5)


class TestMaskPreprocessing:
    """Tests for mask preprocessing"""

    @pytest.fixture
    def white_mask(self):
        """Create a white mask (keep everything)"""
        return Image.new("L", (1024, 1024), color=255)

    @pytest.fixture
    def black_mask(self):
        """Create a black mask (regenerate everything)"""
        return Image.new("L", (1024, 1024), color=0)

    @pytest.fixture
    def gray_mask(self):
        """Create a gray mask (partial mask)"""
        return Image.new("L", (1024, 1024), color=128)

    def test_preprocess_grayscale_mask(self, white_mask):
        """Test preprocessing of grayscale mask"""
        latent_shape = (1, 4, 128, 128)  # 1024/8 = 128
        tensor = SDXLInputValidator.preprocess_mask(
            white_mask,
            latent_shape,
            torch.device("cpu"),
            torch.float32
        )

        # Check shape: (1, 1, H/8, W/8)
        assert tensor.shape == (1, 1, 128, 128)

        # Check dtype and device
        assert tensor.dtype == torch.float32
        assert tensor.device.type == "cpu"

        # Check value range: should be normalized to [0, 1]
        assert tensor.min() >= 0.0
        assert tensor.max() <= 1.0

    def test_preprocess_rgb_mask(self):
        """Test that RGB mask is converted to grayscale"""
        rgb_mask = Image.new("RGB", (1024, 1024), color=(255, 255, 255))
        latent_shape = (1, 4, 128, 128)

        tensor = SDXLInputValidator.preprocess_mask(
            rgb_mask,
            latent_shape,
            torch.device("cpu"),
            torch.float32
        )

        # Should have 1 channel (grayscale)
        assert tensor.shape == (1, 1, 128, 128)

    def test_normalization_white_mask(self, white_mask):
        """Test normalization for white mask (255 → 1.0)"""
        latent_shape = (1, 4, 128, 128)
        tensor = SDXLInputValidator.preprocess_mask(
            white_mask,
            latent_shape,
            torch.device("cpu"),
            torch.float32
        )

        # 255/255.0 = 1.0
        assert torch.allclose(tensor, torch.ones_like(tensor), atol=1e-5)

    def test_normalization_black_mask(self, black_mask):
        """Test normalization for black mask (0 → 0.0)"""
        latent_shape = (1, 4, 128, 128)
        tensor = SDXLInputValidator.preprocess_mask(
            black_mask,
            latent_shape,
            torch.device("cpu"),
            torch.float32
        )

        # 0/255.0 = 0.0
        assert torch.allclose(tensor, torch.zeros_like(tensor), atol=1e-5)

    def test_normalization_gray_mask(self, gray_mask):
        """Test normalization for gray mask (128 → 0.5)"""
        latent_shape = (1, 4, 128, 128)
        tensor = SDXLInputValidator.preprocess_mask(
            gray_mask,
            latent_shape,
            torch.device("cpu"),
            torch.float32
        )

        # 128/255.0 ≈ 0.502
        expected = 128.0 / 255.0
        assert torch.allclose(tensor, torch.full_like(tensor, expected), atol=1e-3)

    def test_resize_to_latent_dimensions(self):
        """Test that mask is resized to latent dimensions"""
        mask = Image.new("L", (2048, 2048), color=255)

        # Different latent shapes
        test_cases = [
            ((1, 4, 128, 128), (1, 1, 128, 128)),  # 1024x1024 image
            ((1, 4, 256, 256), (1, 1, 256, 256)),  # 2048x2048 image
            ((1, 4, 64, 64), (1, 1, 64, 64)),      # 512x512 image
        ]

        for latent_shape, expected_shape in test_cases:
            tensor = SDXLInputValidator.preprocess_mask(
                mask,
                latent_shape,
                torch.device("cpu"),
                torch.float32
            )
            assert tensor.shape == expected_shape

    def test_nearest_neighbor_interpolation(self):
        """Test that nearest-neighbor interpolation preserves mask edges"""
        # Create a binary mask with sharp edge
        mask = Image.new("L", (128, 128), color=0)
        pixels = mask.load()
        # Fill left half with white
        for x in range(64):
            for y in range(128):
                pixels[x, y] = 255

        latent_shape = (1, 4, 16, 16)  # Downsample 8x (128/8 = 16)
        tensor = SDXLInputValidator.preprocess_mask(
            mask,
            latent_shape,
            torch.device("cpu"),
            torch.float32
        )

        # Left half should be mostly 1.0, right half mostly 0.0
        # (some blending at edge due to downsampling)
        left_half = tensor[0, 0, :, :8]
        right_half = tensor[0, 0, :, 8:]

        assert left_half.mean() > 0.8  # Mostly white
        assert right_half.mean() < 0.2  # Mostly black

    def test_dtype_conversion(self):
        """Test that mask is converted to target dtype"""
        mask = Image.new("L", (1024, 1024), color=128)
        latent_shape = (1, 4, 128, 128)

        # Test float16
        tensor_fp16 = SDXLInputValidator.preprocess_mask(
            mask,
            latent_shape,
            torch.device("cpu"),
            torch.float16
        )
        assert tensor_fp16.dtype == torch.float16

        # Test float32
        tensor_fp32 = SDXLInputValidator.preprocess_mask(
            mask,
            latent_shape,
            torch.device("cpu"),
            torch.float32
        )
        assert tensor_fp32.dtype == torch.float32

    @pytest.mark.requires_gpu
    def test_device_placement(self):
        """Test that mask is moved to target device"""
        mask = Image.new("L", (1024, 1024), color=128)
        latent_shape = (1, 4, 128, 128)

        # Test CPU placement
        tensor_cpu = SDXLInputValidator.preprocess_mask(
            mask,
            latent_shape,
            torch.device("cpu"),
            torch.float32
        )
        assert tensor_cpu.device.type == "cpu"

        # Test CUDA placement
        tensor_cuda = SDXLInputValidator.preprocess_mask(
            mask,
            latent_shape,
            torch.device("cuda"),
            torch.float32
        )
        assert tensor_cuda.device.type == "cuda"


class TestConditioningValidation:
    """Tests for conditioning validation"""

    class ValidConditioning:
        """Valid conditioning object with all required attributes"""
        def __init__(self):
            self.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
            self.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}

    def test_valid_conditioning(self):
        """Test that valid conditioning passes validation"""
        conditioning = self.ValidConditioning()
        # Should not raise any exception
        SDXLInputValidator.validate_conditioning(conditioning)

    def test_missing_p_prompt_embeds(self):
        """Test that a missing positive 'embeds' role raises ValueError"""
        conditioning = self.ValidConditioning()
        del conditioning.embeds["embeds"]

        with pytest.raises(ValueError, match="embeds.embeds"):
            SDXLInputValidator.validate_conditioning(conditioning)

    def test_missing_n_prompt_embeds(self):
        """Test that a missing negative 'embeds' role raises ValueError"""
        conditioning = self.ValidConditioning()
        del conditioning.n_embeds["embeds"]

        with pytest.raises(ValueError, match="n_embeds.embeds"):
            SDXLInputValidator.validate_conditioning(conditioning)

    def test_missing_p_prompt_pooled_embeds(self):
        """Test that a missing positive 'pooled' role raises ValueError"""
        conditioning = self.ValidConditioning()
        del conditioning.embeds["pooled"]

        with pytest.raises(ValueError, match="embeds.pooled"):
            SDXLInputValidator.validate_conditioning(conditioning)

    def test_missing_n_prompt_pooled_embeds(self):
        """Test that a missing negative 'pooled' role raises ValueError"""
        conditioning = self.ValidConditioning()
        del conditioning.n_embeds["pooled"]

        with pytest.raises(ValueError, match="n_embeds.pooled"):
            SDXLInputValidator.validate_conditioning(conditioning)

    def test_missing_multiple_attributes(self):
        """Test that missing multiple attributes are reported"""
        class PartialConditioning:
            def __init__(self):
                self.embeds = {"embeds": torch.randn(1, 77, 2048)}  # missing "pooled"
                # n_embeds missing entirely

        conditioning = PartialConditioning()

        with pytest.raises(ValueError) as exc_info:
            SDXLInputValidator.validate_conditioning(conditioning)

        error_message = str(exc_info.value)
        # Should mention all missing attributes
        assert "embeds.pooled" in error_message
        assert "n_embeds" in error_message

    def test_empty_conditioning_object(self):
        """Test that empty conditioning object raises ValueError"""
        class EmptyConditioning:
            pass

        conditioning = EmptyConditioning()

        with pytest.raises(ValueError) as exc_info:
            SDXLInputValidator.validate_conditioning(conditioning)

        error_message = str(exc_info.value)
        # Should mention both required top-level attributes
        assert "embeds" in error_message
        assert "n_embeds" in error_message

    def test_attributes_can_be_none(self):
        """Test that role values can exist but be None (not enforced by validator)"""
        # Note: This test documents current behavior - validator only checks
        # presence of the "embeds"/"pooled" keys, not their values. Additional
        # validation could be added in the future if needed.
        class ConditioningWithNone:
            def __init__(self):
                self.embeds = {"embeds": None, "pooled": None}
                self.n_embeds = {"embeds": None, "pooled": None}

        conditioning = ConditioningWithNone()
        # Should not raise - validator only checks attribute existence
        SDXLInputValidator.validate_conditioning(conditioning)


class TestPipelineInputValidation:
    """Tests for validate_pipeline_inputs method"""

    @pytest.fixture
    def callback_tensor_inputs(self):
        """Valid callback tensor input names"""
        return ["latents", "prompt_embeds", "negative_prompt_embeds"]

    def test_valid_basic_inputs(self, callback_tensor_inputs):
        """Test that valid basic inputs pass validation"""
        SDXLInputValidator.validate_pipeline_inputs(
            callback_tensor_inputs,
            prompt="a cat",
            prompt_2="a cat",
            height=1024,
            width=1024,
            callback_steps=10
        )

    def test_invalid_height_not_divisible_by_8(self, callback_tensor_inputs):
        """Test that height not divisible by 8 raises ValueError"""
        with pytest.raises(ValueError, match="have to be divisible by 8"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2="a cat",
                height=1023,
                width=1024,
                callback_steps=10
            )

    def test_invalid_width_not_divisible_by_8(self, callback_tensor_inputs):
        """Test that width not divisible by 8 raises ValueError"""
        with pytest.raises(ValueError, match="have to be divisible by 8"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2="a cat",
                height=1024,
                width=1023,
                callback_steps=10
            )

    def test_invalid_callback_steps_not_integer(self, callback_tensor_inputs):
        """Test that non-integer callback_steps raises ValueError"""
        with pytest.raises(ValueError, match="has to be a positive integer"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2="a cat",
                height=1024,
                width=1024,
                callback_steps=10.5
            )

    def test_invalid_callback_steps_negative(self, callback_tensor_inputs):
        """Test that negative callback_steps raises ValueError"""
        with pytest.raises(ValueError, match="has to be a positive integer"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2="a cat",
                height=1024,
                width=1024,
                callback_steps=-1
            )

    def test_invalid_callback_tensor_inputs(self, callback_tensor_inputs):
        """Test that invalid callback tensor inputs raise ValueError"""
        with pytest.raises(ValueError, match="callback_on_step_end_tensor_inputs"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2="a cat",
                height=1024,
                width=1024,
                callback_steps=10,
                callback_on_step_end_tensor_inputs=["invalid_tensor"]
            )

    def test_both_prompt_and_prompt_embeds(self, callback_tensor_inputs):
        """Test that both prompt and prompt_embeds raises ValueError"""
        with pytest.raises(ValueError, match="Cannot forward both"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2="a cat",
                height=1024,
                width=1024,
                callback_steps=10,
                prompt_embeds=torch.randn(1, 77, 2048)
            )

    def test_both_prompt_2_and_prompt_embeds(self, callback_tensor_inputs):
        """Test that both prompt_2 and prompt_embeds raises ValueError"""
        with pytest.raises(ValueError, match="Cannot forward both"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt=None,
                prompt_2="a cat",
                height=1024,
                width=1024,
                callback_steps=10,
                prompt_embeds=torch.randn(1, 77, 2048),
                pooled_prompt_embeds=torch.randn(1, 1280)
            )

    def test_neither_prompt_nor_prompt_embeds(self, callback_tensor_inputs):
        """Test that neither prompt nor prompt_embeds raises ValueError"""
        with pytest.raises(ValueError, match="Provide either"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt=None,
                prompt_2=None,
                height=1024,
                width=1024,
                callback_steps=10
            )

    def test_prompt_not_str_or_list(self, callback_tensor_inputs):
        """Test that prompt not str or list raises ValueError"""
        with pytest.raises(ValueError, match="has to be of type"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt=123,
                prompt_2="a cat",
                height=1024,
                width=1024,
                callback_steps=10
            )

    def test_prompt_2_not_str_or_list(self, callback_tensor_inputs):
        """Test that prompt_2 not str or list raises ValueError"""
        with pytest.raises(ValueError, match="has to be of type"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2=123,
                height=1024,
                width=1024,
                callback_steps=10
            )

    def test_both_negative_prompt_and_negative_prompt_embeds(self, callback_tensor_inputs):
        """Test that both negative_prompt and negative_prompt_embeds raises ValueError"""
        with pytest.raises(ValueError, match="Cannot forward both"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2="a cat",
                height=1024,
                width=1024,
                callback_steps=10,
                negative_prompt="ugly",
                negative_prompt_embeds=torch.randn(1, 77, 2048)
            )

    def test_both_negative_prompt_2_and_negative_prompt_embeds(self, callback_tensor_inputs):
        """Test that both negative_prompt_2 and negative_prompt_embeds raises ValueError"""
        with pytest.raises(ValueError, match="Cannot forward both"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2="a cat",
                height=1024,
                width=1024,
                callback_steps=10,
                negative_prompt_2="ugly",
                negative_prompt_embeds=torch.randn(1, 77, 2048)
            )

    def test_mismatched_prompt_embeds_shape(self, callback_tensor_inputs):
        """Test that mismatched prompt_embeds shapes raise ValueError"""
        with pytest.raises(ValueError, match="must have the same shape"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt=None,
                prompt_2=None,
                height=1024,
                width=1024,
                callback_steps=10,
                prompt_embeds=torch.randn(1, 77, 2048),
                negative_prompt_embeds=torch.randn(1, 77, 1024),
                pooled_prompt_embeds=torch.randn(1, 1280)
            )

    def test_prompt_embeds_without_pooled(self, callback_tensor_inputs):
        """Test that prompt_embeds without pooled_prompt_embeds raises ValueError"""
        with pytest.raises(ValueError, match="pooled_prompt_embeds"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt=None,
                prompt_2=None,
                height=1024,
                width=1024,
                callback_steps=10,
                prompt_embeds=torch.randn(1, 77, 2048)
            )

    def test_negative_prompt_embeds_without_pooled(self, callback_tensor_inputs):
        """Test that negative_prompt_embeds without negative_pooled_prompt_embeds raises ValueError"""
        with pytest.raises(ValueError, match="negative_pooled_prompt_embeds"):
            SDXLInputValidator.validate_pipeline_inputs(
                callback_tensor_inputs,
                prompt="a cat",
                prompt_2="a cat",
                height=1024,
                width=1024,
                callback_steps=10,
                negative_prompt_embeds=torch.randn(1, 77, 2048)
            )

    def test_valid_with_embeds(self, callback_tensor_inputs):
        """Test that valid inputs with embeddings pass validation"""
        SDXLInputValidator.validate_pipeline_inputs(
            callback_tensor_inputs,
            prompt=None,
            prompt_2=None,
            height=1024,
            width=1024,
            callback_steps=10,
            prompt_embeds=torch.randn(1, 77, 2048),
            negative_prompt_embeds=torch.randn(1, 77, 2048),
            pooled_prompt_embeds=torch.randn(1, 1280),
            negative_pooled_prompt_embeds=torch.randn(1, 1280)
        )

    def test_valid_callback_tensor_inputs(self, callback_tensor_inputs):
        """Test that valid callback tensor inputs pass validation"""
        SDXLInputValidator.validate_pipeline_inputs(
            callback_tensor_inputs,
            prompt="a cat",
            prompt_2="a cat",
            height=1024,
            width=1024,
            callback_steps=10,
            callback_on_step_end_tensor_inputs=["latents"]
        )

    def test_none_callback_steps(self, callback_tensor_inputs):
        """Test that None callback_steps is allowed"""
        SDXLInputValidator.validate_pipeline_inputs(
            callback_tensor_inputs,
            prompt="a cat",
            prompt_2="a cat",
            height=1024,
            width=1024,
            callback_steps=None
        )

    def test_prompt_list(self, callback_tensor_inputs):
        """Test that prompt as list is valid"""
        SDXLInputValidator.validate_pipeline_inputs(
            callback_tensor_inputs,
            prompt=["a cat", "a dog"],
            prompt_2=["a cat", "a dog"],
            height=1024,
            width=1024,
            callback_steps=10
        )


class TestEdgeCases:
    """Tests for edge cases and integration scenarios"""

    def test_very_large_image(self):
        """Test preprocessing of very large image"""
        # 8K image
        large_image = Image.new("RGB", (8192, 4320), color=(128, 128, 128))
        tensor = SDXLInputValidator.preprocess_image(
            large_image,
            torch.device("cpu"),
            torch.float32
        )

        assert tensor.shape == (1, 3, 4320, 8192)
        assert tensor.dtype == torch.float32

    def test_very_small_image(self):
        """Test preprocessing of very small image"""
        # Minimum SDXL resolution
        small_image = Image.new("RGB", (512, 512), color=(128, 128, 128))
        tensor = SDXLInputValidator.preprocess_image(
            small_image,
            torch.device("cpu"),
            torch.float32
        )

        assert tensor.shape == (1, 3, 512, 512)
        assert tensor.dtype == torch.float32

    def test_non_square_image(self):
        """Test preprocessing of non-square image"""
        rect_image = Image.new("RGB", (1920, 1080), color=(128, 128, 128))
        tensor = SDXLInputValidator.preprocess_image(
            rect_image,
            torch.device("cpu"),
            torch.float32
        )

        assert tensor.shape == (1, 3, 1080, 1920)
        assert tensor.dtype == torch.float32

    def test_portrait_orientation(self):
        """Test preprocessing of portrait-oriented image"""
        portrait_image = Image.new("RGB", (768, 1024), color=(128, 128, 128))
        tensor = SDXLInputValidator.preprocess_image(
            portrait_image,
            torch.device("cpu"),
            torch.float32
        )

        assert tensor.shape == (1, 3, 1024, 768)
        assert tensor.dtype == torch.float32

    def test_latent_shape_with_batch_size(self):
        """Test mask preprocessing with different batch sizes"""
        mask = Image.new("L", (1024, 1024), color=255)

        # Batch size doesn't affect mask preprocessing (always batch=1)
        latent_shapes = [
            (1, 4, 128, 128),   # Batch size 1
            (2, 4, 128, 128),   # Batch size 2 (but mask still (1, 1, 128, 128))
            (4, 4, 128, 128),   # Batch size 4
        ]

        for latent_shape in latent_shapes:
            tensor = SDXLInputValidator.preprocess_mask(
                mask,
                latent_shape,
                torch.device("cpu"),
                torch.float32
            )
            # Mask always has batch size 1
            assert tensor.shape == (1, 1, latent_shape[2], latent_shape[3])
