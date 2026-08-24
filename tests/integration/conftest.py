"""
Pytest configuration and fixtures for integration tests.
"""

import pytest
import torch
from unittest.mock import Mock, patch


@pytest.fixture(autouse=True)
def mock_cuda_for_tests(monkeypatch):
    """
    Auto-use fixture to mock CUDA operations in tests.

    This prevents CUDA errors in test environments without GPU support.
    """
    # Mock torch.Generator to avoid CUDA issues
    original_generator = torch.Generator

    def safe_generator(device="cpu"):
        """Create generator on CPU to avoid CUDA errors."""
        # Force CPU device if CUDA is not available
        if device == "cuda" and not torch.cuda.is_available():
            device = "cpu"
        return original_generator(device=device)

    monkeypatch.setattr("torch.Generator", safe_generator)
