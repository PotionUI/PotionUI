"""
Model loading mocks for testing without downloading large model files.

These mocks replace heavy model loading operations with lightweight fakes
that return mock model objects suitable for testing.
"""

import pytest
from unittest.mock import patch, MagicMock, Mock
from pathlib import Path
from typing import Optional


@pytest.fixture
def mock_model_loader():
    """
    Mock heavy model loading operations.

    This fixture patches model loading functions to return fake model objects
    instead of actually loading multi-gigabyte model files from disk.

    The fake models are MagicMock objects that can be called like real models
    but don't perform actual inference.

    Usage:
        def test_generation(mock_model_loader):
            # Models will be fake objects, not real loaded models
            model = load_checkpoint("some_model.safetensors")
            # model is a MagicMock, not a real diffusion model
    """
    fake_model = MagicMock()
    fake_model.device = 'cpu'
    fake_model.dtype = 'float32'

    # Mock the model's forward/call method to return fake output
    fake_output = MagicMock()
    fake_output.images = []
    fake_model.return_value = fake_output
    fake_model.__call__ = MagicMock(return_value=fake_output)

    patches = [
        patch('diffusers.DiffusionPipeline.from_pretrained', return_value=fake_model),
        patch('torch.load', return_value={'state_dict': {}})
    ]

    # Try to patch from_single_file if it exists (not all diffusers versions have it)
    try:
        from diffusers import DiffusionPipeline
        if hasattr(DiffusionPipeline, 'from_single_file'):
            patches.append(patch('diffusers.DiffusionPipeline.from_single_file', return_value=fake_model))
    except:
        pass

    # Apply all patches
    with patches[0], patches[1]:
        if len(patches) > 2:
            with patches[2]:
                yield fake_model
        else:
            yield fake_model


@pytest.fixture
def mock_model_manager():
    """
    Mock the ModelManager to avoid actual model operations.

    This fixture creates a mock ModelManager that:
    - Returns fake model directories
    - Skips filesystem creation

    ModelManager no longer downloads models; downloads go through the core download
    queue, which authenticates via the provider registry.

    Usage:
        def test_preset_loading(mock_model_manager):
            # ModelManager operations are mocked
            manager = ModelManager("/fake/path")
            # All operations succeed without actual file I/O
    """
    mock_manager = Mock()

    mock_manager.get_model_dir = Mock(side_effect=lambda model_type: Path(f"/fake/models/{model_type}"))
    mock_manager.create_model_dirs = Mock()
    mock_manager.base_path = Path("/fake/models")

    with patch('src.features.models.directory.ModelManager', return_value=mock_manager):
        yield mock_manager


@pytest.fixture
def mock_model_indexer():
    """
    Mock the ModelIndexer to avoid filesystem scanning and hashing.

    This fixture creates a mock ModelIndexer that:
    - Returns empty index without scanning filesystem
    - Skips SHA256 hash calculations
    - Provides fake model metadata

    Usage:
        def test_model_discovery(mock_model_indexer):
            # ModelIndexer operations are mocked
            indexer = ModelIndexer(model_manager)
            indexer.index_models()  # Does nothing
    """
    from src.features.models.directory import ModelIndex

    mock_indexer = Mock()
    mock_indexer.index = {}
    mock_indexer.name_to_hash = {}

    # Mock index_models to do nothing
    mock_indexer.index_models = Mock()

    # Mock get_models_by_type to return empty list
    mock_indexer.get_models_by_type = Mock(return_value=[])

    # Mock get_model_by_sha256 to return None
    mock_indexer.get_model_by_sha256 = Mock(return_value=None)

    # Mock verify_model_integrity to always return True
    mock_indexer.verify_model_integrity = Mock(return_value=True)

    with patch('src.features.models.directory.ModelIndexer', return_value=mock_indexer):
        yield mock_indexer


@pytest.fixture
def mock_diffusers_pipeline():
    """
    Mock diffusers pipeline loading from HuggingFace.

    This fixture patches the diffusers library's pipeline loading
    to return mock pipelines instead of downloading from HuggingFace.

    Usage:
        def test_flux_model(mock_diffusers_pipeline):
            # Pipeline loading is mocked
            pipe = FluxPipeline.from_pretrained("black-forest-labs/FLUX.1-dev")
            # pipe is a mock, no download occurs
    """
    fake_pipe = MagicMock()
    fake_pipe.device = 'cpu'
    fake_pipe.unet = MagicMock()
    fake_pipe.vae = MagicMock()
    fake_pipe.text_encoder = MagicMock()
    fake_pipe.tokenizer = MagicMock()

    # Mock the pipeline call to return fake images
    from PIL import Image
    fake_output = MagicMock()
    fake_output.images = [Image.new('RGB', (512, 512), color='blue')]
    fake_pipe.return_value = fake_output
    fake_pipe.__call__ = MagicMock(return_value=fake_output)

    with patch('diffusers.FluxPipeline.from_pretrained', return_value=fake_pipe), \
         patch('diffusers.StableDiffusionPipeline.from_pretrained', return_value=fake_pipe), \
         patch('diffusers.StableDiffusionXLPipeline.from_pretrained', return_value=fake_pipe):
        yield fake_pipe


@pytest.fixture
def mock_safetensors():
    """
    Mock safetensors loading to avoid loading large checkpoint files.

    This fixture patches safetensors.torch.load_file to return
    empty state dicts instead of loading actual model weights.

    Usage:
        def test_checkpoint_loading(mock_safetensors):
            # Safetensors loading is mocked
            state_dict = load_file("model.safetensors")
            # state_dict is empty, no actual file loaded
    """
    with patch('safetensors.torch.load_file', return_value={}), \
         patch('safetensors.torch.save_file'):
        yield


@pytest.fixture
def mock_upscaler_model():
    """
    Mock upscaler model loading (RealESRGAN, etc.).

    This fixture patches upscaler model initialization to return
    mock models that don't perform actual upscaling.

    Usage:
        def test_upscaling(mock_upscaler_model):
            # Upscaler model is mocked
            upscaler = RealESRGAN()
            # upscaler.enhance() returns input image unchanged
    """
    fake_upscaler = MagicMock()

    # Mock enhance method to return the input image
    def fake_enhance(image, outscale=2):
        return image

    fake_upscaler.enhance = Mock(side_effect=fake_enhance)

    with patch('basicsr.archs.rrdbnet_arch.RRDBNet', return_value=MagicMock()), \
         patch('realesrgan.RealESRGANer', return_value=fake_upscaler):
        yield fake_upscaler
