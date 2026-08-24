"""
Comprehensive tests for MemoryStrategy class.

Tests all memory optimization decision methods and pipeline application logic
for different VRAM configurations.
"""

import pytest
import torch
from unittest.mock import Mock, MagicMock, patch, call
from src.pipelines.pipes.checkpoint_loader.sdxl.memory_strategy import apply_to_pipeline
from src.platform.runtime.model_lifecycle.memory_policy import MemoryPolicy


class TestMemoryStrategyInitialization:
    """Test MemoryStrategy initialization."""

    def test_initialization_with_valid_vram(self):
        """Test strategy initializes correctly with valid VRAM value."""
        strategy = MemoryPolicy(vram_gb=12.0)
        assert strategy.vram_gb == 12.0

    def test_initialization_with_low_vram(self):
        """Test strategy initializes correctly with low VRAM."""
        strategy = MemoryPolicy(vram_gb=6.0)
        assert strategy.vram_gb == 6.0

    def test_initialization_with_high_vram(self):
        """Test strategy initializes correctly with high VRAM."""
        strategy = MemoryPolicy(vram_gb=24.0)
        assert strategy.vram_gb == 24.0


class TestOffloadStrategy:
    """Test CPU offload strategy selection based on VRAM levels."""

    def test_sequential_offload_for_6gb(self):
        """Test sequential offload selected for 6GB VRAM (< 8GB)."""
        strategy = MemoryPolicy(vram_gb=6.0)
        assert strategy.get_offload_strategy() == "sequential"

    def test_sequential_offload_for_7_5gb(self):
        """Test sequential offload selected for 7.5GB VRAM (< 8GB)."""
        strategy = MemoryPolicy(vram_gb=7.5)
        assert strategy.get_offload_strategy() == "sequential"

    def test_model_offload_for_8gb(self):
        """Test model offload selected for 8GB VRAM (boundary case)."""
        strategy = MemoryPolicy(vram_gb=8.0)
        assert strategy.get_offload_strategy() == "model"

    def test_model_offload_for_10gb(self):
        """Test model offload selected for 10GB VRAM (8-12GB range)."""
        strategy = MemoryPolicy(vram_gb=10.0)
        assert strategy.get_offload_strategy() == "model"

    def test_model_offload_for_11_5gb(self):
        """Test model offload selected for 11.5GB VRAM (< 12GB)."""
        strategy = MemoryPolicy(vram_gb=11.5)
        assert strategy.get_offload_strategy() == "model"

    def test_no_offload_for_12gb(self):
        """Test no offload for 12GB VRAM (boundary case)."""
        strategy = MemoryPolicy(vram_gb=12.0)
        assert strategy.get_offload_strategy() == "none"

    def test_no_offload_for_16gb(self):
        """Test no offload for 16GB VRAM (high-end card)."""
        strategy = MemoryPolicy(vram_gb=16.0)
        assert strategy.get_offload_strategy() == "none"

    def test_no_offload_for_24gb(self):
        """Test no offload for 24GB VRAM (professional card)."""
        strategy = MemoryPolicy(vram_gb=24.0)
        assert strategy.get_offload_strategy() == "none"


class TestXformersDecision:
    """Test xformers memory efficient attention decision."""

    def test_xformers_enabled_for_all_vram_levels(self):
        """Test xformers is beneficial for all VRAM levels."""
        vram_levels = [6.0, 8.0, 10.0, 12.0, 16.0, 24.0]
        for vram_gb in vram_levels:
            strategy = MemoryPolicy(vram_gb=vram_gb)
            assert strategy.should_enable_xformers() is True, \
                f"xformers should be enabled for {vram_gb}GB VRAM"


class TestVAESlicing:
    """Test VAE slicing decision based on VRAM."""

    def test_vae_slicing_enabled_for_6gb(self):
        """Test VAE slicing enabled for 6GB VRAM."""
        strategy = MemoryPolicy(vram_gb=6.0)
        assert strategy.should_enable_vae_slicing() is True

    def test_vae_slicing_enabled_for_8gb(self):
        """Test VAE slicing enabled for 8GB VRAM."""
        strategy = MemoryPolicy(vram_gb=8.0)
        assert strategy.should_enable_vae_slicing() is True

    def test_vae_slicing_enabled_for_12gb(self):
        """Test VAE slicing enabled for 12GB VRAM."""
        strategy = MemoryPolicy(vram_gb=12.0)
        assert strategy.should_enable_vae_slicing() is True

    def test_vae_slicing_enabled_for_15_9gb(self):
        """Test VAE slicing enabled for 15.9GB VRAM (just below threshold)."""
        strategy = MemoryPolicy(vram_gb=15.9)
        assert strategy.should_enable_vae_slicing() is True

    def test_vae_slicing_disabled_for_16gb(self):
        """Test VAE slicing disabled for 16GB VRAM (at threshold)."""
        strategy = MemoryPolicy(vram_gb=16.0)
        assert strategy.should_enable_vae_slicing() is False

    def test_vae_slicing_disabled_for_24gb(self):
        """Test VAE slicing disabled for 24GB VRAM."""
        strategy = MemoryPolicy(vram_gb=24.0)
        assert strategy.should_enable_vae_slicing() is False


class TestVAETiling:
    """Test VAE tiling decision based on VRAM."""

    def test_vae_tiling_enabled_for_6gb(self):
        """Test VAE tiling enabled for 6GB VRAM."""
        strategy = MemoryPolicy(vram_gb=6.0)
        assert strategy.should_enable_vae_tiling() is True

    def test_vae_tiling_enabled_for_8gb(self):
        """Test VAE tiling enabled for 8GB VRAM."""
        strategy = MemoryPolicy(vram_gb=8.0)
        assert strategy.should_enable_vae_tiling() is True

    def test_vae_tiling_enabled_for_11_9gb(self):
        """Test VAE tiling enabled for 11.9GB VRAM (just below threshold)."""
        strategy = MemoryPolicy(vram_gb=11.9)
        assert strategy.should_enable_vae_tiling() is True

    def test_vae_tiling_disabled_for_12gb(self):
        """Test VAE tiling disabled for 12GB VRAM (at threshold)."""
        strategy = MemoryPolicy(vram_gb=12.0)
        assert strategy.should_enable_vae_tiling() is False

    def test_vae_tiling_disabled_for_16gb(self):
        """Test VAE tiling disabled for 16GB VRAM."""
        strategy = MemoryPolicy(vram_gb=16.0)
        assert strategy.should_enable_vae_tiling() is False

    def test_vae_tiling_disabled_for_24gb(self):
        """Test VAE tiling disabled for 24GB VRAM."""
        strategy = MemoryPolicy(vram_gb=24.0)
        assert strategy.should_enable_vae_tiling() is False


class TestAttentionSlicing:
    """Test attention slicing strategy selection."""

    def test_max_slicing_for_6gb(self):
        """Test max attention slicing for 6GB VRAM."""
        strategy = MemoryPolicy(vram_gb=6.0)
        assert strategy.get_attention_slicing() == "max"

    def test_max_slicing_for_7_9gb(self):
        """Test max attention slicing for 7.9GB VRAM (just below threshold)."""
        strategy = MemoryPolicy(vram_gb=7.9)
        assert strategy.get_attention_slicing() == "max"

    def test_auto_slicing_for_8gb(self):
        """Test auto attention slicing for 8GB VRAM (at threshold)."""
        strategy = MemoryPolicy(vram_gb=8.0)
        assert strategy.get_attention_slicing() == "auto"

    def test_auto_slicing_for_10gb(self):
        """Test auto attention slicing for 10GB VRAM."""
        strategy = MemoryPolicy(vram_gb=10.0)
        assert strategy.get_attention_slicing() == "auto"

    def test_auto_slicing_for_11_9gb(self):
        """Test auto attention slicing for 11.9GB VRAM (just below threshold)."""
        strategy = MemoryPolicy(vram_gb=11.9)
        assert strategy.get_attention_slicing() == "auto"

    def test_no_slicing_for_12gb(self):
        """Test no attention slicing for 12GB VRAM (at threshold)."""
        strategy = MemoryPolicy(vram_gb=12.0)
        assert strategy.get_attention_slicing() == "none"

    def test_no_slicing_for_16gb(self):
        """Test no attention slicing for 16GB VRAM."""
        strategy = MemoryPolicy(vram_gb=16.0)
        assert strategy.get_attention_slicing() == "none"

    def test_no_slicing_for_24gb(self):
        """Test no attention slicing for 24GB VRAM."""
        strategy = MemoryPolicy(vram_gb=24.0)
        assert strategy.get_attention_slicing() == "none"


class TestPipelineApplication:
    """Test applying memory optimizations to a mock pipeline."""

    @pytest.fixture
    def mock_pipe(self):
        """Create a mock pipeline with all necessary methods."""
        pipe = Mock()
        pipe.enable_sequential_cpu_offload = Mock()
        pipe.enable_model_cpu_offload = Mock()
        pipe.enable_xformers_memory_efficient_attention = Mock()
        pipe.enable_vae_slicing = Mock()
        pipe.enable_vae_tiling = Mock()
        pipe.enable_attention_slicing = Mock()

        # Mock UNet with attention processor support
        pipe.unet = Mock()
        pipe.unet.set_attn_processor = Mock()

        return pipe

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_sequential_offload_for_low_vram(self, mock_cuda, mock_pipe):
        """Test sequential offload applied for low VRAM (6GB)."""
        strategy = MemoryPolicy(vram_gb=6.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify sequential offload was called
        mock_pipe.enable_sequential_cpu_offload.assert_called_once()

        # Verify model offload was NOT called
        mock_pipe.enable_model_cpu_offload.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_model_offload_for_medium_vram(self, mock_cuda, mock_pipe):
        """Test model offload applied for medium VRAM (10GB)."""
        strategy = MemoryPolicy(vram_gb=10.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify model offload was called
        mock_pipe.enable_model_cpu_offload.assert_called_once()

        # Verify sequential offload was NOT called
        mock_pipe.enable_sequential_cpu_offload.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_no_offload_for_high_vram(self, mock_cuda, mock_pipe):
        """Test no offload for high VRAM (24GB)."""
        strategy = MemoryPolicy(vram_gb=24.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify neither offload method was called
        mock_pipe.enable_sequential_cpu_offload.assert_not_called()
        mock_pipe.enable_model_cpu_offload.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_tf32_optimization(self, mock_cuda, mock_pipe):
        """Test TF32 backend flags are set when CUDA is available."""
        strategy = MemoryPolicy(vram_gb=12.0)

        with patch('torch.backends.cuda.matmul') as mock_matmul, \
             patch('torch.backends.cudnn') as mock_cudnn:
            apply_to_pipeline(mock_pipe, strategy)

            # Verify TF32 flags were set
            assert mock_matmul.allow_tf32 is True
            assert mock_cudnn.allow_tf32 is True

    @patch('torch.cuda.is_available', return_value=False)
    def test_skip_tf32_when_cuda_unavailable(self, mock_cuda, mock_pipe):
        """Test TF32 optimization skipped when CUDA is not available."""
        strategy = MemoryPolicy(vram_gb=12.0)

        with patch('torch.backends.cuda.matmul') as mock_matmul:
            apply_to_pipeline(mock_pipe, strategy)

            # TF32 should not be set when CUDA is unavailable
            # The mock should not be accessed at all
            assert mock_matmul.allow_tf32.call_count == 0

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_pytorch_attention_processor(self, mock_cuda, mock_pipe):
        """Test PyTorch 2.0 attention processor is applied."""
        strategy = MemoryPolicy(vram_gb=12.0)

        with patch('diffusers.models.attention_processor.AttnProcessor2_0') as mock_processor:
            apply_to_pipeline(mock_pipe, strategy)

            # Verify attention processor was set
            mock_pipe.unet.set_attn_processor.assert_called_once()

    @patch('torch.cuda.is_available', return_value=True)
    def test_xformers_disabled_by_default(self, mock_cuda, mock_pipe):
        """Test xformers is NOT attempted unless explicitly requested — it
        would silently replace the AttnProcessor2_0 backend."""
        strategy = MemoryPolicy(vram_gb=12.0)
        apply_to_pipeline(mock_pipe, strategy)

        mock_pipe.enable_xformers_memory_efficient_attention.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_xformers_when_requested(self, mock_cuda, mock_pipe):
        """Test xformers is enabled when explicitly requested."""
        strategy = MemoryPolicy(vram_gb=12.0)
        apply_to_pipeline(mock_pipe, strategy, use_xformers=True)

        # Verify xformers was attempted
        mock_pipe.enable_xformers_memory_efficient_attention.assert_called_once()

    @patch('torch.cuda.is_available', return_value=True)
    def test_handle_xformers_import_error(self, mock_cuda, mock_pipe):
        """Test graceful handling when xformers is not available."""
        strategy = MemoryPolicy(vram_gb=12.0)

        # Simulate ImportError when enabling xformers
        mock_pipe.enable_xformers_memory_efficient_attention.side_effect = ImportError("xformers not installed")

        # Should not raise exception
        apply_to_pipeline(mock_pipe, strategy, use_xformers=True)

    @patch('torch.cuda.is_available', return_value=True)
    def test_handle_flash_attention_error(self, mock_cuda, mock_pipe):
        """Test graceful handling of Flash Attention CUDA errors."""
        strategy = MemoryPolicy(vram_gb=12.0)

        # Simulate Flash Attention error
        mock_pipe.enable_xformers_memory_efficient_attention.side_effect = \
            RuntimeError("Flash attention invalid argument")

        # Should not raise exception
        apply_to_pipeline(mock_pipe, strategy, use_xformers=True)

    @patch('torch.cuda.is_available', return_value=True)
    def test_offload_override_forces_sequential_on_high_vram(self, mock_cuda, mock_pipe):
        """Test extras.memory_strategy=sequential_offload wins over the policy tier."""
        strategy = MemoryPolicy(vram_gb=24.0)
        apply_to_pipeline(mock_pipe, strategy, offload_override="sequential")

        mock_pipe.enable_sequential_cpu_offload.assert_called_once()
        mock_pipe.enable_model_cpu_offload.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_offload_override_forces_gpu_resident_on_low_vram(self, mock_cuda, mock_pipe):
        """Test extras.memory_strategy=gpu_only wins over the policy tier."""
        strategy = MemoryPolicy(vram_gb=6.0)
        apply_to_pipeline(mock_pipe, strategy, offload_override="none")

        mock_pipe.enable_sequential_cpu_offload.assert_not_called()
        mock_pipe.enable_model_cpu_offload.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_offload_override_forces_model_offload(self, mock_cuda, mock_pipe):
        """Test extras.memory_strategy=cpu_offload forces model offload."""
        strategy = MemoryPolicy(vram_gb=24.0)
        apply_to_pipeline(mock_pipe, strategy, offload_override="model")

        mock_pipe.enable_model_cpu_offload.assert_called_once()
        mock_pipe.enable_sequential_cpu_offload.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_vae_slicing_when_needed(self, mock_cuda, mock_pipe):
        """Test VAE slicing is enabled for VRAM < 16GB."""
        strategy = MemoryPolicy(vram_gb=12.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify VAE slicing was enabled
        mock_pipe.enable_vae_slicing.assert_called_once()

    @patch('torch.cuda.is_available', return_value=True)
    def test_skip_vae_slicing_for_high_vram(self, mock_cuda, mock_pipe):
        """Test VAE slicing is skipped for VRAM >= 16GB."""
        strategy = MemoryPolicy(vram_gb=24.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify VAE slicing was NOT enabled
        mock_pipe.enable_vae_slicing.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_vae_tiling_when_needed(self, mock_cuda, mock_pipe):
        """Test VAE tiling is enabled for VRAM < 12GB."""
        strategy = MemoryPolicy(vram_gb=8.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify VAE tiling was enabled
        mock_pipe.enable_vae_tiling.assert_called_once()

    @patch('torch.cuda.is_available', return_value=True)
    def test_skip_vae_tiling_for_high_vram(self, mock_cuda, mock_pipe):
        """Test VAE tiling is skipped for VRAM >= 12GB."""
        strategy = MemoryPolicy(vram_gb=16.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify VAE tiling was NOT enabled
        mock_pipe.enable_vae_tiling.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_max_attention_slicing(self, mock_cuda, mock_pipe):
        """Test max attention slicing is applied for low VRAM."""
        strategy = MemoryPolicy(vram_gb=6.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify max attention slicing was enabled
        mock_pipe.enable_attention_slicing.assert_called_once_with(slice_size="max")

    @patch('torch.cuda.is_available', return_value=True)
    def test_apply_auto_attention_slicing(self, mock_cuda, mock_pipe):
        """Test auto attention slicing is applied for medium VRAM."""
        strategy = MemoryPolicy(vram_gb=10.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify auto attention slicing was enabled
        mock_pipe.enable_attention_slicing.assert_called_once_with(slice_size="auto")

    @patch('torch.cuda.is_available', return_value=True)
    def test_skip_attention_slicing_for_high_vram(self, mock_cuda, mock_pipe):
        """Test attention slicing is skipped for high VRAM."""
        strategy = MemoryPolicy(vram_gb=24.0)
        apply_to_pipeline(mock_pipe, strategy)

        # Verify attention slicing was NOT enabled
        mock_pipe.enable_attention_slicing.assert_not_called()

    @patch('torch.cuda.is_available', return_value=True)
    def test_handle_missing_pipeline_methods(self, mock_cuda):
        """Test graceful handling when pipeline is missing optimization methods."""
        # Create a minimal mock without optimization methods
        minimal_pipe = Mock(spec=[])

        strategy = MemoryPolicy(vram_gb=12.0)

        # Should not raise exception even when methods are missing
        apply_to_pipeline(minimal_pipe, strategy)

    @patch('torch.cuda.is_available', return_value=True)
    def test_handle_vae_optimization_errors(self, mock_cuda, mock_pipe):
        """Test graceful handling of VAE optimization errors."""
        strategy = MemoryPolicy(vram_gb=8.0)

        # Simulate errors in VAE methods
        mock_pipe.enable_vae_slicing.side_effect = RuntimeError("VAE slicing failed")
        mock_pipe.enable_vae_tiling.side_effect = RuntimeError("VAE tiling failed")

        # Should not raise exception
        apply_to_pipeline(mock_pipe, strategy)

    @patch('torch.cuda.is_available', return_value=True)
    def test_handle_attention_slicing_errors(self, mock_cuda, mock_pipe):
        """Test graceful handling of attention slicing errors."""
        strategy = MemoryPolicy(vram_gb=6.0)

        # Simulate error in attention slicing
        mock_pipe.enable_attention_slicing.side_effect = RuntimeError("Attention slicing failed")

        # Should not raise exception
        apply_to_pipeline(mock_pipe, strategy)


class TestVRAMThresholds:
    """Test boundary conditions and edge cases for VRAM thresholds."""

    def test_very_low_vram_2gb(self):
        """Test strategy for very low VRAM (2GB - integrated GPU)."""
        strategy = MemoryPolicy(vram_gb=2.0)
        assert strategy.get_offload_strategy() == "sequential"
        assert strategy.should_enable_vae_slicing() is True
        assert strategy.should_enable_vae_tiling() is True
        assert strategy.get_attention_slicing() == "max"

    def test_very_high_vram_48gb(self):
        """Test strategy for very high VRAM (48GB - professional card)."""
        strategy = MemoryPolicy(vram_gb=48.0)
        assert strategy.get_offload_strategy() == "none"
        assert strategy.should_enable_vae_slicing() is False
        assert strategy.should_enable_vae_tiling() is False
        assert strategy.get_attention_slicing() == "none"

    def test_exact_8gb_boundary(self):
        """Test exact 8GB boundary (common GPU: RTX 3060)."""
        strategy = MemoryPolicy(vram_gb=8.0)
        assert strategy.get_offload_strategy() == "model"  # Not sequential
        assert strategy.get_attention_slicing() == "auto"  # Not max

    def test_exact_12gb_boundary(self):
        """Test exact 12GB boundary (common GPU: RTX 3060 Ti, 4070)."""
        strategy = MemoryPolicy(vram_gb=12.0)
        assert strategy.get_offload_strategy() == "none"  # Not model
        assert strategy.should_enable_vae_tiling() is False  # Not enabled
        assert strategy.get_attention_slicing() == "none"  # Not auto

    def test_exact_16gb_boundary(self):
        """Test exact 16GB boundary (common GPU: RTX 4080)."""
        strategy = MemoryPolicy(vram_gb=16.0)
        assert strategy.should_enable_vae_slicing() is False  # Not enabled


class TestComprehensiveVRAMProfiles:
    """Test complete optimization profiles for common GPU configurations."""

    def test_rtx_3050_8gb_profile(self):
        """Test RTX 3050 (8GB VRAM) optimization profile."""
        strategy = MemoryPolicy(vram_gb=8.0)

        assert strategy.get_offload_strategy() == "model"
        assert strategy.should_enable_xformers() is True
        assert strategy.should_enable_vae_slicing() is True
        assert strategy.should_enable_vae_tiling() is True
        assert strategy.get_attention_slicing() == "auto"

    def test_rtx_4060_ti_16gb_profile(self):
        """Test RTX 4060 Ti (16GB VRAM) optimization profile."""
        strategy = MemoryPolicy(vram_gb=16.0)

        assert strategy.get_offload_strategy() == "none"
        assert strategy.should_enable_xformers() is True
        assert strategy.should_enable_vae_slicing() is False
        assert strategy.should_enable_vae_tiling() is False
        assert strategy.get_attention_slicing() == "none"

    def test_rtx_4090_24gb_profile(self):
        """Test RTX 4090 (24GB VRAM) optimization profile."""
        strategy = MemoryPolicy(vram_gb=24.0)

        assert strategy.get_offload_strategy() == "none"
        assert strategy.should_enable_xformers() is True
        assert strategy.should_enable_vae_slicing() is False
        assert strategy.should_enable_vae_tiling() is False
        assert strategy.get_attention_slicing() == "none"

    def test_rtx_3060_laptop_6gb_profile(self):
        """Test RTX 3060 Laptop (6GB VRAM) optimization profile."""
        strategy = MemoryPolicy(vram_gb=6.0)

        assert strategy.get_offload_strategy() == "sequential"
        assert strategy.should_enable_xformers() is True
        assert strategy.should_enable_vae_slicing() is True
        assert strategy.should_enable_vae_tiling() is True
        assert strategy.get_attention_slicing() == "max"

    def test_a100_40gb_profile(self):
        """Test A100 (40GB VRAM) optimization profile."""
        strategy = MemoryPolicy(vram_gb=40.0)

        assert strategy.get_offload_strategy() == "none"
        assert strategy.should_enable_xformers() is True
        assert strategy.should_enable_vae_slicing() is False
        assert strategy.should_enable_vae_tiling() is False
        assert strategy.get_attention_slicing() == "none"
