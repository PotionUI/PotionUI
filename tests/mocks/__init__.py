"""
Mock utilities for testing the PotionUI project.

This package provides pytest fixtures and mock objects for:
- GPU operations (running tests on CPU)
- Model loading (avoiding heavy model downloads)
- Generation pipes (returning fake outputs)
- External APIs (Civitai, HuggingFace)
"""

from tests.mocks.gpu_mock import mock_gpu
from tests.mocks.model_mock import mock_model_loader, mock_model_manager
from tests.mocks.pipe_mock import (
    mock_generator_pipe,
    mock_upscaler_pipe,
    mock_seed_generator_pipe,
    fake_image
)
from tests.mocks.external_api_mock import (
    mock_civitai,
    mock_huggingface,
    mock_requests_get
)

__all__ = [
    # GPU mocks
    'mock_gpu',

    # Model mocks
    'mock_model_loader',
    'mock_model_manager',

    # Pipe mocks
    'mock_generator_pipe',
    'mock_upscaler_pipe',
    'mock_seed_generator_pipe',
    'fake_image',

    # External API mocks
    'mock_civitai',
    'mock_huggingface',
    'mock_requests_get',
]
