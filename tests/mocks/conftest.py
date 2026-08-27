"""
Pytest configuration for mock fixtures.

This file registers all mock fixtures so they can be used in tests.
Pytest automatically discovers conftest.py files and makes their fixtures available.
"""

# Import all fixtures to make them available to pytest
from tests.mocks.gpu_mock import (
    mock_gpu,
    mock_torch_cuda,
    mock_nvml,
    mock_device_cpu
)
from tests.mocks.model_mock import (
    mock_model_loader,
    mock_model_directories,
    mock_model_indexer,
    mock_diffusers_pipeline,
    mock_safetensors,
    mock_upscaler_model
)
from tests.mocks.pipe_mock import (
    fake_image,
    mock_generator_pipe,
    mock_upscaler_pipe,
    mock_seed_generator_pipe,
    mock_prompt_encoder_pipe,
    mock_gallery_pipe,
    mock_all_pipes
)
from tests.mocks.external_api_mock import (
    mock_civitai,
    mock_huggingface,
    mock_requests_get,
    mock_requests_post,
    mock_aiohttp_session,
    mock_all_external_apis
)

# Explicitly tell pytest which fixtures are available
__all__ = [
    # GPU mocks
    'mock_gpu',
    'mock_torch_cuda',
    'mock_nvml',
    'mock_device_cpu',

    # Model mocks
    'mock_model_loader',
    'mock_model_directories',
    'mock_model_indexer',
    'mock_diffusers_pipeline',
    'mock_safetensors',
    'mock_upscaler_model',

    # Pipe mocks
    'fake_image',
    'mock_generator_pipe',
    'mock_upscaler_pipe',
    'mock_seed_generator_pipe',
    'mock_prompt_encoder_pipe',
    'mock_gallery_pipe',
    'mock_all_pipes',

    # External API mocks
    'mock_civitai',
    'mock_huggingface',
    'mock_requests_get',
    'mock_requests_post',
    'mock_aiohttp_session',
    'mock_all_external_apis',
]
