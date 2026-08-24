"""
Tests for Maya model loader pipe.

This test suite covers:
- Pipe metadata and configuration
- Input/output specifications
- Model loading workflow
- Model caching behavior
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import torch

from src.pipelines.pipes.model_loader.maya.main import ModelLoaderMayaPipe
from src.pipelines.pipes._shared.models.maya.maya_model import MayaModel
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.models import BaseModel
from src.pipelines.outputs import (
    ProgressGenerationOutput,
    ModelGenerationOutput,
    ModelsGenerationOutput,
)


def _fake_assets(depot: Path) -> Mock:
    """An ASSETS service that mirrors a repo id to a real local directory.

    Creating the directory matters: the pipe hands the result to `MayaModel`,
    which refuses anything that is not an existing directory - that refusal is
    what keeps a repo id from reaching `from_pretrained` and being fetched
    outside the download manager.
    """
    def _ensure(repo_id, *, subdir, **kwargs):
        target = depot / subdir
        target.mkdir(parents=True, exist_ok=True)
        return target

    assets = Mock()
    assets.ensure_asset_repo = Mock(side_effect=_ensure)
    return assets


class TestModelLoaderMayaPipe:
    """Tests for Maya model loader pipe."""

    def test_pipe_metadata(self):
        """Test pipe has correct metadata."""
        assert ModelLoaderMayaPipe.name == "model_loader"
        assert "Maya" in ModelLoaderMayaPipe.description
        assert "text-to-speech" in ModelLoaderMayaPipe.description.lower()

    def test_default_config(self):
        """Test default configuration."""
        config = ModelLoaderMayaPipe.get_default_config()

        assert config["model_id"] == "maya-research/maya1"
        assert config["snac_model"] == "hubertsiuzdak/snac_24khz"
        assert config["device"] == "cuda"
        assert config["dtype"] == "bfloat16"

    def test_configuration_specs(self):
        """Test configuration specifications."""
        specs = ModelLoaderMayaPipe.configuration()

        spec_names = [spec.name for spec in specs]
        assert "model_id" in spec_names
        assert "snac_model" in spec_names
        assert "device" in spec_names
        assert "dtype" in spec_names

        # Check device choices
        device_spec = next(s for s in specs if s.name == "device")
        assert device_spec.choices == ["cuda", "cpu"]

        # Check dtype choices
        dtype_spec = next(s for s in specs if s.name == "dtype")
        assert "bfloat16" in dtype_spec.choices
        assert "float16" in dtype_spec.choices
        assert "float32" in dtype_spec.choices

    def test_inputs_specs(self):
        """Test input specifications."""
        inputs = ModelLoaderMayaPipe.inputs()

        input_names = [inp.name for inp in inputs]
        assert "MODELS" in input_names

        models_input = next(inp for inp in inputs if inp.name == "MODELS")
        assert models_input.io_type == IOType.SERVICE
        assert models_input.required is False
        assert models_input.is_array is False

    def test_outputs_specs(self):
        """Test output specifications."""
        outputs = ModelLoaderMayaPipe.outputs()

        output_names = [out.name for out in outputs]
        assert "model" in output_names

        model_output = next(out for out in outputs if out.name == "model")
        assert model_output.io_type == IOType.MODEL
        assert model_output.is_array is False

    @patch('src.pipelines.pipes.model_loader.maya.main.MayaModel')
    def test_process_loads_new_model(self, mock_maya_model_class, tmp_path):
        """Test loading a new Maya model."""
        # Setup mock
        mock_model = Mock()
        mock_maya_model_class.return_value = mock_model

        # Create pipe
        config = ModelLoaderMayaPipe.get_default_config()
        pipe = ModelLoaderMayaPipe(config=config)

        # Create input with no cached model
        assets = _fake_assets(tmp_path)
        pipe_input = PipeInput(input={"ASSETS": assets})

        # Track emitted outputs
        emitted_outputs = []

        def mock_callback(output):
            emitted_outputs.append(output)

        # Process
        result = pipe.process(pipe_input, mock_callback)

        # Verify MayaModel was created with correct config. The two repo ids
        # arrive as local directories: the pipe mirrors them through ASSETS so
        # `from_pretrained` never sees a repo id and never fetches anything
        # itself, outside the download manager.
        mock_maya_model_class.assert_called_once()
        call_kwargs = mock_maya_model_class.call_args[1]
        assert call_kwargs["config"]["model_id"] == str(tmp_path / "tts/maya-research-maya1")
        assert call_kwargs["config"]["snac_model"] == str(
            tmp_path / "tts/hubertsiuzdak-snac-24khz"
        )
        assert call_kwargs["config"]["device"] == "cuda"
        assert call_kwargs["config"]["dtype"] == "bfloat16"

        assert [call.args[0] for call in assets.ensure_asset_repo.call_args_list] == [
            "maya-research/maya1",
            "hubertsiuzdak/snac_24khz",
        ]

        # Verify model.load was called
        mock_model.load.assert_called_once_with(mode="txt2speech")

        # Verify output
        assert "model" in result.output
        assert result.output["model"] == mock_model

        # Verify progress output was emitted
        progress_outputs = [o for o in emitted_outputs if isinstance(o, ProgressGenerationOutput)]
        assert len(progress_outputs) >= 1

        # Verify model info was emitted
        models_outputs = [o for o in emitted_outputs if isinstance(o, ModelsGenerationOutput)]
        assert len(models_outputs) == 1
        assert len(models_outputs[0].models) == 2  # Maya model + SNAC

    @patch('src.pipelines.pipes.model_loader.maya.main.MayaModel')
    def test_process_uses_cached_model(self, mock_maya_model_class):
        """Test that a MODELS-service hit skips loading a new model."""
        mock_cached_model = Mock()

        # Fake MODELS service: fingerprint match -> return cached value
        # without invoking the loader (mirrors ModelLifecycleManager.acquire).
        fake_models = Mock()
        fake_models.acquire = Mock(return_value=mock_cached_model)

        config = ModelLoaderMayaPipe.get_default_config()
        pipe = ModelLoaderMayaPipe(config=config)

        pipe_input = PipeInput(input={"MODELS": fake_models})

        result = pipe.process(pipe_input, Mock())

        # Verify no new model was created directly (acquire() owns that)
        mock_maya_model_class.assert_not_called()
        fake_models.acquire.assert_called_once()
        assert fake_models.acquire.call_args.kwargs["key"] == "model_loader/maya"

        # Verify cached model was returned
        assert result.output["model"] == mock_cached_model

    @patch('src.pipelines.pipes.model_loader.maya.main.MayaModel')
    def test_process_replaces_cached_model(self, mock_maya_model_class, tmp_path):
        """Test that a MODELS-service miss (fingerprint changed) loads a new model."""
        mock_new_model = Mock()
        mock_maya_model_class.return_value = mock_new_model

        # Fake MODELS service: miss -> calls the loader and returns its result
        # (mirrors ModelLifecycleManager.acquire on a fingerprint change).
        fake_models = Mock()
        fake_models.acquire = Mock(side_effect=lambda key, fingerprint, loader: loader())

        config = ModelLoaderMayaPipe.get_default_config()
        pipe = ModelLoaderMayaPipe(config=config)

        pipe_input = PipeInput(input={"MODELS": fake_models, "ASSETS": _fake_assets(tmp_path)})

        result = pipe.process(pipe_input, Mock())

        # Verify new model was created and loaded
        mock_maya_model_class.assert_called_once()
        mock_new_model.load.assert_called_once_with(mode="txt2speech")

        # Verify new model was returned
        assert result.output["model"] == mock_new_model


class TestMayaModel:
    """Tests for MayaModel wrapper class."""

    def test_initialization(self):
        """Test MayaModel initialization."""
        template = {"base": BaseModel.MAYA}
        config = {
            "model_id": "maya-research/maya1",
            "snac_model": "hubertsiuzdak/snac_24khz",
            "device": "cuda",
            "dtype": "bfloat16"
        }

        model = MayaModel(template=template, config=config)

        assert model.template == template
        assert model.config == config
        assert model.device == "cuda"
        assert model.dtype_str == "bfloat16"
        assert model.dtype == torch.bfloat16
        assert model.model is None  # Not loaded yet
        assert model.tokenizer is None
        assert model.snac is None

    def test_dtype_mapping(self):
        """Test dtype string to torch dtype mapping."""
        template = {"base": BaseModel.MAYA}

        # Test bfloat16
        config = {"device": "cuda", "dtype": "bfloat16"}
        model = MayaModel(template=template, config=config)
        assert model.dtype == torch.bfloat16

        # Test float16
        config = {"device": "cuda", "dtype": "float16"}
        model = MayaModel(template=template, config=config)
        assert model.dtype == torch.float16

        # Test float32
        config = {"device": "cuda", "dtype": "float32"}
        model = MayaModel(template=template, config=config)
        assert model.dtype == torch.float32

    def test_load_invalid_mode(self):
        """Test that invalid mode raises error."""
        template = {"base": BaseModel.MAYA}
        config = {"device": "cuda", "dtype": "bfloat16"}

        model = MayaModel(template=template, config=config)

        with pytest.raises(ValueError) as exc_info:
            model.load(mode="invalid_mode")

        assert "Unsupported mode" in str(exc_info.value)

    @patch('torch.cuda.is_available', return_value=True)
    @patch('torch.cuda.get_device_properties')
    def test_load_txt2speech(
        self,
        mock_gpu_props,
        mock_cuda_available,
        tmp_path
    ):
        """Test loading model for txt2speech mode."""
        import sys

        # Setup GPU properties mock
        mock_gpu_props.return_value.total_memory = 24 * 1024**3  # 24GB VRAM

        # Create mock modules
        mock_transformers = MagicMock()
        mock_snac_module = MagicMock()

        mock_model = Mock()
        mock_transformers.AutoModelForCausalLM.from_pretrained.return_value = mock_model

        mock_tokenizer = Mock()
        mock_transformers.AutoTokenizer.from_pretrained.return_value = mock_tokenizer

        mock_snac_model = Mock()
        mock_snac_model.eval.return_value = mock_snac_model
        mock_snac_model.to.return_value = mock_snac_model
        mock_snac_module.SNAC.from_pretrained.return_value = mock_snac_model

        # Patch the imports
        with patch.dict(sys.modules, {
            'transformers': mock_transformers,
            'snac': mock_snac_module
        }):
            # Create and load model
            # Local directories, not repo ids: `model_loader/maya` mirrors the
            # repos through ASSETS and passes the resulting paths, so every
            # `from_pretrained` below loads from disk.
            maya_dir = tmp_path / "maya"
            snac_dir = tmp_path / "snac"
            maya_dir.mkdir()
            snac_dir.mkdir()

            template = {"base": BaseModel.MAYA}
            config = {
                "model_id": str(maya_dir),
                "snac_model": str(snac_dir),
                "device": "cuda",
                "dtype": "bfloat16"
            }

            model = MayaModel(template=template, config=config)
            model.load(mode="txt2speech")

            # Verify model was loaded
            mock_transformers.AutoModelForCausalLM.from_pretrained.assert_called_once()
            call_kwargs = mock_transformers.AutoModelForCausalLM.from_pretrained.call_args
            assert call_kwargs[0][0] == str(maya_dir)

            # Verify tokenizer was loaded
            mock_transformers.AutoTokenizer.from_pretrained.assert_called_once_with(str(maya_dir))

            # Verify SNAC was loaded
            mock_snac_module.SNAC.from_pretrained.assert_called_once_with(str(snac_dir))

            # Verify components are set
            assert model.model is not None
            assert model.tokenizer is not None
            assert model.snac is not None

    def test_unload(self):
        """Test unloading model components."""
        template = {"base": BaseModel.MAYA}
        config = {"device": "cuda", "dtype": "bfloat16"}

        model = MayaModel(template=template, config=config)

        # Set mock components
        model.model = Mock()
        model.tokenizer = Mock()
        model.snac = Mock()

        # Unload
        with patch('torch.cuda.is_available', return_value=True), \
             patch('torch.cuda.empty_cache'), \
             patch('torch.cuda.synchronize'):
            model.unload()

        # Verify components are cleared
        assert model.model is None
        assert model.tokenizer is None
        assert model.snac is None

    def test_get_code_end_token_id(self):
        """Test getting end-of-audio token ID."""
        from src.pipelines.pipes._shared.models.maya.maya_model import CODE_END_TOKEN_ID, CODE_START_TOKEN_ID

        template = {"base": BaseModel.MAYA}
        config = {"device": "cuda", "dtype": "bfloat16"}

        model = MayaModel(template=template, config=config)

        # Maya always returns the Maya-specific CODE_END_TOKEN_ID
        assert model.get_code_end_token_id() == CODE_END_TOKEN_ID
        assert model.get_code_end_token_id() == 128258

        # Also test get_code_start_token_id
        assert model.get_code_start_token_id() == CODE_START_TOKEN_ID
        assert model.get_code_start_token_id() == 128257

        # Test get_snac_token_range
        min_id, max_id = model.get_snac_token_range()
        assert min_id == 128266
        assert max_id == 156937

        # Test get_code_token_offset
        assert model.get_code_token_offset() == 128266
