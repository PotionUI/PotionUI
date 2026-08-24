"""
Regression Tests for SDXL Output Consistency

These tests verify that the SDXL pipeline produces deterministic outputs: given the
same inputs and a fixed seed, repeated runs yield identical results. This guards against
behavioral changes or regressions that would surface as non-deterministic generation.
"""

import pytest
import torch
import numpy as np
from unittest.mock import Mock
from PIL import Image
from typing import Tuple

from src.pipelines.pipes.generator.sdxl.main import GeneratorSDXLPipe
from src.pipelines.contracts import PipeInput, IOType
from src.pipelines.contracts import GenerationInput, GenerationInputItem
from src.pipelines.outputs import ImageGenerationOutput


class MockDeterministicModel:
    """
    Mock model that produces deterministic outputs based on seed.

    This simulates actual generation by creating synthetic images with
    predictable pixel patterns based on the seed value.
    """

    def __init__(self):
        self.txt2img_calls = []
        self.img2img_calls = []

    def txt2img(self, generation_input: GenerationInput, callback=None):
        """Generate deterministic image for txt2img."""
        self.txt2img_calls.append(generation_input)

        seed = generation_input[IOType.SEED]
        resolution = generation_input[IOType.RESOLUTION]
        width, height = resolution[0], resolution[1]

        # Create deterministic image based on seed
        np.random.seed(seed)
        image_array = np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)
        image = Image.fromarray(image_array)

        return ImageGenerationOutput(image=image, seed=seed)

    def img2img(self, generation_input: GenerationInput, callback=None):
        """Generate deterministic image for img2img."""
        self.img2img_calls.append(generation_input)

        seed = generation_input[IOType.SEED]
        input_image = generation_input[IOType.IMAGE]
        width, height = input_image.size

        # Create deterministic image based on seed and input
        np.random.seed(seed)
        # Blend with input image for img2img behavior
        input_array = np.array(input_image)
        noise_array = np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)
        denoise = generation_input[IOType.DENOISE]
        blended = (input_array * (1 - denoise) + noise_array * denoise).astype(np.uint8)
        image = Image.fromarray(blended)

        return ImageGenerationOutput(image=image, seed=seed)

    def txt2img_controlnet(self, generation_input: GenerationInput, control_images, callback=None):
        """Generate deterministic image for txt2img with ControlNet."""
        self.txt2img_calls.append(generation_input)

        seed = generation_input[IOType.SEED]
        resolution = generation_input[IOType.RESOLUTION]
        width, height = resolution[0], resolution[1]

        # Create deterministic image with ControlNet influence
        np.random.seed(seed)
        image_array = np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)

        # Add control image influence
        if control_images:
            control_array = np.array(control_images[0].resize((width, height)))
            image_array = (image_array * 0.7 + control_array * 0.3).astype(np.uint8)

        image = Image.fromarray(image_array)
        return ImageGenerationOutput(image=image, seed=seed)

    def img2img_controlnet(self, generation_input: GenerationInput, control_images, callback=None):
        """Generate deterministic image for img2img with ControlNet."""
        return self.img2img(generation_input, callback)

    def load_with_controlnet(self, controlnets, mode="txt2img"):
        """Mock ControlNet loading."""
        pass


def compare_images(img1: Image.Image, img2: Image.Image, tolerance: float = 0.01) -> Tuple[bool, float]:
    """
    Compare two images and return similarity.

    Args:
        img1: First image
        img2: Second image
        tolerance: Maximum allowed difference ratio (0.01 = 1%)

    Returns:
        Tuple of (images_match, difference_ratio)
    """
    # Convert to arrays
    arr1 = np.array(img1)
    arr2 = np.array(img2)

    # Check dimensions match
    if arr1.shape != arr2.shape:
        return False, 1.0

    # Calculate pixel-wise difference
    diff = np.abs(arr1.astype(float) - arr2.astype(float))
    max_diff = 255.0 * arr1.size
    total_diff = np.sum(diff)
    diff_ratio = total_diff / max_diff

    return diff_ratio <= tolerance, diff_ratio


class TestOutputConsistencyTxt2Img:
    """Test output consistency for txt2img generation."""

    @pytest.fixture
    def mock_conditioning(self):
        """Create deterministic conditioning."""
        conditioning = Mock()
        # Use fixed seed for deterministic tensors
        torch.manual_seed(42)
        conditioning.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        conditioning.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        return conditioning

    def test_txt2img_identical_output_fixed_seed(self, mock_conditioning):
        """Test that repeated txt2img runs produce identical outputs with a fixed seed."""
        FIXED_SEED = 42

        config = GeneratorSDXLPipe.get_default_config()
        config["resolution"] = "512x512"
        config["steps"] = 20
        config["cfg"] = 7.5

        # Create generator
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()

        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })

        first_result = generator_pipe.process(first_input, Mock())
        first_image = first_result.output["image"][0]

        # Second run
        second_model = MockDeterministicModel()

        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })

        second_result = generator_pipe.process(second_input, Mock())
        second_image = second_result.output["image"][0]

        # Compare outputs (should be identical since both use same seed in mock)
        match, diff_ratio = compare_images(first_image, second_image, tolerance=0.0)
        assert match, f"Images differ by {diff_ratio*100:.2f}% (expected identical)"

    def test_txt2img_multiple_seeds_consistency(self, mock_conditioning):
        """Test consistency across multiple seeds."""
        SEEDS = [42, 123, 999]

        config = GeneratorSDXLPipe.get_default_config()
        config["resolution"] = "512x512"
        config["quantity"] = 3

        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()

        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": SEEDS
        })

        first_result = generator_pipe.process(first_input, Mock())
        first_images = first_result.output["image"]

        # Second run
        second_model = MockDeterministicModel()

        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": SEEDS
        })

        second_result = generator_pipe.process(second_input, Mock())
        second_images = second_result.output["image"]

        # Compare all images
        assert len(first_images) == len(second_images) == 3

        for i, (first_img, second_img) in enumerate(zip(first_images, second_images)):
            match, diff_ratio = compare_images(first_img, second_img, tolerance=0.0)
            assert match, f"Image {i} differs by {diff_ratio*100:.2f}% (seed={SEEDS[i]})"

    def test_txt2img_different_resolutions_consistency(self, mock_conditioning):
        """Test consistency across different resolutions."""
        RESOLUTIONS = ["512x512", "768x768", "1024x1024"]
        FIXED_SEED = 42

        for resolution in RESOLUTIONS:
            config = GeneratorSDXLPipe.get_default_config()
            config["resolution"] = resolution
            generator_pipe = GeneratorSDXLPipe(config)

            # First run
            first_model = MockDeterministicModel()
            first_input = PipeInput(input={
                "model": first_model,
                "conditioning": [mock_conditioning],
                "seed": [FIXED_SEED]
            })
            first_result = generator_pipe.process(first_input, Mock())
            first_image = first_result.output["image"][0]

            # Second run
            second_model = MockDeterministicModel()
            second_input = PipeInput(input={
                "model": second_model,
                "conditioning": [mock_conditioning],
                "seed": [FIXED_SEED]
            })
            second_result = generator_pipe.process(second_input, Mock())
            second_image = second_result.output["image"][0]

            # Compare
            match, diff_ratio = compare_images(first_image, second_image, tolerance=0.0)
            assert match, f"Images at {resolution} differ by {diff_ratio*100:.2f}%"


class TestOutputConsistencyImg2Img:
    """Test output consistency for img2img generation."""

    @pytest.fixture
    def mock_conditioning(self):
        """Create deterministic conditioning."""
        conditioning = Mock()
        torch.manual_seed(42)
        conditioning.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        conditioning.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        return conditioning

    @pytest.fixture
    def input_image(self):
        """Create deterministic input image."""
        np.random.seed(100)
        arr = np.random.randint(0, 256, (512, 512, 3), dtype=np.uint8)
        return Image.fromarray(arr)

    def test_img2img_identical_output_fixed_seed(self, mock_conditioning, input_image):
        """Test that img2img produces identical outputs with fixed seed."""
        FIXED_SEED = 42

        config = GeneratorSDXLPipe.get_default_config()
        config["denoise"] = 0.7
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()
        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED],
            "image": [input_image]
        })
        first_result = generator_pipe.process(first_input, Mock())
        first_image = first_result.output["image"][0]

        # Second run
        second_model = MockDeterministicModel()
        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED],
            "image": [input_image]
        })
        second_result = generator_pipe.process(second_input, Mock())
        second_image = second_result.output["image"][0]

        # Compare
        match, diff_ratio = compare_images(first_image, second_image, tolerance=0.0)
        assert match, f"Img2img outputs differ by {diff_ratio*100:.2f}%"

    def test_img2img_different_denoise_consistency(self, mock_conditioning, input_image):
        """Test consistency across different denoise values."""
        DENOISE_VALUES = [0.3, 0.5, 0.7, 0.9]
        FIXED_SEED = 42

        for denoise in DENOISE_VALUES:
            config = GeneratorSDXLPipe.get_default_config()
            config["denoise"] = denoise
            generator_pipe = GeneratorSDXLPipe(config)

            # First run
            first_model = MockDeterministicModel()
            first_input = PipeInput(input={
                "model": first_model,
                "conditioning": [mock_conditioning],
                "seed": [FIXED_SEED],
                "image": [input_image]
            })
            first_result = generator_pipe.process(first_input, Mock())
            first_img = first_result.output["image"][0]

            # Second run
            second_model = MockDeterministicModel()
            second_input = PipeInput(input={
                "model": second_model,
                "conditioning": [mock_conditioning],
                "seed": [FIXED_SEED],
                "image": [input_image]
            })
            second_result = generator_pipe.process(second_input, Mock())
            second_img = second_result.output["image"][0]

            # Compare
            match, diff_ratio = compare_images(first_img, second_img, tolerance=0.0)
            assert match, f"Img2img with denoise={denoise} differs by {diff_ratio*100:.2f}%"

    def test_img2img_with_mask_consistency(self, mock_conditioning, input_image):
        """Test img2img with mask (inpainting) consistency."""
        FIXED_SEED = 42

        # Create deterministic mask
        np.random.seed(200)
        mask_arr = np.random.randint(0, 256, (512, 512), dtype=np.uint8)
        mask_image = Image.fromarray(mask_arr, mode='L')

        config = GeneratorSDXLPipe.get_default_config()
        config["inpaint_mode"] = True
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()
        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED],
            "image": [input_image],
            "mask": mask_image
        })
        first_result = generator_pipe.process(first_input, Mock())
        first_img = first_result.output["image"][0]

        # Second run
        second_model = MockDeterministicModel()
        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED],
            "image": [input_image],
            "mask": mask_image
        })
        second_result = generator_pipe.process(second_input, Mock())
        second_img = second_result.output["image"][0]

        # Compare
        match, diff_ratio = compare_images(first_img, second_img, tolerance=0.0)
        assert match, f"Inpainting outputs differ by {diff_ratio*100:.2f}%"


class TestOutputConsistencyControlNet:
    """Test output consistency for ControlNet generation."""

    @pytest.fixture
    def mock_conditioning(self):
        """Create deterministic conditioning."""
        conditioning = Mock()
        torch.manual_seed(42)
        conditioning.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        conditioning.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        return conditioning

    @pytest.fixture
    def control_image(self):
        """Create deterministic control image."""
        np.random.seed(300)
        arr = np.random.randint(0, 256, (512, 512, 3), dtype=np.uint8)
        return Image.fromarray(arr)

    @pytest.fixture
    def controlnet_config(self):
        """Create ControlNet configuration."""
        return {
            'model': Mock(),
            'conditioning_scale': 0.8,
            'control_guidance_start': 0.0,
            'control_guidance_end': 1.0
        }

    def test_controlnet_txt2img_consistency(self, mock_conditioning, control_image, controlnet_config):
        """Test ControlNet txt2img produces consistent outputs."""
        FIXED_SEED = 42

        config = GeneratorSDXLPipe.get_default_config()
        config["resolution"] = "512x512"
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()
        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED],
            "controlnet": [controlnet_config],
            "control_image": [control_image]
        })
        first_result = generator_pipe.process(first_input, Mock())
        first_img = first_result.output["image"][0]

        # Second run
        second_model = MockDeterministicModel()
        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED],
            "controlnet": [controlnet_config],
            "control_image": [control_image]
        })
        second_result = generator_pipe.process(second_input, Mock())
        second_img = second_result.output["image"][0]

        # Compare
        match, diff_ratio = compare_images(first_img, second_img, tolerance=0.0)
        assert match, f"ControlNet txt2img outputs differ by {diff_ratio*100:.2f}%"

    def test_controlnet_multiple_models_consistency(self, mock_conditioning):
        """Test multiple ControlNet models produce consistent outputs."""
        FIXED_SEED = 42

        controlnets = [
            {
                'model': Mock(),
                'conditioning_scale': 0.8,
                'control_guidance_start': 0.0,
                'control_guidance_end': 0.5
            },
            {
                'model': Mock(),
                'conditioning_scale': 0.6,
                'control_guidance_start': 0.5,
                'control_guidance_end': 1.0
            }
        ]

        control_images = [
            Image.fromarray(np.random.RandomState(300).randint(0, 256, (512, 512, 3), dtype=np.uint8)),
            Image.fromarray(np.random.RandomState(301).randint(0, 256, (512, 512, 3), dtype=np.uint8))
        ]

        config = GeneratorSDXLPipe.get_default_config()
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()
        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED],
            "controlnet": controlnets,
            "control_image": control_images
        })
        first_result = generator_pipe.process(first_input, Mock())
        first_img = first_result.output["image"][0]

        # Second run
        second_model = MockDeterministicModel()
        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED],
            "controlnet": controlnets,
            "control_image": control_images
        })
        second_result = generator_pipe.process(second_input, Mock())
        second_img = second_result.output["image"][0]

        # Compare
        match, diff_ratio = compare_images(first_img, second_img, tolerance=0.0)
        assert match, f"Multi-ControlNet outputs differ by {diff_ratio*100:.2f}%"


class TestAdvancedFeatureConsistency:
    """Test consistency of advanced features (ADM, SAG, Sharpness)."""

    @pytest.fixture
    def mock_conditioning(self):
        """Create deterministic conditioning."""
        conditioning = Mock()
        torch.manual_seed(42)
        conditioning.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        conditioning.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        return conditioning

    def test_adm_guidance_parameters_consistency(self, mock_conditioning):
        """Test ADM guidance parameters are passed consistently."""
        FIXED_SEED = 42

        config = GeneratorSDXLPipe.get_default_config()
        config.update({
            "adm_guidance_enabled": True,
            "adm_positive_scale": 1.8,
            "adm_negative_scale": 0.6,
            "adm_scaler_end": 0.4
        })
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()
        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(first_input, Mock())

        # Second run
        second_model = MockDeterministicModel()
        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(second_input, Mock())

        # Compare generation inputs
        first_gen_input = first_model.txt2img_calls[0]
        second_gen_input = second_model.txt2img_calls[0]

        # Verify ADM parameters match
        assert first_gen_input.get_by_name("adm_guidance_enabled") == second_gen_input.get_by_name("adm_guidance_enabled")
        assert first_gen_input.get_by_name("adm_positive_scale") == second_gen_input.get_by_name("adm_positive_scale")
        assert first_gen_input.get_by_name("adm_negative_scale") == second_gen_input.get_by_name("adm_negative_scale")
        assert first_gen_input.get_by_name("adm_scaler_end") == second_gen_input.get_by_name("adm_scaler_end")

    def test_sag_guidance_parameters_consistency(self, mock_conditioning):
        """Test SAG guidance parameters are passed consistently."""
        FIXED_SEED = 42

        config = GeneratorSDXLPipe.get_default_config()
        config.update({
            "sag_enabled": True,
            "sag_scale": 0.8,
            "sag_sigma": 1.5,
            "sag_threshold": 0.6
        })
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()
        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(first_input, Mock())

        # Second run
        second_model = MockDeterministicModel()
        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(second_input, Mock())

        # Compare generation inputs
        first_gen_input = first_model.txt2img_calls[0]
        second_gen_input = second_model.txt2img_calls[0]

        # Verify SAG parameters match
        assert first_gen_input.get_by_name("sag_enabled") == second_gen_input.get_by_name("sag_enabled")
        assert first_gen_input.get_by_name("sag_scale") == second_gen_input.get_by_name("sag_scale")
        assert first_gen_input.get_by_name("sag_sigma") == second_gen_input.get_by_name("sag_sigma")
        assert first_gen_input.get_by_name("sag_threshold") == second_gen_input.get_by_name("sag_threshold")

    def test_sharpness_parameter_consistency(self, mock_conditioning):
        """Test sharpness parameter is passed consistently."""
        FIXED_SEED = 42

        config = GeneratorSDXLPipe.get_default_config()
        config["sharpness"] = 3.0
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()
        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(first_input, Mock())

        # Second run
        second_model = MockDeterministicModel()
        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(second_input, Mock())

        # Compare generation inputs
        first_gen_input = first_model.txt2img_calls[0]
        second_gen_input = second_model.txt2img_calls[0]

        # Verify sharpness matches
        assert first_gen_input.get_by_name("sharpness") == second_gen_input.get_by_name("sharpness")


class TestSamplerSchedulerConsistency:
    """Test sampler and scheduler consistency."""

    @pytest.fixture
    def mock_conditioning(self):
        """Create deterministic conditioning."""
        conditioning = Mock()
        torch.manual_seed(42)
        conditioning.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        conditioning.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        return conditioning

    @pytest.mark.parametrize("sampler", [
        "EULER", "EULER_A", "HEUN", "DPM2", "DPMPP_2M", "DPMPP_2M_SDE"
    ])
    def test_sampler_consistency(self, sampler, mock_conditioning):
        """Test different samplers produce consistent outputs."""
        FIXED_SEED = 42

        config = GeneratorSDXLPipe.get_default_config()
        config["sampler"] = sampler
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()
        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(first_input, Mock())
        first_gen_input = first_model.txt2img_calls[0]

        # Second run
        second_model = MockDeterministicModel()
        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(second_input, Mock())
        second_gen_input = second_model.txt2img_calls[0]

        # Verify sampler was passed correctly
        assert first_gen_input[IOType.SAMPLER] == second_gen_input[IOType.SAMPLER]

    @pytest.mark.parametrize("scheduler", [
        "normal", "karras", "exponential", "sgm_uniform"
    ])
    def test_scheduler_consistency(self, scheduler, mock_conditioning):
        """Test different schedulers produce consistent outputs."""
        FIXED_SEED = 42

        config = GeneratorSDXLPipe.get_default_config()
        config["scheduler"] = scheduler
        generator_pipe = GeneratorSDXLPipe(config)

        # First run
        first_model = MockDeterministicModel()
        first_input = PipeInput(input={
            "model": first_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(first_input, Mock())
        first_gen_input = first_model.txt2img_calls[0]

        # Second run
        second_model = MockDeterministicModel()
        second_input = PipeInput(input={
            "model": second_model,
            "conditioning": [mock_conditioning],
            "seed": [FIXED_SEED]
        })
        generator_pipe.process(second_input, Mock())
        second_gen_input = second_model.txt2img_calls[0]

        # Verify scheduler was passed correctly
        assert first_gen_input[IOType.SCHEDULER] == second_gen_input[IOType.SCHEDULER]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
