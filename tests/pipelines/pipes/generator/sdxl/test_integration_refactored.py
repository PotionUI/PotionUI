"""
Integration test for refactored SDXL generator pipe implementation.

This test validates that the generator pipe works correctly with the
hook-based architecture where ADM, SAG, and Sharpness are separate pipes.
"""
import pytest
from unittest.mock import Mock
from PIL import Image

from src.pipelines.pipes.generator.sdxl.main import GeneratorSDXLPipe
from src.pipelines.contracts import PipeInput, IOType
from src.pipelines.contracts import GenerationInput
from src.pipelines.outputs import ImageGenerationOutput


class TestRefactoredIntegration:
    """Integration tests for refactored pipeline implementation."""

    @pytest.fixture
    def mock_model(self):
        """Create a mock model that simulates txt2img/img2img generation."""
        model = Mock()
        model.txt2img = Mock(return_value=ImageGenerationOutput(
            image=Image.new("RGB", (512, 512), color="red"),
            seed=12345
        ))
        model.img2img = Mock(return_value=ImageGenerationOutput(
            image=Image.new("RGB", (512, 512), color="blue"),
            seed=12345
        ))
        model.txt2img_controlnet = Mock(return_value=ImageGenerationOutput(
            image=Image.new("RGB", (512, 512), color="green"),
            seed=12345
        ))
        model.img2img_controlnet = Mock(return_value=ImageGenerationOutput(
            image=Image.new("RGB", (512, 512), color="yellow"),
            seed=12345
        ))
        model.load_with_controlnet = Mock()
        return model

    @pytest.fixture
    def mock_conditioning(self):
        """Create mock conditioning with required attributes."""
        import torch
        conditioning = Mock()
        conditioning.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        conditioning.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        return conditioning

    @pytest.fixture
    def generator_pipe(self):
        """Create generator pipe instance with default config."""
        config = GeneratorSDXLPipe.get_default_config()
        pipe = GeneratorSDXLPipe(config)
        return pipe

    def test_txt2img_basic_generation(self, generator_pipe, mock_model, mock_conditioning):
        """Test basic txt2img generation without environment variables."""
        # Create minimal input
        pipe_input = PipeInput(
            input={
                "model": mock_model,
                "conditioning": [mock_conditioning],
                "seed": [12345]
            }
        )

        # Mock generation outputs callback
        outputs_callback = Mock()

        # Call process
        result = generator_pipe.process(pipe_input, outputs_callback)

        # Verify txt2img was called
        assert mock_model.txt2img.called, "txt2img should be called"

    def test_img2img_mode(self, generator_pipe, mock_model, mock_conditioning):
        """Test implementation for img2img mode."""
        # Create input with image (triggers img2img mode)
        pipe_input = PipeInput(
            input={
                "model": mock_model,
                "conditioning": [mock_conditioning],
                "seed": [12345],
                "image": [Image.new("RGB", (512, 512))]
            }
        )

        outputs_callback = Mock()

        # Call process
        result = generator_pipe.process(pipe_input, outputs_callback)

        # Verify img2img was called
        assert mock_model.img2img.called

    def test_validates_conditioning_early(self, generator_pipe, mock_model):
        """Test that pipe validates conditioning early and fails fast."""
        # Create conditioning WITHOUT required attributes
        # Use spec=[] to prevent Mock from auto-creating attributes
        bad_conditioning = Mock(spec=[])
        # This Mock will NOT have p_prompt_embeds, n_prompt_embeds, etc.

        pipe_input = PipeInput(
            input={
                "model": mock_model,
                "conditioning": [bad_conditioning],
                "seed": [12345]
            }
        )

        outputs_callback = Mock()

        # Should raise ValueError due to validation
        with pytest.raises(ValueError, match="Conditioning missing required attributes"):
            generator_pipe.process(pipe_input, outputs_callback)

    def test_validates_missing_conditioning(self, generator_pipe, mock_model):
        """Test that pipe validates when conditioning is completely missing."""
        pipe_input = PipeInput(
            input={
                "model": mock_model,
                "conditioning": [],  # Empty list
                "seed": [12345]
            }
        )

        outputs_callback = Mock()

        # Should raise ValueError for missing conditioning
        with pytest.raises(ValueError, match="Conditioning is required"):
            generator_pipe.process(pipe_input, outputs_callback)

    def test_output_structure(self, generator_pipe, mock_model, mock_conditioning):
        """Test that output has correct structure."""
        pipe_input = PipeInput(
            input={
                "model": mock_model,
                "conditioning": [mock_conditioning],
                "seed": [12345]
            }
        )

        outputs_callback = Mock()

        # Call process
        result = generator_pipe.process(pipe_input, outputs_callback)

        # Verify output structure
        assert "image" in result.output
        assert isinstance(result.output["image"], list)

    def test_handles_controlnet(self, generator_pipe, mock_model, mock_conditioning):
        """Test that pipe handles ControlNet correctly."""
        # Create mock ControlNet data
        mock_controlnet = {
            'model': Mock(),
            'conditioning_scale': 0.7,
            'control_guidance_start': 0.0,
            'control_guidance_end': 1.0
        }

        pipe_input = PipeInput(
            input={
                "model": mock_model,
                "conditioning": [mock_conditioning],
                "seed": [12345],
                "controlnet": [mock_controlnet],
                "control_image": [Image.new("RGB", (512, 512))]
            }
        )

        outputs_callback = Mock()

        # Call process
        result = generator_pipe.process(pipe_input, outputs_callback)

        # Verify ControlNet methods were called
        assert mock_model.load_with_controlnet.called
        assert mock_model.txt2img_controlnet.called


class TestBuildGenerationInput:
    """Test helper methods for building GenerationInput."""

    @pytest.fixture
    def generator_pipe(self):
        config = GeneratorSDXLPipe.get_default_config()
        pipe = GeneratorSDXLPipe(config)
        return pipe

    @pytest.fixture
    def mock_conditioning(self):
        """Create mock conditioning."""
        import torch
        conditioning = Mock()
        conditioning.embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        conditioning.n_embeds = {"embeds": torch.randn(1, 77, 2048), "pooled": torch.randn(1, 1280)}
        return conditioning

    def test_build_generation_input_txt2img(self, generator_pipe, mock_conditioning):
        """Test building GenerationInput for txt2img."""
        result = generator_pipe._build_generation_input_txt2img(
            idx=0,
            seed=12345,
            conditioning=[mock_conditioning],
            quantity=1
        )

        # Verify structure
        assert isinstance(result, GenerationInput)
        assert result[IOType.SEED] == 12345
        assert result[IOType.SAMPLER] == "DPMPP_2M"
        assert result[IOType.SCHEDULER] == "karras"
        assert result[IOType.CFG] == 6.0
        assert result[IOType.STEP] == 25

    def test_build_generation_input_img2img(self, generator_pipe, mock_conditioning):
        """Test building GenerationInput for img2img."""
        image = Image.new("RGB", (512, 512))
        mask = Image.new("L", (512, 512))

        result = generator_pipe._build_generation_input_img2img(
            image=image,
            mask=mask,
            width=512,
            height=512,
            seeds=[12345],
            conditioning=[mock_conditioning]
        )

        # Verify structure
        assert isinstance(result, GenerationInput)
        assert result[IOType.SEED] == 12345
        assert result[IOType.IMAGE] == image
        assert result[IOType.MASK] == mask
        assert result[IOType.RESOLUTION] == [512, 512]
        assert result[IOType.DENOISE] == 0.8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
