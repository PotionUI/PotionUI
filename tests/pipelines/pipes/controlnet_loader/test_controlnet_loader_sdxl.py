import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from src.pipelines.pipes.controlnet_loader.sdxl.main import ControlNetLoaderSDXLPipe
from src.pipelines.contracts import PipeInput, IOType


class TestControlNetLoaderSDXLPipe:
    """Tests for SDXL ControlNet loader pipe"""

    def test_pipe_metadata(self):
        """Test pipe has correct metadata"""
        assert ControlNetLoaderSDXLPipe.name == "controlnet_loader"
        assert "ControlNet" in ControlNetLoaderSDXLPipe.description

    def test_default_config(self):
        """Test default configuration"""
        config = ControlNetLoaderSDXLPipe.get_default_config()

        assert config["controlnets"] == []
        assert config["device"] == "cuda"
        assert config["dtype"] == "float16"

    def test_configuration_specs(self):
        """Test configuration specifications"""
        specs = ControlNetLoaderSDXLPipe.configuration()

        # Check required specs are present
        spec_names = [spec.name for spec in specs]
        assert "controlnets" in spec_names
        assert "device" in spec_names
        assert "dtype" in spec_names

    def test_inputs_specs(self):
        """Test input specifications"""
        inputs = ControlNetLoaderSDXLPipe.inputs()

        # ControlNet caching now goes through the MODELS lifecycle service
        # instead of a dedicated "controlnet" cache input.
        input_names = [inp.name for inp in inputs]
        assert "MODELS" in input_names

        models_input = next(inp for inp in inputs if inp.name == "MODELS")
        assert models_input.required == False
        assert models_input.io_type == IOType.SERVICE

    def test_outputs_specs(self):
        """Test output specifications"""
        outputs = ControlNetLoaderSDXLPipe.outputs()

        # Check outputs
        output_names = [out.name for out in outputs]
        assert "controlnet" in output_names

        # Check that output is array
        controlnet_output = next(out for out in outputs if out.name == "controlnet")
        assert controlnet_output.is_array == True

    def test_process_no_controlnets_configured(self):
        """Test that process returns empty list when no controlnets configured"""
        config = {"controlnets": [], "device": "cuda", "dtype": "float16"}
        pipe = ControlNetLoaderSDXLPipe(config)

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        # Execute
        result = pipe.process(pipe_input, generation_outputs)

        # Verify
        assert result.output["controlnet"] == []

    def test_process_uses_cached_controlnets(self):
        """Test that a MODELS-service hit is returned without reloading"""
        config = {
            "controlnets": [{"file_path": "/path/to/controlnet.safetensors", "enabled": True}],
            "device": "cuda",
            "dtype": "float16",
        }
        pipe = ControlNetLoaderSDXLPipe(config)

        cached_controlnets = [{"model": MagicMock(), "name": "test_cn", "type": "canny"}]
        fake_models = Mock()
        fake_models.acquire = Mock(return_value=cached_controlnets)

        pipe_input = PipeInput(input={"MODELS": fake_models})
        generation_outputs = Mock()

        result = pipe.process(pipe_input, generation_outputs)

        fake_models.acquire.assert_called_once()
        assert fake_models.acquire.call_args.kwargs["key"] == "controlnet_loader/sdxl"
        assert result.output["controlnet"] == cached_controlnets

    @patch('diffusers.ControlNetModel')
    @patch('src.pipelines.pipes.controlnet_loader.sdxl.main.Path')
    def test_process_loads_controlnet(self, mock_path, mock_controlnet_model):
        """Test that process loads a ControlNet model"""
        # Setup
        controlnet_path = "/path/to/controlnet.safetensors"

        # Mock Path.exists to return True
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path.return_value = mock_path_instance

        config = {
            "controlnets": [
                {
                    "file_path": controlnet_path,
                    "name": "test_controlnet",
                    "type": "canny",
                    "conditioning_scale": 1.0,
                    "control_guidance_start": 0.0,
                    "control_guidance_end": 1.0,
                    "enabled": True
                }
            ],
            "device": "cuda",
            "dtype": "float16"
        }
        pipe = ControlNetLoaderSDXLPipe(config)

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        # Mock ControlNet model
        mock_cn_instance = MagicMock()
        mock_controlnet_model.from_single_file.return_value = mock_cn_instance
        mock_cn_instance.to.return_value = mock_cn_instance

        # Execute
        result = pipe.process(pipe_input, generation_outputs)

        # Verify
        assert len(result.output["controlnet"]) == 1
        assert result.output["controlnet"][0]["name"] == "test_controlnet"
        assert result.output["controlnet"][0]["type"] == "canny"
        assert result.output["controlnet"][0]["conditioning_scale"] == 1.0

    def test_process_skips_disabled_controlnets(self):
        """Test that process skips controlnets with enabled=False"""
        config = {
            "controlnets": [
                {
                    "file_path": "/path/to/controlnet.safetensors",
                    "name": "disabled_cn",
                    "type": "canny",
                    "conditioning_scale": 1.0,
                    "enabled": False  # Disabled
                }
            ],
            "device": "cuda",
            "dtype": "float16"
        }
        pipe = ControlNetLoaderSDXLPipe(config)

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        # Execute
        result = pipe.process(pipe_input, generation_outputs)

        # Verify - should return empty list
        assert result.output["controlnet"] == []

    @patch('src.pipelines.pipes.controlnet_loader.sdxl.main.Path')
    def test_process_skips_missing_files(self, mock_path):
        """Test that process skips controlnets with missing files"""
        # Mock Path.exists to return False
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        mock_path.return_value = mock_path_instance

        config = {
            "controlnets": [
                {
                    "file_path": "/path/to/missing.safetensors",
                    "name": "missing_cn",
                    "type": "canny",
                    "conditioning_scale": 1.0,
                    "enabled": True
                }
            ],
            "device": "cuda",
            "dtype": "float16"
        }
        pipe = ControlNetLoaderSDXLPipe(config)

        pipe_input = PipeInput(input={})
        generation_outputs = Mock()

        # Execute
        result = pipe.process(pipe_input, generation_outputs)

        # Verify - should return empty list
        assert result.output["controlnet"] == []
