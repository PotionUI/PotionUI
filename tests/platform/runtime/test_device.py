"""Tests for src.platform.runtime.device"""

import gc
import logging
from unittest.mock import MagicMock, patch

import pytest

from src.platform.runtime.device import (
    clear_gpu_memory,
    gpu_memory_scope,
    log_memory_usage,
)


# ---- clear_gpu_memory ----

class TestClearGpuMemory:
    @patch("src.platform.runtime.device.gc.collect")
    def test_calls_gc_collect(self, mock_gc_collect):
        """gc.collect() should always be called."""
        with patch.dict("sys.modules", {"torch": MagicMock()}):
            clear_gpu_memory()
        mock_gc_collect.assert_called_once()

    @patch("src.platform.runtime.device.gc.collect")
    def test_calls_cuda_empty_cache_when_available(self, mock_gc_collect):
        """torch.cuda.empty_cache() should be called when CUDA is available."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True

        with patch.dict("sys.modules", {"torch": mock_torch}):
            # Re-import to pick up patched module
            import importlib
            import src.platform.runtime.device as mod
            importlib.reload(mod)
            mod.clear_gpu_memory()
            mock_torch.cuda.empty_cache.assert_called_once()

    @patch("src.platform.runtime.device.gc.collect")
    def test_no_cuda_empty_cache_when_not_available(self, mock_gc_collect):
        """torch.cuda.empty_cache() should NOT be called when CUDA is unavailable."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False

        with patch.dict("sys.modules", {"torch": mock_torch}):
            import importlib
            import src.platform.runtime.device as mod
            importlib.reload(mod)
            mod.clear_gpu_memory()
            mock_torch.cuda.empty_cache.assert_not_called()

    @patch("src.platform.runtime.device.gc.collect")
    def test_handles_torch_import_error(self, mock_gc_collect):
        """Should not raise when torch is not installed."""
        with patch.dict("sys.modules", {"torch": None}):
            import importlib
            import src.platform.runtime.device as mod
            importlib.reload(mod)
            mod.clear_gpu_memory()  # should not raise
        mock_gc_collect.assert_called_once()


# ---- gpu_memory_scope ----

class TestGpuMemoryScope:
    @patch("src.platform.runtime.device.log_memory_usage")
    @patch("src.platform.runtime.device.clear_gpu_memory")
    def test_clears_gpu_memory_on_exit(self, mock_clear, mock_log):
        """clear_gpu_memory() should be called when exiting the context."""
        with gpu_memory_scope():
            mock_clear.assert_not_called()
        mock_clear.assert_called_once()

    @patch("src.platform.runtime.device.log_memory_usage")
    @patch("src.platform.runtime.device.clear_gpu_memory")
    def test_logs_memory_when_prefix_given(self, mock_clear, mock_log):
        """log_memory_usage() should be called when log_prefix is provided."""
        with gpu_memory_scope(log_prefix="test_stage"):
            pass
        mock_log.assert_called_once_with("test_stage")

    @patch("src.platform.runtime.device.log_memory_usage")
    @patch("src.platform.runtime.device.clear_gpu_memory")
    def test_no_log_when_no_prefix(self, mock_clear, mock_log):
        """log_memory_usage() should NOT be called when no log_prefix."""
        with gpu_memory_scope():
            pass
        mock_log.assert_not_called()

    @patch("src.platform.runtime.device.log_memory_usage")
    @patch("src.platform.runtime.device.clear_gpu_memory")
    def test_cleanup_on_exception(self, mock_clear, mock_log):
        """clear_gpu_memory() should still be called even if an exception occurs."""
        with pytest.raises(RuntimeError):
            with gpu_memory_scope():
                raise RuntimeError("test error")
        mock_clear.assert_called_once()

    @patch("src.platform.runtime.device.log_memory_usage")
    @patch("src.platform.runtime.device.clear_gpu_memory")
    def test_yields_none(self, mock_clear, mock_log):
        """The context manager should yield None."""
        with gpu_memory_scope() as value:
            assert value is None


# ---- log_memory_usage ----

class TestLogMemoryUsage:
    def test_logs_vram_when_cuda_available(self, caplog):
        """Should log VRAM info when CUDA is available."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.memory_allocated.return_value = 2 * 1024**3  # 2 GB
        mock_torch.cuda.memory_reserved.return_value = 4 * 1024**3   # 4 GB

        mock_psutil = MagicMock()
        mock_psutil.Process.return_value.memory_info.return_value.rss = 500 * 1024**2

        with patch.dict("sys.modules", {"torch": mock_torch, "psutil": mock_psutil}):
            import importlib
            import src.platform.runtime.device as mod
            importlib.reload(mod)
            with caplog.at_level(logging.DEBUG, logger="src.platform.runtime.device"):
                mod.log_memory_usage("after load")

            assert any("VRAM" in record.message and "2.00GB" in record.message for record in caplog.records)
            assert any("after load" in record.message for record in caplog.records)

    def test_logs_with_prefix(self, caplog):
        """Should include prefix in log messages."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.memory_allocated.return_value = 1024**3
        mock_torch.cuda.memory_reserved.return_value = 2 * 1024**3

        mock_psutil = MagicMock()
        mock_psutil.Process.return_value.memory_info.return_value.rss = 100 * 1024**2

        with patch.dict("sys.modules", {"torch": mock_torch, "psutil": mock_psutil}):
            import importlib
            import src.platform.runtime.device as mod
            importlib.reload(mod)
            with caplog.at_level(logging.DEBUG, logger="src.platform.runtime.device"):
                mod.log_memory_usage("test_stage", prefix="[MY_PIPE] ")

            assert any("[MY_PIPE]" in record.message for record in caplog.records)

    def test_logs_ram_via_psutil(self, caplog):
        """Should log RAM usage via psutil."""
        mock_psutil = MagicMock()
        mock_psutil.Process.return_value.memory_info.return_value.rss = 256 * 1024**2

        # Make torch import fail so we only test psutil path
        with patch.dict("sys.modules", {"torch": None, "psutil": mock_psutil}):
            import importlib
            import src.platform.runtime.device as mod
            importlib.reload(mod)
            with caplog.at_level(logging.DEBUG, logger="src.platform.runtime.device"):
                mod.log_memory_usage("test_ram")

            assert any("RAM" in record.message and "256" in record.message for record in caplog.records)

    def test_handles_no_torch_no_psutil(self, caplog):
        """Should not raise when both torch and psutil are unavailable."""
        with patch.dict("sys.modules", {"torch": None, "psutil": None}):
            import importlib
            import src.platform.runtime.device as mod
            importlib.reload(mod)
            with caplog.at_level(logging.DEBUG, logger="src.platform.runtime.device"):
                mod.log_memory_usage("no_deps")  # should not raise

    def test_handles_cuda_not_available(self, caplog):
        """Should skip VRAM logging when CUDA is not available."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False

        mock_psutil = MagicMock()
        mock_psutil.Process.return_value.memory_info.return_value.rss = 100 * 1024**2

        with patch.dict("sys.modules", {"torch": mock_torch, "psutil": mock_psutil}):
            import importlib
            import src.platform.runtime.device as mod
            importlib.reload(mod)
            with caplog.at_level(logging.DEBUG, logger="src.platform.runtime.device"):
                mod.log_memory_usage("no_cuda")

            # Should not have VRAM log
            vram_msgs = [r for r in caplog.records if "VRAM" in r.message]
            assert len(vram_msgs) == 0
            # Should still have RAM log
            ram_msgs = [r for r in caplog.records if "RAM" in r.message]
            assert len(ram_msgs) == 1
