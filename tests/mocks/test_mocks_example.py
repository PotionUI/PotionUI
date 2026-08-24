"""
Example test file demonstrating how to use the mock utilities.

This file shows how to use each mock fixture in your tests.
These are working examples that can be copied and adapted for real tests.
"""

import pytest
from PIL import Image


class TestGpuMocks:
    """Examples of using GPU mocks"""

    def test_mock_gpu_basic(self, mock_gpu):
        """Test that GPU operations are mocked"""
        # GPU manager returns CPU device
        assert mock_gpu.get_free_vram() == 0
        assert mock_gpu.get_used_vram() == 0
        assert mock_gpu.get_total_vram() == 0
        assert mock_gpu.get_temperature() == 0

    def test_mock_torch_cuda(self, mock_torch_cuda):
        """Test that PyTorch CUDA is mocked"""
        import torch
        # CUDA should report as unavailable
        assert not torch.cuda.is_available()
        assert torch.cuda.device_count() == 0


class TestModelMocks:
    """Examples of using model mocks"""

    def test_mock_model_loader(self, mock_model_loader):
        """Test that model loading is mocked"""
        # Mock model loader returns a fake model
        assert mock_model_loader is not None
        assert mock_model_loader.device == 'cpu'
        assert mock_model_loader.dtype == 'float32'

    def test_mock_model_manager(self, mock_model_manager):
        """ModelManager owns the models directory layout, not downloads."""
        from pathlib import Path

        assert mock_model_manager.get_model_dir("checkpoint") == Path("/fake/models/checkpoint")
        assert mock_model_manager.base_path == Path("/fake/models")


class TestPipeMocks:
    """Examples of using pipe mocks"""

    def test_fake_image(self, fake_image):
        """Test that fake_image fixture works"""
        assert isinstance(fake_image, Image.Image)
        assert fake_image.size == (512, 512)
        assert fake_image.mode == 'RGB'

    def test_mock_generator_pipe(self, mock_generator_pipe):
        """Test that generator pipe is mocked"""
        from src.pipelines.contracts import PipeInput

        # Create a fake pipe input
        pipe_input = PipeInput(input={})
        outputs = []

        def capture_output(output):
            outputs.append(output)

        # This would normally do heavy inference, but is now mocked
        # The mock is already applied via the fixture
        # In real usage, you would call the actual GeneratorPipe.process()
        # and it would use the mock implementation
        assert True  # Mock is applied at import/patch level

    def test_mock_seed_generator_pipe(self, mock_seed_generator_pipe):
        """Test that seed generator pipe is mocked"""
        # Mock is applied and will return predictable seeds
        # When GeneratorPipe.process() is called, seeds will be 1000, 1001, etc.
        assert True  # Mock is applied at import/patch level


class TestExternalAPIMocks:
    """Examples of using external API mocks"""

    def test_mock_civitai(self, mock_civitai):
        """Test that Civitai API is mocked"""
        # Mock Civitai returns fake model data
        assert mock_civitai['id'] == 12345
        assert mock_civitai['name'] == 'Test Model'
        assert mock_civitai['type'] == 'Checkpoint'
        assert len(mock_civitai['modelVersions']) > 0

    def test_mock_huggingface(self, mock_huggingface):
        """Test that HuggingFace API is mocked"""
        # Mock HuggingFace returns fake paths
        assert mock_huggingface == '/fake/huggingface/models/test_model.safetensors'

    def test_mock_requests_get(self, mock_requests_get):
        """Test that HTTP requests are mocked"""
        import requests

        # Test image URL
        response = requests.get("https://example.com/image.png")
        assert response.status_code == 200
        assert 'image' in response.headers['content-type']

        # Test JSON API
        response = requests.get("https://api.example.com/data.json")
        assert response.status_code == 200
        assert 'json' in response.headers['content-type']

        # Test generic file
        response = requests.get("https://example.com/file.dat")
        assert response.status_code == 200
        assert response.content == b'fake file content for testing'


class TestCombinedMocks:
    """Examples of using multiple mocks together"""

    def test_multiple_mocks(self, mock_gpu, mock_model_loader, mock_civitai):
        """Test using multiple mocks in one test"""
        # All mocks are active simultaneously
        assert mock_gpu.get_free_vram() == 0
        assert mock_model_loader.device == 'cpu'
        assert mock_civitai['name'] == 'Test Model'

    def test_all_external_apis(self, mock_all_external_apis):
        """Test using the convenience fixture for all external APIs"""
        import requests

        # All external APIs are mocked
        response = requests.get("https://example.com/test")
        assert response.status_code == 200

    def test_all_pipes(self, mock_all_pipes):
        """Test using the convenience fixture for all pipes"""
        # All pipes are mocked
        # Your pipeline tests can run without actual model inference
        assert True


# Example integration test using mocks
class TestIntegrationWithMocks:
    """Example of a more realistic integration test"""

    def test_generation_flow_mocked(
        self,
        mock_gpu,
        mock_model_loader,
        mock_generator_pipe,
        mock_seed_generator_pipe,
        fake_image
    ):
        """
        Test a complete generation flow with all heavy operations mocked.

        This test demonstrates how to test complex generation logic
        without actually running GPU inference or loading models.
        """
        # All expensive operations are mocked:
        # - GPU operations return fake values
        # - Model loading returns mock objects
        # - Generator pipe returns fake images
        # - Seed generation returns predictable values

        # Your test logic here would call the generation pipeline
        # and verify the results using the mocked components

        assert isinstance(fake_image, Image.Image)
        assert mock_model_loader.device == 'cpu'
        assert mock_gpu.get_free_vram() == 0
