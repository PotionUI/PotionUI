# Mock Utilities for PotionUI Testing

This directory contains comprehensive mock utilities for testing the PotionUI project without requiring GPU hardware, large model downloads, or external API calls.

## Overview

The mock utilities are organized into four main categories:

1. **GPU Mocks** (`gpu_mock.py`) - Run tests on CPU instead of GPU
2. **Model Mocks** (`model_mock.py`) - Avoid loading large model files
3. **Pipe Mocks** (`pipe_mock.py`) - Skip heavy inference operations
4. **External API Mocks** (`external_api_mock.py`) - Avoid network calls

## Quick Start

### Using Individual Mocks

Import and use mocks as pytest fixtures:

```python
def test_my_feature(mock_gpu, mock_model_loader):
    """Test runs on CPU with fake models"""
    # Your test code here
    # GPU operations are mocked
    # Model loading is mocked
    pass
```

### Available GPU Mocks

```python
def test_with_cpu(mock_gpu):
    """GPU operations return fake values"""
    assert mock_gpu.get_free_vram() == 0

def test_torch_cpu(mock_torch_cuda):
    """PyTorch CUDA reports as unavailable"""
    import torch
    assert not torch.cuda.is_available()
```

### Available Model Mocks

```python
def test_model_loading(mock_model_loader):
    """Model loading returns fake models"""
    # mock_model_loader is a MagicMock
    # Actual model loading is bypassed
    pass

def test_model_manager(mock_model_manager):
    """ModelManager operations are mocked"""
    # All operations succeed without file I/O
    assert mock_model_manager.is_model_installed(model_info)
```

### Available Pipe Mocks

```python
def test_with_fake_image(fake_image):
    """Use a simple test image"""
    assert fake_image.size == (512, 512)

def test_generation(mock_generator_pipe):
    """Generator returns fake images instantly"""
    # Generation pipe is mocked
    pass

def test_upscaling(mock_upscaler_pipe):
    """Upscaler resizes without ML inference"""
    # Upscaling is instant
    pass

def test_seeds(mock_seed_generator_pipe):
    """Seeds are predictable for testing"""
    # Seeds start at 1000, 1001, 1002...
    pass
```

### Available External API Mocks

```python
def test_civitai(mock_civitai):
    """Civitai API returns fake model data"""
    assert mock_civitai['name'] == 'Test Model'

def test_huggingface(mock_huggingface):
    """HuggingFace returns fake file paths"""
    # No actual downloads occur
    pass

def test_http(mock_requests_get):
    """HTTP requests are mocked"""
    import requests
    response = requests.get("https://example.com/file")
    assert response.status_code == 200
```

### Convenience Fixtures

Use these to apply multiple mocks at once:

```python
def test_full_pipeline(mock_all_pipes):
    """All pipes are mocked"""
    # Generator, upscaler, seed gen, etc. all mocked
    pass

def test_offline(mock_all_external_apis):
    """All external APIs are mocked"""
    # No network calls will be made
    pass
```

## Complete Fixture List

### GPU Mocks
- `mock_gpu` - Mock GpuManager for CPU-only testing
- `mock_torch_cuda` - Mock PyTorch CUDA operations
- `mock_nvml` - Mock NVIDIA Management Library
- `mock_device_cpu` - Force all torch tensors to CPU

### Model Mocks
- `mock_model_loader` - Mock heavy model loading
- `mock_model_manager` - Mock ModelManager operations
- `mock_diffusers_pipeline` - Mock diffusers pipeline loading
- `mock_safetensors` - Mock safetensors file operations
- `mock_upscaler_model` - Mock upscaler model loading

### Pipe Mocks
- `fake_image` - Create a simple test image (512x512 red)
- `mock_generator_pipe` - Mock image generation
- `mock_upscaler_pipe` - Mock image upscaling
- `mock_seed_generator_pipe` - Mock seed generation
- `mock_prompt_encoder_pipe` - Mock CLIP encoding
- `mock_gallery_pipe` - Mock gallery operations
- `mock_all_pipes` - Apply all pipe mocks at once

### External API Mocks
- `mock_civitai` - Mock Civitai API calls
- `mock_huggingface` - Mock HuggingFace Hub calls
- `mock_requests_get` - Mock HTTP GET requests
- `mock_requests_post` - Mock HTTP POST requests
- `mock_aiohttp_session` - Mock async HTTP requests
- `mock_all_external_apis` - Apply all API mocks at once

## Integration with Existing Tests

The mock fixtures are automatically available to all tests in the project thanks to `conftest.py`. Simply add them as parameters to your test functions:

```python
# tests/my_feature/test_feature.py

def test_my_feature(mock_gpu, mock_model_loader, mock_civitai):
    """Test runs completely offline on CPU"""
    # All expensive operations are mocked
    result = my_feature_function()
    assert result.success
```

## Best Practices

### 1. Use Appropriate Mocks
Only mock what you need. If your test doesn't use the GPU, you don't need `mock_gpu`.

```python
# Good - only mocks what's needed
def test_preset_loading(mock_model_manager):
    preset = load_preset("flux")
    assert preset.name == "flux"

# Overkill - mocks too much
def test_preset_loading(mock_gpu, mock_model_loader, mock_all_pipes):
    preset = load_preset("flux")
    assert preset.name == "flux"
```

### 2. Combine Related Mocks
Use convenience fixtures when testing complex workflows:

```python
def test_full_generation_pipeline(mock_all_pipes, mock_all_external_apis):
    """Test complete pipeline without GPU or network"""
    result = generate_image(prompt="test")
    assert result.success
```

### 3. Verify Mock Behavior
Test that mocks are actually being used:

```python
def test_gpu_mock_is_active(mock_gpu):
    """Verify GPU operations return fake values"""
    assert mock_gpu.get_free_vram() == 0  # Would be > 0 on real GPU
```

### 4. Document Mock Usage
Explain why mocks are needed in your test docstrings:

```python
def test_model_loading(mock_model_loader):
    """
    Test model loading logic without downloading 4GB models.

    Uses mock_model_loader to bypass actual model downloads
    and inference, allowing test to run in < 1 second.
    """
    pass
```

## Adding New Mocks

To add a new mock fixture:

1. Add the fixture function to the appropriate file:
   - GPU-related → `gpu_mock.py`
   - Model-related → `model_mock.py`
   - Pipe-related → `pipe_mock.py`
   - API-related → `external_api_mock.py`

2. Import it in `conftest.py`:
```python
from tests.mocks.my_file import my_new_fixture
```

3. Add to `__all__` in both files:
```python
__all__ = [
    'existing_fixture',
    'my_new_fixture',  # Add here
]
```

4. Document it in this README

## Troubleshooting

### Fixture Not Found
If pytest can't find your fixture:
1. Check it's imported in `conftest.py`
2. Verify the fixture has `@pytest.fixture` decorator
3. Ensure `conftest.py` is in the same directory

### Mock Not Working
If your mock isn't being applied:
1. Check the patch path is correct
2. Verify the module/class exists
3. Ensure you're using the fixture in your test
4. Check for import order issues

### Tests Still Slow
If tests are still slow despite mocks:
1. Verify mocks are actually being used (add assertions)
2. Check for operations that aren't mocked
3. Profile the test to find bottlenecks
4. Consider adding more specific mocks

## Performance Benefits

With proper mocking, tests should see:
- **99% faster** - No GPU inference or model loading
- **100% offline** - No network calls required
- **No hardware requirements** - Runs on any machine
- **Deterministic** - Predictable outputs for reliable tests

## Examples

See `test_mocks_example.py` for working examples of all mock fixtures.

## License

These mocks are part of the PotionUI project and follow the same license.
