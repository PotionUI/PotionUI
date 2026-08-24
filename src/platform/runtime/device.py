"""
Device utilities for GPU memory management.

Provides standalone functions for GPU cleanup, memory logging,
and context managers used across pipeline pipes.
"""

import gc
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def clear_gpu_memory():
    """Run gc.collect() and torch.cuda.empty_cache() safely."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


@contextmanager
def gpu_memory_scope(log_prefix=None):
    """Context manager that clears GPU memory on exit.

    Args:
        log_prefix: Optional prefix for memory logging on exit.
    """
    try:
        yield
    finally:
        clear_gpu_memory()
        if log_prefix:
            log_memory_usage(log_prefix)


def log_memory_usage(stage: str, prefix: str = ""):
    """Log current VRAM and RAM usage.

    Args:
        stage: Description of the current stage (e.g. "after model load").
        prefix: Optional prefix for log messages.
    """
    try:
        import torch

        if torch.cuda.is_available():
            allocated = torch.cuda.memory_allocated() / 1024**3
            reserved = torch.cuda.memory_reserved() / 1024**3
            logger.debug(
                f"{prefix}[{stage}] VRAM: {allocated:.2f}GB allocated, "
                f"{reserved:.2f}GB reserved"
            )
    except ImportError:
        pass

    try:
        import psutil

        process = psutil.Process()
        ram_mb = process.memory_info().rss / 1024**2
        logger.debug(f"{prefix}[{stage}] RAM: {ram_mb:.0f}MB")
    except ImportError:
        pass
