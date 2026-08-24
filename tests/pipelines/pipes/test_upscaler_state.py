"""
Test upscaler state management across multiple generations.
This test verifies the fix for black images on second+ generations.
"""
import pytest
import torch
import numpy as np
from PIL import Image
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.pipelines.pipes.upscaler.main import Upscaler, ImageUpscaler, tiled_scale
from src.pipelines.contracts import PipeInput


@pytest.fixture
def mock_upscaler_model():
    """Create a mock upscaler model that simulates ESRGAN behavior."""
    class MockESRGAN(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.scale = 4

        def forward(self, x):
            # Simulate 4x upscaling with actual processing (not just resize)
            b, c, h, w = x.shape
            # Add some variation to detect state pollution
            output = torch.nn.functional.interpolate(
                x, scale_factor=4, mode='bilinear', align_corners=False
            )
            # Add small noise to make output unique per call
            output = output + torch.randn_like(output) * 0.01
            return output.clamp(0, 1)

        def eval(self):
            return self

        def to(self, device):
            return self

    return MockESRGAN()


@pytest.fixture
def test_image():
    """Create a simple test image."""
    # Create a 128x128 RGB image with a gradient pattern
    img_array = np.zeros((128, 128, 3), dtype=np.uint8)
    for i in range(128):
        for j in range(128):
            img_array[i, j] = [i % 256, j % 256, (i + j) % 256]
    return Image.fromarray(img_array)


def test_tiled_scale_multiple_calls(mock_upscaler_model):
    """Test that tiled_scale produces non-black images on multiple consecutive calls."""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # Create test input tensor
    input_tensor = torch.rand(1, 3, 128, 128, device=device)

    results = []
    for i in range(3):
        # Call tiled_scale multiple times
        output = tiled_scale(
            input_tensor,
            mock_upscaler_model,
            user_scale=2.0,
            tile_w=64,
            tile_h=64,
            overlap=8
        )

        # Verify output is not all zeros (black)
        assert output is not None, f"Generation {i+1}: Output is None"
        assert output.shape[0] == 1, f"Generation {i+1}: Wrong batch size"
        assert output.shape[1] == 3, f"Generation {i+1}: Wrong number of channels"

        # Check that output has non-zero values
        output_mean = output.mean().item()
        output_max = output.max().item()
        output_min = output.min().item()

        assert output_max > 0.01, f"Generation {i+1}: Output is all black (max={output_max})"
        assert output_mean > 0.001, f"Generation {i+1}: Output mean too low (mean={output_mean})"

        # Store results for comparison
        results.append({
            'mean': output_mean,
            'max': output_max,
            'min': output_min,
            'tensor': output.clone()
        })

        print(f"Generation {i+1}: mean={output_mean:.6f}, max={output_max:.6f}, min={output_min:.6f}")

    # Verify all generations produced reasonable outputs
    for i, result in enumerate(results):
        assert result['max'] > 0.01, f"Generation {i+1} failed: black image"
        assert result['mean'] > 0.001, f"Generation {i+1} failed: near-black image"


@patch('vendor.chainner_pfn.RRDB.RRDBNet')
@patch('src.pipelines.pipes.upscaler.main.torch.load')
def test_image_upscaler_multiple_generations(mock_torch_load, mock_esrgan_class, mock_upscaler_model, test_image):
    """Test ImageUpscaler.upscale() with multiple consecutive calls."""
    # Setup mocks
    mock_torch_load.return_value = {'test_key': 'test_value'}
    mock_esrgan_class.return_value = mock_upscaler_model

    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    # Create upscaler
    upscaler = ImageUpscaler(
        model_path='test_model.pth',
        device=device,
        user_scale=2.0,
        tile_size=64,
        tile_pad=8
    )

    results = []
    for i in range(3):
        # Upscale the same image multiple times
        output_image = upscaler.upscale(test_image)

        # Verify output is valid
        assert output_image is not None, f"Generation {i+1}: Output is None"
        assert isinstance(output_image, Image.Image), f"Generation {i+1}: Output is not PIL Image"

        # Convert to numpy to check values
        output_array = np.array(output_image)

        # Check that output is not all black
        mean_value = output_array.mean()
        max_value = output_array.max()
        min_value = output_array.min()

        assert max_value > 10, f"Generation {i+1}: Image is all black (max={max_value})"
        assert mean_value > 1, f"Generation {i+1}: Image mean too low (mean={mean_value})"

        results.append({
            'mean': mean_value,
            'max': max_value,
            'min': min_value,
            'array': output_array.copy()
        })

        print(f"Generation {i+1}: mean={mean_value:.2f}, max={max_value}, min={min_value}")

    # Verify all generations produced non-black images
    for i, result in enumerate(results):
        assert result['max'] > 10, f"Generation {i+1} failed: black image"
        assert result['mean'] > 1, f"Generation {i+1} failed: near-black image"


@patch('vendor.chainner_pfn.RRDB.RRDBNet')
@patch('src.pipelines.pipes.upscaler.main.torch.load')
def test_upscaler_pipe_multiple_generations(mock_torch_load, mock_esrgan_class, mock_upscaler_model, test_image):
    """Test Upscaler pipe with multiple consecutive generations."""
    # Setup mocks
    mock_torch_load.return_value = {'test_key': 'test_value'}
    mock_esrgan_class.return_value = mock_upscaler_model

    # Create pipe config
    config = {
        'model': 'test_model.pth',
        'scale': 2.0,
        'tile_size': 64,
        'tile_padding': 8
    }

    upscaler_pipe = Upscaler(config)

    results = []
    for i in range(3):
        # Mock generation outputs callback
        outputs_received = []
        def mock_generation_outputs(output):
            outputs_received.append(output)

        # Create pipe input
        pipe_input = PipeInput(input={'image': [test_image]})

        # Process
        result = upscaler_pipe.process(pipe_input, mock_generation_outputs)

        # Verify result
        assert result is not None, f"Generation {i+1}: Result is None"
        assert 'image' in result.output, f"Generation {i+1}: No image in output"
        assert len(result.output['image']) > 0, f"Generation {i+1}: Empty image list"

        output_image = result.output['image'][0]
        output_array = np.array(output_image)

        # Check that output is not all black
        mean_value = output_array.mean()
        max_value = output_array.max()

        assert max_value > 10, f"Generation {i+1}: Image is all black (max={max_value})"
        assert mean_value > 1, f"Generation {i+1}: Image mean too low (mean={mean_value})"

        results.append({
            'mean': mean_value,
            'max': max_value,
            'array': output_array.copy()
        })

        print(f"Generation {i+1}: mean={mean_value:.2f}, max={max_value}")

    # Verify all generations produced non-black images
    for i, result in enumerate(results):
        assert result['max'] > 10, f"Generation {i+1} failed: black image"
        assert result['mean'] > 1, f"Generation {i+1} failed: near-black image"


def test_buffer_isolation():
    """Test that output buffers are properly isolated between calls."""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # Create two different input tensors with distinct patterns
    input1 = torch.ones(1, 3, 64, 64, device=device) * 0.3
    input2 = torch.ones(1, 3, 64, 64, device=device) * 0.7

    # Create a simple mock model
    class SimpleModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.scale = 2

        def forward(self, x):
            return torch.nn.functional.interpolate(x, scale_factor=2, mode='bilinear')

        def eval(self):
            return self

        def to(self, device):
            return self

    model = SimpleModel()

    # Process first input
    output1 = tiled_scale(input1, model, user_scale=2.0, tile_w=32, tile_h=32, overlap=4)
    mean1 = output1.mean().item()

    # Process second input
    output2 = tiled_scale(input2, model, user_scale=2.0, tile_w=32, tile_h=32, overlap=4)
    mean2 = output2.mean().item()

    # Outputs should reflect the different inputs
    assert abs(mean1 - 0.3) < 0.1, f"First output doesn't match input (expected ~0.3, got {mean1})"
    assert abs(mean2 - 0.7) < 0.1, f"Second output doesn't match input (expected ~0.7, got {mean2})"
    assert abs(mean1 - mean2) > 0.2, "Outputs are too similar - possible buffer pollution"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
