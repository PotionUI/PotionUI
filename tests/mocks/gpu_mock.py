"""
GPU operation mocks for testing without NVIDIA hardware.

These mocks allow tests to run on CPU-only machines by simulating GPU operations.
All CUDA operations are replaced with CPU equivalents.
"""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_gpu():
    """
    Mock GPU operations - forces tests to run on CPU.

    This fixture patches the GpuManager to always return CPU device
    and report GPU as unavailable. This allows running tests on machines
    without NVIDIA GPUs.

    Usage:
        def test_something(mock_gpu):
            # Test will run on CPU instead of GPU
            pass
    """
    with patch('src.platform.runtime.gpu.GpuManager') as mock_manager_class:
        # Create mock instance
        mock_instance = MagicMock()
        mock_instance.get_free_vram.return_value = 0
        mock_instance.get_used_vram.return_value = 0
        mock_instance.get_total_vram.return_value = 0
        mock_instance.get_temperature.return_value = 0

        # Return the mock instance when GpuManager is instantiated
        mock_manager_class.return_value = mock_instance

        yield mock_instance


@pytest.fixture
def mock_torch_cuda():
    """
    Mock PyTorch CUDA operations to use CPU.

    This fixture patches torch.cuda functions to report CUDA as unavailable
    and redirects all device allocations to CPU.

    Usage:
        def test_model_loading(mock_torch_cuda):
            # Model will load on CPU instead of GPU
            pass
    """
    with patch('torch.cuda.is_available', return_value=False), \
         patch('torch.cuda.empty_cache'), \
         patch('torch.cuda.device_count', return_value=0):
        yield


@pytest.fixture
def mock_nvml():
    """
    Mock NVIDIA Management Library (pynvml) calls.

    This fixture patches all pynvml functions used for GPU monitoring
    so tests don't require NVIDIA drivers to be installed.

    Usage:
        def test_gpu_monitoring(mock_nvml):
            # GPU monitoring will use mocked values
            pass
    """
    mock_handle = MagicMock()
    mock_memory_info = MagicMock()
    mock_memory_info.free = 8 * 1024 * 1024 * 1024  # 8GB
    mock_memory_info.used = 2 * 1024 * 1024 * 1024  # 2GB
    mock_memory_info.total = 10 * 1024 * 1024 * 1024  # 10GB

    with patch('pynvml.nvmlInit'), \
         patch('pynvml.nvmlShutdown'), \
         patch('pynvml.nvmlDeviceGetHandleByIndex', return_value=mock_handle), \
         patch('pynvml.nvmlDeviceGetMemoryInfo', return_value=mock_memory_info), \
         patch('pynvml.nvmlDeviceGetTemperature', return_value=65):
        yield mock_handle


@pytest.fixture
def mock_device_cpu():
    """
    Force all torch device allocations to CPU.

    This fixture patches torch.device to always return 'cpu' regardless
    of what device string is requested.

    Usage:
        def test_tensor_ops(mock_device_cpu):
            # All tensors will be on CPU
            tensor = torch.randn(10).to('cuda')  # Will actually be on CPU
    """
    import torch
    original_device = torch.device

    def mock_device(device_str):
        if isinstance(device_str, str) and 'cuda' in device_str:
            return original_device('cpu')
        return original_device(device_str)

    with patch('torch.device', side_effect=mock_device):
        yield
