"""
Tests for SDXL InpaintHead model and integration.

This module tests the InpaintHead convolutional model used for Fooocus-style inpainting,
including model loading, input preparation, and feature injection.
"""

import inspect
import pytest
import torch
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from src.pipelines.pipes.generator.sdxl.inpaint_head import (
    INPAINT_HEAD_FILENAME,
    INPAINT_HEAD_SUBDIR,
    InpaintHead,
    InpaintHeadLoader,
    inpaint_head_path,
    prepare_inpaint_head_input
)


class TestInpaintHead:
    """Tests for the InpaintHead neural network module."""

    def test_initialization(self):
        """Test that InpaintHead initializes with correct shape."""
        model = InpaintHead()

        # Check that head parameter exists and has correct shape
        assert hasattr(model, 'head')
        assert model.head.shape == (320, 5, 3, 3)
        assert model.head.device.type == 'cpu'

    def test_forward_pass_shape(self):
        """Test that forward pass produces correct output shape."""
        model = InpaintHead()
        # Initialize with random weights for testing
        torch.nn.init.normal_(model.head, mean=0, std=0.01)

        # Create 5-channel input (batch=1, channels=5, height=64, width=64)
        x = torch.randn(1, 5, 64, 64)

        # Forward pass
        output = model(x)

        # Check output shape: (batch, 320, height, width)
        assert output.shape == (1, 320, 64, 64)

    def test_forward_pass_padding(self):
        """Test that padding is applied correctly (replicate mode)."""
        model = InpaintHead()
        torch.nn.init.normal_(model.head, mean=0, std=0.01)

        # Create small input to easily verify padding behavior
        x = torch.ones(1, 5, 4, 4)

        # The forward pass should apply replicate padding (1 pixel on each side)
        # Then apply 3x3 convolution
        # Output height/width should be same as input (4x4) due to padding
        output = model(x)

        assert output.shape == (1, 320, 4, 4)

    def test_forward_pass_batch_size(self):
        """Test that model handles different batch sizes."""
        model = InpaintHead()
        torch.nn.init.normal_(model.head, mean=0, std=0.01)

        for batch_size in [1, 2, 4]:
            x = torch.randn(batch_size, 5, 64, 64)
            output = model(x)
            assert output.shape == (batch_size, 320, 64, 64)

    def test_forward_pass_different_resolutions(self):
        """Test that model works with different input resolutions."""
        model = InpaintHead()
        torch.nn.init.normal_(model.head, mean=0, std=0.01)

        # Test various SDXL-compatible resolutions
        resolutions = [(64, 64), (96, 64), (64, 96), (128, 128)]

        for h, w in resolutions:
            x = torch.randn(1, 5, h, w)
            output = model(x)
            assert output.shape == (1, 320, h, w)

    def test_device_movement(self):
        """Test that model can be moved to different devices."""
        model = InpaintHead()

        # Test CPU
        model = model.to('cpu')
        assert model.head.device.type == 'cpu'

        # Test CUDA if available
        if torch.cuda.is_available():
            model = model.to('cuda')
            assert model.head.device.type == 'cuda'

            x = torch.randn(1, 5, 64, 64).cuda()
            output = model(x)
            assert output.device.type == 'cuda'

    def test_dtype_conversion(self):
        """Test that model supports different data types."""
        model = InpaintHead()

        for dtype in [torch.float32, torch.float16]:
            model_typed = model.to(dtype=dtype)
            assert model_typed.head.dtype == dtype

            x = torch.randn(1, 5, 64, 64, dtype=dtype)
            output = model_typed(x)
            assert output.dtype == dtype


class TestInpaintHeadLoader:
    """Tests for the InpaintHeadLoader class."""

    def test_load_inpaint_head_file_not_found(self):
        """A missing file is an error, never a download: the loader only loads."""
        with pytest.raises(FileNotFoundError) as exc_info:
            InpaintHeadLoader.load_inpaint_head("/nonexistent/path/model.pth")

        assert "InpaintHead model not found" in str(exc_info.value)
        assert "huggingface.co/lllyasviel/fooocus_inpaint" in str(exc_info.value)

    @patch('os.path.exists')
    @patch('torch.load')
    def test_load_inpaint_head_success(self, mock_torch_load, mock_exists):
        """Test successful model loading."""
        # Clear cache before test
        InpaintHeadLoader.clear_cache()

        # Mock file existence
        mock_exists.return_value = True

        # Mock state dict
        mock_state_dict = {
            'head': torch.randn(320, 5, 3, 3)
        }
        mock_torch_load.return_value = mock_state_dict

        # Load model
        model = InpaintHeadLoader.load_inpaint_head("/fake/path/model.pth")

        # Verify model was loaded
        assert isinstance(model, InpaintHead)
        assert mock_torch_load.called
        assert mock_torch_load.call_args[1]['map_location'] == 'cpu'
        assert mock_torch_load.call_args[1]['weights_only'] is True

    @patch('os.path.exists')
    @patch('torch.load')
    def test_load_inpaint_head_caching(self, mock_torch_load, mock_exists):
        """Test that loader caches models and returns cached instance."""
        # Clear cache before test
        InpaintHeadLoader.clear_cache()

        mock_exists.return_value = True
        mock_state_dict = {'head': torch.randn(320, 5, 3, 3)}
        mock_torch_load.return_value = mock_state_dict

        # Load model twice with same path
        model1 = InpaintHeadLoader.load_inpaint_head("/fake/path/model.pth")
        model2 = InpaintHeadLoader.load_inpaint_head("/fake/path/model.pth")

        # Should return same instance
        assert model1 is model2

        # torch.load should only be called once (cached on second call)
        assert mock_torch_load.call_count == 1

    @patch('os.path.exists')
    @patch('torch.load')
    def test_load_inpaint_head_different_paths(self, mock_torch_load, mock_exists):
        """Test that different paths load different models."""
        # Clear cache before test
        InpaintHeadLoader.clear_cache()

        mock_exists.return_value = True
        mock_state_dict = {'head': torch.randn(320, 5, 3, 3)}
        mock_torch_load.return_value = mock_state_dict

        # Load models with different paths
        model1 = InpaintHeadLoader.load_inpaint_head("/fake/path1/model.pth")
        model2 = InpaintHeadLoader.load_inpaint_head("/fake/path2/model.pth")

        # Should load twice (different paths)
        assert mock_torch_load.call_count == 2

    def test_clear_cache(self):
        """Test that cache clearing works."""
        # Set dummy cache
        InpaintHeadLoader._instance = Mock()
        InpaintHeadLoader._loaded_path = "/some/path"

        # Clear cache
        InpaintHeadLoader.clear_cache()

        # Verify cache is cleared
        assert InpaintHeadLoader._instance is None
        assert InpaintHeadLoader._loaded_path is None

    @patch('os.path.exists')
    @patch('torch.load')
    def test_load_inpaint_head_runtime_error(self, mock_torch_load, mock_exists):
        """Test that loading failures raise RuntimeError."""
        InpaintHeadLoader.clear_cache()

        mock_exists.return_value = True
        mock_torch_load.side_effect = Exception("Loading failed")

        with pytest.raises(RuntimeError) as exc_info:
            InpaintHeadLoader.load_inpaint_head("/fake/path/model.pth")

        assert "Failed to load InpaintHead model" in str(exc_info.value)

    def test_loader_holds_no_download_path_at_all(self):
        """The loader used to fetch the weights itself with `requests.get`,
        outside the download manager - no history, no depot containment, no
        progress. `generator/sdxl` now fetches through the ASSETS service
        before generation, so nothing here may reach the network."""
        import src.pipelines.pipes.generator.sdxl.inpaint_head as module

        assert not hasattr(InpaintHeadLoader, "_download_model")
        assert not hasattr(module, "requests")
        assert "auto_download" not in inspect.signature(
            InpaintHeadLoader.load_inpaint_head
        ).parameters


class TestInpaintHeadPath:
    """The fetch destination and the load path derive from one place."""

    def test_path_is_under_the_given_depot(self):
        path = inpaint_head_path("/depot")

        assert path == Path("/depot/inpaint/fooocus_inpaint_head.pth")

    def test_path_components_match_the_fetch_coordinates(self):
        """`generator/sdxl` fetches with (subdir, filename) and the k-diffusion
        pipeline loads from `inpaint_head_path`; if these disagreed, every
        masked generation would fetch to one path and load from another."""
        path = inpaint_head_path("/depot")

        assert path.name == INPAINT_HEAD_FILENAME
        assert path.parent.name == INPAINT_HEAD_SUBDIR

    def test_relative_depot_is_resolved_to_an_absolute_path(self):
        assert inpaint_head_path("models").is_absolute()


class TestPrepareInpaintHeadInput:
    """Tests for the prepare_inpaint_head_input helper function."""

    def test_basic_concatenation(self):
        """Test basic mask + latent concatenation."""
        mask = torch.ones(1, 1, 64, 64)
        latents = torch.randn(1, 4, 64, 64)

        result = prepare_inpaint_head_input(mask, latents)

        # Should be 5 channels: 1 mask + 4 latents
        assert result.shape == (1, 5, 64, 64)

        # First channel should be mask
        assert torch.equal(result[:, 0:1, :, :], mask)

        # Last 4 channels should be latents
        assert torch.equal(result[:, 1:5, :, :], latents)

    def test_mask_with_3d_input(self):
        """Test that 3D mask (no channel dim) is handled correctly."""
        # Mask without channel dimension
        mask = torch.ones(1, 64, 64)
        latents = torch.randn(1, 4, 64, 64)

        result = prepare_inpaint_head_input(mask, latents)

        # Should still produce 5 channels
        assert result.shape == (1, 5, 64, 64)

    def test_mask_with_multiple_channels(self):
        """Test that multi-channel mask uses only first channel."""
        # Mask with 3 channels (should only use first)
        mask = torch.ones(1, 3, 64, 64)
        latents = torch.randn(1, 4, 64, 64)

        result = prepare_inpaint_head_input(mask, latents)

        # Should produce 5 channels (first mask channel + 4 latents)
        assert result.shape == (1, 5, 64, 64)

    def test_batch_size_consistency(self):
        """Test that batch sizes are preserved."""
        for batch_size in [1, 2, 4]:
            mask = torch.ones(batch_size, 1, 64, 64)
            latents = torch.randn(batch_size, 4, 64, 64)

            result = prepare_inpaint_head_input(mask, latents)

            assert result.shape == (batch_size, 5, 64, 64)

    def test_resolution_consistency(self):
        """Test that different resolutions work correctly."""
        resolutions = [(64, 64), (96, 64), (64, 96), (128, 128)]

        for h, w in resolutions:
            mask = torch.ones(1, 1, h, w)
            latents = torch.randn(1, 4, h, w)

            result = prepare_inpaint_head_input(mask, latents)

            assert result.shape == (1, 5, h, w)

    def test_vae_encoder_parameter_ignored(self):
        """Test that vae_encoder parameter is accepted but not used."""
        mask = torch.ones(1, 1, 64, 64)
        latents = torch.randn(1, 4, 64, 64)

        # Should work with vae_encoder=None (default)
        result1 = prepare_inpaint_head_input(mask, latents)

        # Should also work with a mock vae_encoder (ignored)
        mock_vae = Mock()
        result2 = prepare_inpaint_head_input(mask, latents, vae_encoder=mock_vae)

        # Results should be identical
        assert torch.equal(result1, result2)

    def test_dtype_preservation(self):
        """Test that data type is preserved."""
        for dtype in [torch.float32, torch.float16]:
            mask = torch.ones(1, 1, 64, 64, dtype=dtype)
            latents = torch.randn(1, 4, 64, 64, dtype=dtype)

            result = prepare_inpaint_head_input(mask, latents)

            assert result.dtype == dtype

    def test_device_preservation(self):
        """Test that device is preserved."""
        mask = torch.ones(1, 1, 64, 64)
        latents = torch.randn(1, 4, 64, 64)

        result = prepare_inpaint_head_input(mask, latents)
        assert result.device == mask.device == latents.device

        # Test CUDA if available
        if torch.cuda.is_available():
            mask_cuda = mask.cuda()
            latents_cuda = latents.cuda()

            result_cuda = prepare_inpaint_head_input(mask_cuda, latents_cuda)
            assert result_cuda.device.type == 'cuda'


class TestInpaintHeadIntegration:
    """Integration tests for InpaintHead with real model file."""

    @pytest.fixture
    def model_path(self):
        """Get path to the InpaintHead model file."""
        return os.path.abspath(
            os.path.join(os.path.dirname(__file__),
                        "../../../../models/inpaint/fooocus_inpaint_head.pth")
        )

    @pytest.mark.skipif(
        not os.path.exists(
            os.path.abspath(
                os.path.join(os.path.dirname(__file__),
                            "../../../../models/inpaint/fooocus_inpaint_head.pth")
            )
        ),
        reason="InpaintHead model file not found"
    )
    def test_load_real_model(self, model_path):
        """Test loading the actual InpaintHead model file."""
        # Clear cache
        InpaintHeadLoader.clear_cache()

        # Load model
        model = InpaintHeadLoader.load_inpaint_head(model_path)

        # Verify model loaded correctly
        assert isinstance(model, InpaintHead)
        assert model.head.shape == (320, 5, 3, 3)

    @pytest.mark.skipif(
        not os.path.exists(
            os.path.abspath(
                os.path.join(os.path.dirname(__file__),
                            "../../../../models/inpaint/fooocus_inpaint_head.pth")
            )
        ),
        reason="InpaintHead model file not found"
    )
    def test_real_model_inference(self, model_path):
        """Test inference with real InpaintHead model."""
        # Load model
        model = InpaintHeadLoader.load_inpaint_head(model_path)

        # Create realistic input (mask + latents)
        mask = torch.ones(1, 1, 64, 64)
        latents = torch.randn(1, 4, 64, 64)

        # Prepare input
        inpaint_input = prepare_inpaint_head_input(mask, latents)

        # Run inference
        with torch.no_grad():
            features = model(inpaint_input)

        # Verify output
        assert features.shape == (1, 320, 64, 64)
        assert not torch.isnan(features).any()
        assert not torch.isinf(features).any()

    @pytest.mark.skipif(
        not os.path.exists(
            os.path.abspath(
                os.path.join(os.path.dirname(__file__),
                            "../../../../models/inpaint/fooocus_inpaint_head.pth")
            )
        ),
        reason="InpaintHead model file not found"
    )
    def test_real_model_gpu_inference(self, model_path):
        """Test GPU inference with real InpaintHead model."""
        if not torch.cuda.is_available():
            pytest.skip("CUDA not available")

        # Load model and move to GPU
        model = InpaintHeadLoader.load_inpaint_head(model_path)
        model = model.to('cuda', dtype=torch.float16)

        # Create input on GPU
        mask = torch.ones(1, 1, 64, 64, device='cuda', dtype=torch.float16)
        latents = torch.randn(1, 4, 64, 64, device='cuda', dtype=torch.float16)

        # Prepare and run
        inpaint_input = prepare_inpaint_head_input(mask, latents)

        with torch.no_grad():
            features = model(inpaint_input)

        # Verify
        assert features.device.type == 'cuda'
        assert features.dtype == torch.float16
        assert features.shape == (1, 320, 64, 64)
