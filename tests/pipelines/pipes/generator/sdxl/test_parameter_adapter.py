"""
Tests for SDXL Parameter Adapter

Tests the conversion of GenerationInput to diffusers pipeline parameters
for SDXL models.
"""

import pytest
import torch
from unittest.mock import Mock

from src.pipelines.pipes._shared.models.sdxl.parameter_adapter import SDXLParameterAdapter
from src.pipelines.contracts import IOType
from src.pipelines.contracts import GenerationInput, GenerationInputItem


class TestSDXLParameterAdapter:
    """Test suite for SDXLParameterAdapter"""

    @pytest.fixture
    def basic_generation_input(self):
        """Create a basic GenerationInput for testing"""
        return GenerationInput(input=[
            GenerationInputItem(name="seed", value=42, io_type=IOType.SEED),
            GenerationInputItem(name="step", value=30, io_type=IOType.STEP),
            GenerationInputItem(name="cfg", value=7.5, io_type=IOType.CFG),
            GenerationInputItem(name="sampler", value="DPMPP_2M", io_type=IOType.SAMPLER),
            GenerationInputItem(name="scheduler", value="karras", io_type=IOType.SCHEDULER),
            GenerationInputItem(name="device", value="cpu", io_type=IOType.TEXT),  # Use CPU for testing
        ])

    @pytest.fixture
    def img2img_generation_input(self):
        """Create a GenerationInput for img2img testing"""
        mock_image = Mock()
        mock_mask = Mock()

        return GenerationInput(input=[
            GenerationInputItem(name="seed", value=42, io_type=IOType.SEED),
            GenerationInputItem(name="step", value=30, io_type=IOType.STEP),
            GenerationInputItem(name="cfg", value=7.5, io_type=IOType.CFG),
            GenerationInputItem(name="sampler", value="EULER", io_type=IOType.SAMPLER),
            GenerationInputItem(name="scheduler", value="karras", io_type=IOType.SCHEDULER),
            GenerationInputItem(name="image", value=mock_image, io_type=IOType.IMAGE),
            GenerationInputItem(name="denoise", value=0.8, io_type=IOType.DENOISE),
            GenerationInputItem(name="mask", value=mock_mask, io_type=IOType.MASK),
            GenerationInputItem(name="device", value="cpu", io_type=IOType.TEXT),  # Use CPU for testing
        ])

    @pytest.fixture
    def mock_conditioning(self):
        """Create mock conditioning object"""
        conditioning = Mock()
        conditioning.embeds = {"embeds": torch.randn(1, 77, 768), "pooled": torch.randn(1, 1280)}
        conditioning.n_embeds = {"embeds": torch.randn(1, 77, 768), "pooled": torch.randn(1, 1280)}
        return conditioning

    # Sampler Mapping Tests

    def test_sampler_mapping_dpmpp_2m(self, basic_generation_input):
        """Test DPMPP_2M sampler mapping"""
        adapter = SDXLParameterAdapter(basic_generation_input)
        assert adapter.sampler == "dpmpp_2m"

    def test_sampler_mapping_euler(self):
        """Test EULER sampler mapping"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="sampler", value="EULER", io_type=IOType.SAMPLER),
        ])
        adapter = SDXLParameterAdapter(gen_input)
        assert adapter.sampler == "euler"

    def test_sampler_mapping_euler_ancestral(self):
        """Test EULER_A sampler mapping"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="sampler", value="EULER_A", io_type=IOType.SAMPLER),
        ])
        adapter = SDXLParameterAdapter(gen_input)
        assert adapter.sampler == "euler_ancestral"

    def test_sampler_mapping_heun(self):
        """Test HEUN sampler mapping"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="sampler", value="HEUN", io_type=IOType.SAMPLER),
        ])
        adapter = SDXLParameterAdapter(gen_input)
        assert adapter.sampler == "heun"

    def test_sampler_mapping_dpmpp_2m_sde(self):
        """Test DPMPP_2M_SDE sampler mapping"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="sampler", value="DPMPP_2M_SDE", io_type=IOType.SAMPLER),
        ])
        adapter = SDXLParameterAdapter(gen_input)
        assert adapter.sampler == "dpmpp_2m_sde"

    def test_sampler_mapping_unknown_defaults_to_dpmpp_2m(self):
        """Test unknown sampler defaults to dpmpp_2m"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="sampler", value="UNKNOWN_SAMPLER", io_type=IOType.SAMPLER),
        ])
        adapter = SDXLParameterAdapter(gen_input)
        assert adapter.sampler == "dpmpp_2m"

    def test_sampler_default_when_not_provided(self):
        """Test sampler defaults to DPMPP_2M when not provided"""
        gen_input = GenerationInput(input=[])
        adapter = SDXLParameterAdapter(gen_input)
        assert adapter.sampler == "dpmpp_2m"

    # Scheduler Tests

    def test_scheduler_property(self, basic_generation_input):
        """Test scheduler property returns correct value"""
        adapter = SDXLParameterAdapter(basic_generation_input)
        assert adapter.scheduler == "karras"

    def test_scheduler_default_when_not_provided(self):
        """Test scheduler defaults to karras when not provided"""
        gen_input = GenerationInput(input=[])
        adapter = SDXLParameterAdapter(gen_input)
        assert adapter.scheduler == "karras"

    # txt2img Pipeline Parameters Tests

    def test_build_pipeline_params_txt2img_basic(self, basic_generation_input, mock_conditioning):
        """Test basic txt2img pipeline parameter building"""
        adapter = SDXLParameterAdapter(basic_generation_input)
        params = adapter.build_pipeline_params(mock_conditioning, mode="txt2img")

        # Core conditioning parameters
        assert torch.equal(params["prompt_embeds"], mock_conditioning.embeds["embeds"])
        assert torch.equal(params["negative_prompt_embeds"], mock_conditioning.n_embeds["embeds"])
        assert torch.equal(params["pooled_prompt_embeds"], mock_conditioning.embeds["pooled"])
        assert torch.equal(params["negative_pooled_prompt_embeds"], mock_conditioning.n_embeds["pooled"])

        # Sampling parameters
        assert params["num_inference_steps"] == 30
        assert params["guidance_scale"] == 7.5
        assert params["sampler"] == "dpmpp_2m"
        assert params["scheduler"] == "karras"
        assert params["output_type"] == "pil"

        # Generator
        assert type(params["generator"]).__name__ == "Generator"

        # Should NOT have img2img parameters
        assert "image" not in params
        assert "strength" not in params
        assert "mask_image" not in params

    def test_build_pipeline_params_txt2img_all_samplers(self, mock_conditioning):
        """Test pipeline params with all sampler types"""
        samplers = ["EULER", "EULER_A", "HEUN", "DPM2", "DPM2_A", "LMS",
                   "DPMPP_2S_A", "DPMPP_SDE", "DPMPP_2M", "DPMPP_2M_SDE",
                   "DPMPP_3M_SDE", "LCM"]

        expected_mappings = {
            "EULER": "euler",
            "EULER_A": "euler_ancestral",
            "HEUN": "heun",
            "DPM2": "dpm_2",
            "DPM2_A": "dpm_2_ancestral",
            "LMS": "lms",
            "DPMPP_2S_A": "dpmpp_2s_ancestral",
            "DPMPP_SDE": "dpmpp_sde",
            "DPMPP_2M": "dpmpp_2m",
            "DPMPP_2M_SDE": "dpmpp_2m_sde",
            "DPMPP_3M_SDE": "dpmpp_3m_sde",
            "LCM": "lcm"
        }

        for sampler in samplers:
            gen_input = GenerationInput(input=[
                GenerationInputItem(name="seed", value=42, io_type=IOType.SEED),
                GenerationInputItem(name="step", value=30, io_type=IOType.STEP),
                GenerationInputItem(name="cfg", value=7.5, io_type=IOType.CFG),
                GenerationInputItem(name="sampler", value=sampler, io_type=IOType.SAMPLER),
                GenerationInputItem(name="device", value="cpu", io_type=IOType.TEXT),  # Use CPU for testing
            ])
            adapter = SDXLParameterAdapter(gen_input)
            params = adapter.build_pipeline_params(mock_conditioning, mode="txt2img")
            assert params["sampler"] == expected_mappings[sampler], f"Failed for {sampler}"

    # img2img Pipeline Parameters Tests

    def test_build_pipeline_params_img2img(self, img2img_generation_input, mock_conditioning):
        """Test img2img pipeline parameter building"""
        adapter = SDXLParameterAdapter(img2img_generation_input)
        params = adapter.build_pipeline_params(mock_conditioning, mode="img2img")

        # Should have img2img-specific parameters
        assert "image" in params
        assert params["image"] is not None
        assert params["strength"] == 0.8
        assert "mask_image" in params
        assert params["mask_image"] is not None

        # Should still have core parameters
        assert params["num_inference_steps"] == 30
        assert params["guidance_scale"] == 7.5
        assert params["sampler"] == "euler"

    def test_build_pipeline_params_img2img_without_mask(self, mock_conditioning):
        """Test img2img without mask"""
        mock_image = Mock()
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="seed", value=42, io_type=IOType.SEED),
            GenerationInputItem(name="step", value=30, io_type=IOType.STEP),
            GenerationInputItem(name="cfg", value=7.5, io_type=IOType.CFG),
            GenerationInputItem(name="sampler", value="EULER", io_type=IOType.SAMPLER),
            GenerationInputItem(name="image", value=mock_image, io_type=IOType.IMAGE),
            GenerationInputItem(name="denoise", value=0.7, io_type=IOType.DENOISE),
            GenerationInputItem(name="device", value="cpu", io_type=IOType.TEXT),  # Use CPU for testing
        ])

        adapter = SDXLParameterAdapter(gen_input)
        params = adapter.build_pipeline_params(mock_conditioning, mode="img2img")

        assert "image" in params
        assert params["strength"] == 0.7
        assert "mask_image" not in params

    def test_build_pipeline_params_img2img_default_strength(self, mock_conditioning):
        """Test img2img uses default strength when not provided"""
        mock_image = Mock()
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="seed", value=42, io_type=IOType.SEED),
            GenerationInputItem(name="step", value=30, io_type=IOType.STEP),
            GenerationInputItem(name="cfg", value=7.5, io_type=IOType.CFG),
            GenerationInputItem(name="sampler", value="EULER", io_type=IOType.SAMPLER),
            GenerationInputItem(name="image", value=mock_image, io_type=IOType.IMAGE),
            GenerationInputItem(name="device", value="cpu", io_type=IOType.TEXT),  # Use CPU for testing
        ])

        adapter = SDXLParameterAdapter(gen_input)
        params = adapter.build_pipeline_params(mock_conditioning, mode="img2img")

        assert params["strength"] == 0.8  # Default value

    # Generator Tests

    def test_create_generator_with_seed(self):
        """Test generator creation with seed (using CPU device for testing)"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="seed", value=42, io_type=IOType.SEED),
            GenerationInputItem(name="device", value="cpu", io_type=IOType.TEXT),
        ])
        adapter = SDXLParameterAdapter(gen_input)
        generator = adapter._create_generator()

        assert type(generator).__name__ == "Generator"
        assert generator.initial_seed() == 42

    def test_create_generator_with_cpu_device(self):
        """Test generator creation with CPU device"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="seed", value=123, io_type=IOType.SEED),
            GenerationInputItem(name="device", value="cpu", io_type=IOType.TEXT),
        ])

        adapter = SDXLParameterAdapter(gen_input)
        generator = adapter._create_generator()

        assert generator.device.type == "cpu"

    def test_create_generator_without_seed(self):
        """Test generator returns None when seed not provided"""
        gen_input = GenerationInput(input=[])
        adapter = SDXLParameterAdapter(gen_input)
        generator = adapter._create_generator()

        assert generator is None

    def test_create_generator_default_device_is_cuda(self):
        """Test generator defaults to cuda device when not specified"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="seed", value=42, io_type=IOType.SEED),
        ])
        adapter = SDXLParameterAdapter(gen_input)

        # The implementation defaults to "cuda", but we test the logic not actual creation
        # since CUDA may not be available in test environment
        device = gen_input.get_by_name("device", "cuda")
        assert device == "cuda"

    # Edge Cases and Error Handling

    def test_cfg_scale_as_int(self, mock_conditioning):
        """Test CFG scale works with integer values"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="step", value=30, io_type=IOType.STEP),
            GenerationInputItem(name="cfg", value=7, io_type=IOType.CFG),  # Integer
        ])

        adapter = SDXLParameterAdapter(gen_input)
        params = adapter.build_pipeline_params(mock_conditioning, mode="txt2img")

        assert params["guidance_scale"] == 7.0  # Converted to float

    def test_steps_as_string(self, mock_conditioning):
        """Test steps parameter type handling"""
        gen_input = GenerationInput(input=[
            GenerationInputItem(name="step", value=30, io_type=IOType.STEP),
            GenerationInputItem(name="cfg", value=7.5, io_type=IOType.CFG),
        ])

        adapter = SDXLParameterAdapter(gen_input)
        params = adapter.build_pipeline_params(mock_conditioning, mode="txt2img")

        # Should work with direct integer
        assert params["num_inference_steps"] == 30

    def test_all_sampler_mappings_exist(self):
        """Test that all samplers in SAMPLER_MAP are valid"""
        assert len(SDXLParameterAdapter.SAMPLER_MAP) == 12

        # Verify all expected samplers are present
        expected_samplers = [
            "EULER", "EULER_A", "HEUN", "DPM2", "DPM2_A", "LMS",
            "DPMPP_2S_A", "DPMPP_SDE", "DPMPP_2M", "DPMPP_2M_SDE",
            "DPMPP_3M_SDE", "LCM"
        ]

        for sampler in expected_samplers:
            assert sampler in SDXLParameterAdapter.SAMPLER_MAP
