"""
Unit tests for SDXL Conditioning Builder

Tests the build_time_ids and prepare_for_cfg methods to ensure correct
SDXL conditioning tensor generation.
"""

import pytest
import torch
from src.pipelines.pipes.generator.sdxl.conditioning_builder import SDXLConditioningBuilder


class TestSDXLConditioningBuilder:
    """Test suite for SDXLConditioningBuilder"""

    def test_build_time_ids_basic(self):
        """Test basic time IDs generation with single batch"""
        time_ids = SDXLConditioningBuilder.build_time_ids(
            original_size=(1024, 1024),
            crops_coords_top_left=(0, 0),
            target_size=(1024, 1024),
            dtype=torch.float32,
            device="cpu",
            batch_size=1
        )

        # Check shape: [batch_size, 6]
        assert time_ids.shape == (1, 6)

        # Check values: [orig_h, orig_w, crop_y, crop_x, target_h, target_w]
        expected = torch.tensor([[1024, 1024, 0, 0, 1024, 1024]], dtype=torch.float32)
        assert torch.equal(time_ids, expected)

    def test_build_time_ids_multiple_batches(self):
        """Test time IDs generation with multiple batches"""
        batch_size = 4
        time_ids = SDXLConditioningBuilder.build_time_ids(
            original_size=(512, 768),
            crops_coords_top_left=(10, 20),
            target_size=(1024, 1536),
            dtype=torch.float16,
            device="cpu",
            batch_size=batch_size
        )

        # Check shape: [batch_size, 6]
        assert time_ids.shape == (batch_size, 6)

        # Check all batches have identical values
        expected_row = torch.tensor([512, 768, 10, 20, 1024, 1536], dtype=torch.float16)
        for i in range(batch_size):
            assert torch.equal(time_ids[i], expected_row)

    def test_build_time_ids_dtype_preservation(self):
        """Test that dtype is preserved correctly"""
        # Test float16
        time_ids_f16 = SDXLConditioningBuilder.build_time_ids(
            original_size=(1024, 1024),
            crops_coords_top_left=(0, 0),
            target_size=(1024, 1024),
            dtype=torch.float16,
            device="cpu"
        )
        assert time_ids_f16.dtype == torch.float16

        # Test float32
        time_ids_f32 = SDXLConditioningBuilder.build_time_ids(
            original_size=(1024, 1024),
            crops_coords_top_left=(0, 0),
            target_size=(1024, 1024),
            dtype=torch.float32,
            device="cpu"
        )
        assert time_ids_f32.dtype == torch.float32

        # Test bfloat16
        time_ids_bf16 = SDXLConditioningBuilder.build_time_ids(
            original_size=(1024, 1024),
            crops_coords_top_left=(0, 0),
            target_size=(1024, 1024),
            dtype=torch.bfloat16,
            device="cpu"
        )
        assert time_ids_bf16.dtype == torch.bfloat16

    def test_build_time_ids_device_placement(self):
        """Test that tensors are created on correct device"""
        # Test CPU
        time_ids_cpu = SDXLConditioningBuilder.build_time_ids(
            original_size=(1024, 1024),
            crops_coords_top_left=(0, 0),
            target_size=(1024, 1024),
            dtype=torch.float32,
            device="cpu"
        )
        assert time_ids_cpu.device.type == "cpu"

        # Test CUDA (if available)
        if torch.cuda.is_available():
            time_ids_cuda = SDXLConditioningBuilder.build_time_ids(
                original_size=(1024, 1024),
                crops_coords_top_left=(0, 0),
                target_size=(1024, 1024),
                dtype=torch.float32,
                device="cuda"
            )
            assert time_ids_cuda.device.type == "cuda"

    def test_build_time_ids_different_resolutions(self):
        """Test time IDs with various resolution configurations"""
        # Portrait orientation
        time_ids_portrait = SDXLConditioningBuilder.build_time_ids(
            original_size=(1536, 1024),
            crops_coords_top_left=(0, 0),
            target_size=(1536, 1024),
            dtype=torch.float32,
            device="cpu"
        )
        assert time_ids_portrait[0, 0] == 1536  # height
        assert time_ids_portrait[0, 1] == 1024  # width

        # Landscape orientation
        time_ids_landscape = SDXLConditioningBuilder.build_time_ids(
            original_size=(1024, 1536),
            crops_coords_top_left=(0, 0),
            target_size=(1024, 1536),
            dtype=torch.float32,
            device="cpu"
        )
        assert time_ids_landscape[0, 0] == 1024  # height
        assert time_ids_landscape[0, 1] == 1536  # width

    def test_build_time_ids_with_crops(self):
        """Test time IDs with non-zero crop coordinates"""
        time_ids = SDXLConditioningBuilder.build_time_ids(
            original_size=(2048, 2048),
            crops_coords_top_left=(512, 256),
            target_size=(1024, 1024),
            dtype=torch.float32,
            device="cpu"
        )

        # Check crop coordinates are in correct positions
        assert time_ids[0, 2] == 512  # crop_y
        assert time_ids[0, 3] == 256  # crop_x

    def test_prepare_for_cfg_basic(self):
        """Test basic CFG preparation with dummy tensors"""
        batch_size = 1
        seq_len = 77
        embed_dim = 2048
        pooled_dim = 1280

        # Create dummy tensors
        prompt_embeds = torch.randn(batch_size, seq_len, embed_dim)
        negative_prompt_embeds = torch.randn(batch_size, seq_len, embed_dim)
        pooled_embeds = torch.randn(batch_size, pooled_dim)
        negative_pooled_embeds = torch.randn(batch_size, pooled_dim)
        time_ids = torch.randn(batch_size, 6)

        # Prepare for CFG
        cfg_tensors = SDXLConditioningBuilder.prepare_for_cfg(
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            pooled_embeds=pooled_embeds,
            negative_pooled_embeds=negative_pooled_embeds,
            time_ids=time_ids
        )

        # Check all expected keys are present
        assert "prompt_embeds" in cfg_tensors
        assert "pooled_prompt_embeds" in cfg_tensors
        assert "time_ids" in cfg_tensors

        # Check shapes (should be doubled in batch dimension)
        assert cfg_tensors["prompt_embeds"].shape == (batch_size * 2, seq_len, embed_dim)
        assert cfg_tensors["pooled_prompt_embeds"].shape == (batch_size * 2, pooled_dim)
        assert cfg_tensors["time_ids"].shape == (batch_size * 2, 6)

    def test_prepare_for_cfg_concatenation_order(self):
        """Test that CFG concatenation follows [negative, positive] order"""
        batch_size = 1
        seq_len = 77
        embed_dim = 4  # Small for easy testing

        # Create distinct tensors to verify order
        prompt_embeds = torch.ones(batch_size, seq_len, embed_dim)
        negative_prompt_embeds = torch.zeros(batch_size, seq_len, embed_dim)
        pooled_embeds = torch.ones(batch_size, embed_dim)
        negative_pooled_embeds = torch.zeros(batch_size, embed_dim)
        time_ids = torch.ones(batch_size, 6)

        cfg_tensors = SDXLConditioningBuilder.prepare_for_cfg(
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            pooled_embeds=pooled_embeds,
            negative_pooled_embeds=negative_pooled_embeds,
            time_ids=time_ids
        )

        # First half should be negative (zeros)
        assert torch.all(cfg_tensors["prompt_embeds"][0] == 0)
        assert torch.all(cfg_tensors["pooled_prompt_embeds"][0] == 0)

        # Second half should be positive (ones)
        assert torch.all(cfg_tensors["prompt_embeds"][1] == 1)
        assert torch.all(cfg_tensors["pooled_prompt_embeds"][1] == 1)

        # Time IDs should be identical for both (all ones)
        assert torch.all(cfg_tensors["time_ids"][0] == 1)
        assert torch.all(cfg_tensors["time_ids"][1] == 1)

    def test_prepare_for_cfg_multiple_batches(self):
        """Test CFG preparation with multiple batches"""
        batch_size = 3
        seq_len = 77
        embed_dim = 2048
        pooled_dim = 1280

        # Create dummy tensors
        prompt_embeds = torch.randn(batch_size, seq_len, embed_dim)
        negative_prompt_embeds = torch.randn(batch_size, seq_len, embed_dim)
        pooled_embeds = torch.randn(batch_size, pooled_dim)
        negative_pooled_embeds = torch.randn(batch_size, pooled_dim)
        time_ids = torch.randn(batch_size, 6)

        cfg_tensors = SDXLConditioningBuilder.prepare_for_cfg(
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            pooled_embeds=pooled_embeds,
            negative_pooled_embeds=negative_pooled_embeds,
            time_ids=time_ids
        )

        # Check shapes (batch dimension should be doubled)
        assert cfg_tensors["prompt_embeds"].shape == (batch_size * 2, seq_len, embed_dim)
        assert cfg_tensors["pooled_prompt_embeds"].shape == (batch_size * 2, pooled_dim)
        assert cfg_tensors["time_ids"].shape == (batch_size * 2, 6)

        # Verify concatenation for each batch item
        for i in range(batch_size):
            # Negative embeddings in first half
            assert torch.equal(
                cfg_tensors["prompt_embeds"][i],
                negative_prompt_embeds[i]
            )
            # Positive embeddings in second half
            assert torch.equal(
                cfg_tensors["prompt_embeds"][batch_size + i],
                prompt_embeds[i]
            )

    def test_prepare_for_cfg_dtype_preservation(self):
        """Test that dtype is preserved during CFG preparation"""
        batch_size = 1
        seq_len = 77
        embed_dim = 2048
        pooled_dim = 1280

        for dtype in [torch.float16, torch.float32, torch.bfloat16]:
            prompt_embeds = torch.randn(batch_size, seq_len, embed_dim, dtype=dtype)
            negative_prompt_embeds = torch.randn(batch_size, seq_len, embed_dim, dtype=dtype)
            pooled_embeds = torch.randn(batch_size, pooled_dim, dtype=dtype)
            negative_pooled_embeds = torch.randn(batch_size, pooled_dim, dtype=dtype)
            time_ids = torch.randn(batch_size, 6, dtype=dtype)

            cfg_tensors = SDXLConditioningBuilder.prepare_for_cfg(
                prompt_embeds=prompt_embeds,
                negative_prompt_embeds=negative_prompt_embeds,
                pooled_embeds=pooled_embeds,
                negative_pooled_embeds=negative_pooled_embeds,
                time_ids=time_ids
            )

            # All tensors should maintain original dtype
            assert cfg_tensors["prompt_embeds"].dtype == dtype
            assert cfg_tensors["pooled_prompt_embeds"].dtype == dtype
            assert cfg_tensors["time_ids"].dtype == dtype

    def test_prepare_for_cfg_device_preservation(self):
        """Test that device is preserved during CFG preparation"""
        batch_size = 1
        seq_len = 77
        embed_dim = 2048
        pooled_dim = 1280

        # Test CPU
        prompt_embeds_cpu = torch.randn(batch_size, seq_len, embed_dim, device="cpu")
        negative_prompt_embeds_cpu = torch.randn(batch_size, seq_len, embed_dim, device="cpu")
        pooled_embeds_cpu = torch.randn(batch_size, pooled_dim, device="cpu")
        negative_pooled_embeds_cpu = torch.randn(batch_size, pooled_dim, device="cpu")
        time_ids_cpu = torch.randn(batch_size, 6, device="cpu")

        cfg_tensors_cpu = SDXLConditioningBuilder.prepare_for_cfg(
            prompt_embeds=prompt_embeds_cpu,
            negative_prompt_embeds=negative_prompt_embeds_cpu,
            pooled_embeds=pooled_embeds_cpu,
            negative_pooled_embeds=negative_pooled_embeds_cpu,
            time_ids=time_ids_cpu
        )

        assert cfg_tensors_cpu["prompt_embeds"].device.type == "cpu"
        assert cfg_tensors_cpu["pooled_prompt_embeds"].device.type == "cpu"
        assert cfg_tensors_cpu["time_ids"].device.type == "cpu"

        # Test CUDA (if available)
        if torch.cuda.is_available():
            prompt_embeds_cuda = torch.randn(batch_size, seq_len, embed_dim, device="cuda")
            negative_prompt_embeds_cuda = torch.randn(batch_size, seq_len, embed_dim, device="cuda")
            pooled_embeds_cuda = torch.randn(batch_size, pooled_dim, device="cuda")
            negative_pooled_embeds_cuda = torch.randn(batch_size, pooled_dim, device="cuda")
            time_ids_cuda = torch.randn(batch_size, 6, device="cuda")

            cfg_tensors_cuda = SDXLConditioningBuilder.prepare_for_cfg(
                prompt_embeds=prompt_embeds_cuda,
                negative_prompt_embeds=negative_prompt_embeds_cuda,
                pooled_embeds=pooled_embeds_cuda,
                negative_pooled_embeds=negative_pooled_embeds_cuda,
                time_ids=time_ids_cuda
            )

            assert cfg_tensors_cuda["prompt_embeds"].device.type == "cuda"
            assert cfg_tensors_cuda["pooled_prompt_embeds"].device.type == "cuda"
            assert cfg_tensors_cuda["time_ids"].device.type == "cuda"

    def test_integration_build_time_ids_and_prepare_cfg(self):
        """Test integration: build time IDs and then prepare for CFG"""
        batch_size = 2
        seq_len = 77
        embed_dim = 2048
        pooled_dim = 1280

        # Build time IDs
        time_ids = SDXLConditioningBuilder.build_time_ids(
            original_size=(1024, 1024),
            crops_coords_top_left=(0, 0),
            target_size=(1024, 1024),
            dtype=torch.float16,
            device="cpu",
            batch_size=batch_size
        )

        # Create dummy embeddings
        prompt_embeds = torch.randn(batch_size, seq_len, embed_dim, dtype=torch.float16)
        negative_prompt_embeds = torch.randn(batch_size, seq_len, embed_dim, dtype=torch.float16)
        pooled_embeds = torch.randn(batch_size, pooled_dim, dtype=torch.float16)
        negative_pooled_embeds = torch.randn(batch_size, pooled_dim, dtype=torch.float16)

        # Prepare for CFG
        cfg_tensors = SDXLConditioningBuilder.prepare_for_cfg(
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            pooled_embeds=pooled_embeds,
            negative_pooled_embeds=negative_pooled_embeds,
            time_ids=time_ids
        )

        # Verify everything is consistent
        assert cfg_tensors["prompt_embeds"].shape[0] == batch_size * 2
        assert cfg_tensors["pooled_prompt_embeds"].shape[0] == batch_size * 2
        assert cfg_tensors["time_ids"].shape[0] == batch_size * 2

        # Verify time IDs are duplicated correctly
        for i in range(batch_size):
            # First half (negative)
            assert torch.equal(cfg_tensors["time_ids"][i], time_ids[i])
            # Second half (positive)
            assert torch.equal(cfg_tensors["time_ids"][batch_size + i], time_ids[i])
