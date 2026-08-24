"""
Tests for SystemMonitorManager.

Tests the system monitoring manager functionality including:
- System stats retrieval
- Monitoring interval validation
- Hook execution
- Connection management
"""
import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
import asyncio

from src.features.system_monitor.manager import SystemMonitorManager
from src.features.system_monitor.connection_manager import MonitoringConnectionManager
from src.platform.plugins.hooks import HookContext
from src.features.system_monitor.hooks import SYSTEM_MONITOR_HOOKS


class TestSystemMonitorManager:
    """Tests for SystemMonitorManager class."""

    @pytest.fixture
    def mock_system_monitor(self):
        """Create a mock SystemMonitor."""
        monitor = Mock()
        monitor.get_system_snapshot.return_value = {
            "cpu": {
                "usage_percent": 45.5,
                "core_count": 8,
                "core_count_physical": 4
            },
            "ram": {
                "total_gb": 32.0,
                "available_gb": 16.0,
                "used_gb": 16.0,
                "usage_percent": 50.0
            },
            "vram": {
                "total_gb": 12.0,
                "available_gb": 8.0,
                "used_gb": 4.0,
                "free_gb": 8.0,
                "reserved_gb": 2.0,
                "allocated_gb": 1.5,
                "usage_percent": 33.3
            },
            "gpu": {
                "temperature_c": 55.0,
                "utilization_percent": 30.0,
                "name": "NVIDIA GeForce RTX 3080",
                "available": True
            }
        }
        return monitor

    @pytest.fixture
    def mock_gpu_manager(self):
        """Create a mock GpuManager."""
        manager = Mock()
        manager.get_used_vram.return_value = 4096
        manager.get_free_vram.return_value = 8192
        manager.get_total_vram.return_value = 12288
        manager.get_temperature.return_value = 55
        return manager

    @pytest.fixture
    def mock_plugin_registry(self):
        """Create a mock PluginRegistry."""
        registry = Mock()
        # Default: return unchanged data, not blocked
        context = Mock()
        context.data = {}
        registry.execute_hook.return_value = (context, [])
        return registry

    @pytest.fixture
    def manager(self, mock_system_monitor, mock_gpu_manager, mock_plugin_registry):
        """Create a SystemMonitorManager with mocked dependencies."""
        return SystemMonitorManager(
            system_monitor=mock_system_monitor,
            gpu_manager=mock_gpu_manager,
            plugin_registry=mock_plugin_registry
        )

    @pytest.fixture
    def manager_no_plugins(self, mock_system_monitor, mock_gpu_manager):
        """Create a SystemMonitorManager without plugin registry."""
        return SystemMonitorManager(
            system_monitor=mock_system_monitor,
            gpu_manager=mock_gpu_manager,
            plugin_registry=None
        )

    def test_get_system_stats_returns_formatted_data(self, manager, mock_system_monitor):
        """Test that get_system_stats returns properly formatted stats."""
        stats = manager.get_system_stats()

        # Verify timestamp is present
        assert "timestamp" in stats

        # Verify GPU stats (converted from GB to MB)
        assert stats["gpu"]["available"] is True
        assert stats["gpu"]["vram_used"] == 4096  # 4 GB * 1024
        assert stats["gpu"]["vram_free"] == 8192  # 8 GB * 1024
        assert stats["gpu"]["vram_total"] == 12288  # 12 GB * 1024
        assert stats["gpu"]["temperature"] == 55.0

        # Verify RAM stats (converted from GB to MB)
        assert stats["ram"]["available"] is True
        assert stats["ram"]["used"] == 16384  # 16 GB * 1024
        assert stats["ram"]["free"] == 16384  # 16 GB * 1024
        assert stats["ram"]["total"] == 32768  # 32 GB * 1024
        assert stats["ram"]["usage_percent"] == 50.0

        # Verify CPU stats
        assert stats["cpu"]["available"] is True
        assert stats["cpu"]["usage_percent"] == 45.5
        assert stats["cpu"]["core_count"] == 8

    def test_get_system_stats_without_plugins(self, manager_no_plugins, mock_system_monitor):
        """Test that get_system_stats works without plugin registry."""
        stats = manager_no_plugins.get_system_stats()

        assert "timestamp" in stats
        assert "gpu" in stats
        assert "ram" in stats
        assert "cpu" in stats

    def test_get_system_stats_hook_blocks_execution(self, manager, mock_plugin_registry):
        """Test that before_stats hook can block stats collection."""
        # Configure hook to block execution
        context = Mock()
        context.data = {"blocked": True, "block_reason": "Testing block"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        with pytest.raises(ValueError) as exc_info:
            manager.get_system_stats()

        assert "Testing block" in str(exc_info.value)

    def test_get_system_stats_hook_modifies_stats(self, manager, mock_plugin_registry, mock_system_monitor):
        """Test that after_stats hook can modify returned stats."""
        # First call returns unchanged data (before hook)
        # Second call returns modified stats (after hook)
        call_count = [0]

        def mock_execute_hook(hook_name, initial_data=None):
            call_count[0] += 1
            context = Mock()
            if call_count[0] == 1:
                # Before hook - not blocked
                context.data = initial_data or {}
            else:
                # After hook - modify stats
                stats = initial_data.get("stats", {})
                stats["custom_field"] = "plugin_added"
                context.data = {"stats": stats}
            return context, []

        mock_plugin_registry.execute_hook.side_effect = mock_execute_hook

        stats = manager.get_system_stats()

        assert stats.get("custom_field") == "plugin_added"

    def test_set_monitoring_interval_valid_values(self, manager):
        """Test that set_monitoring_interval accepts valid values."""
        manager.set_monitoring_interval(0.1)
        assert manager.monitoring_interval == 0.1

        manager.set_monitoring_interval(30.0)
        assert manager.monitoring_interval == 30.0

        manager.set_monitoring_interval(60.0)
        assert manager.monitoring_interval == 60.0

    def test_set_monitoring_interval_too_small(self, manager):
        """Test that set_monitoring_interval rejects values below 0.1."""
        with pytest.raises(ValueError) as exc_info:
            manager.set_monitoring_interval(0.05)

        assert "at least 0.1 seconds" in str(exc_info.value)

    def test_set_monitoring_interval_too_large(self, manager):
        """Test that set_monitoring_interval rejects values above 60."""
        with pytest.raises(ValueError) as exc_info:
            manager.set_monitoring_interval(120)

        assert "cannot exceed 60 seconds" in str(exc_info.value)

    def test_set_monitoring_interval_executes_hook(self, manager, mock_plugin_registry):
        """Test that set_monitoring_interval executes the interval_changed hook."""
        manager.set_monitoring_interval(5.0)

        # Verify hook was called with correct parameters
        mock_plugin_registry.execute_hook.assert_called()
        call_args = mock_plugin_registry.execute_hook.call_args_list[-1]
        assert call_args[0][0] == SYSTEM_MONITOR_HOOKS.interval_changed
        assert call_args[1]["initial_data"]["old_interval"] == 3.0
        assert call_args[1]["initial_data"]["new_interval"] == 5.0

    def test_connection_manager_initialized(self, manager):
        """Test that connection manager is initialized."""
        assert manager.connection_manager is not None
        assert isinstance(manager.connection_manager, MonitoringConnectionManager)

    def test_format_gpu_stats_unavailable(self, manager):
        """Test _format_gpu_stats returns unavailable when GPU not available."""
        snapshot = {
            "gpu": {"available": False},
            "vram": {}
        }

        result = manager._format_gpu_stats(snapshot)
        assert result == {"available": False}

    def test_format_ram_stats_zero_total(self, manager):
        """Test _format_ram_stats handles zero total gracefully."""
        snapshot = {
            "ram": {"total_gb": 0, "available_gb": 0, "used_gb": 0, "usage_percent": 0}
        }

        result = manager._format_ram_stats(snapshot)
        assert result == {"available": False}


class TestMonitoringConnectionManager:
    """Tests for MonitoringConnectionManager class."""

    @pytest.fixture
    def connection_manager(self):
        """Create a MonitoringConnectionManager."""
        return MonitoringConnectionManager()

    def test_add_connection(self, connection_manager):
        """Test adding a connection."""
        mock_ws = Mock()
        connection_manager.add_connection(mock_ws)

        assert connection_manager.connection_count() == 1
        assert connection_manager.has_connections() is True

    def test_remove_connection(self, connection_manager):
        """Test removing a connection."""
        mock_ws = Mock()
        connection_manager.add_connection(mock_ws)
        connection_manager.remove_connection(mock_ws)

        assert connection_manager.connection_count() == 0
        assert connection_manager.has_connections() is False

    def test_remove_nonexistent_connection(self, connection_manager):
        """Test removing a connection that doesn't exist."""
        mock_ws = Mock()
        # Should not raise
        connection_manager.remove_connection(mock_ws)
        assert connection_manager.connection_count() == 0

    @pytest.mark.asyncio
    async def test_broadcast_to_connections(self, connection_manager):
        """Test broadcasting message to all connections."""
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()

        connection_manager.add_connection(mock_ws1)
        connection_manager.add_connection(mock_ws2)

        message = {"test": "data"}
        await connection_manager.broadcast(message)

        mock_ws1.send_text.assert_called_once()
        mock_ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_removes_failed_connections(self, connection_manager):
        """Test that broadcast removes connections that fail to send."""
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()
        mock_ws2.send_text.side_effect = Exception("Connection closed")

        connection_manager.add_connection(mock_ws1)
        connection_manager.add_connection(mock_ws2)

        failed = await connection_manager.broadcast({"test": "data"})

        assert mock_ws2 in failed
        assert connection_manager.connection_count() == 1

    @pytest.mark.asyncio
    async def test_broadcast_no_connections(self, connection_manager):
        """Test broadcasting with no connections returns empty list."""
        result = await connection_manager.broadcast({"test": "data"})
        assert result == []


class TestSystemMonitorManagerAsync:
    """Async tests for SystemMonitorManager."""

    @pytest.fixture
    def mock_system_monitor(self):
        """Create a mock SystemMonitor."""
        monitor = Mock()
        monitor.get_system_snapshot.return_value = {
            "cpu": {"usage_percent": 45.5, "core_count": 8},
            "ram": {"total_gb": 32.0, "available_gb": 16.0, "used_gb": 16.0, "usage_percent": 50.0},
            "vram": {"total_gb": 12.0, "available_gb": 8.0, "used_gb": 4.0, "free_gb": 8.0},
            "gpu": {"temperature_c": 55.0, "available": True}
        }
        return monitor

    @pytest.fixture
    def manager(self, mock_system_monitor):
        """Create a SystemMonitorManager."""
        return SystemMonitorManager(
            system_monitor=mock_system_monitor,
            gpu_manager=None,
            plugin_registry=None
        )

    @pytest.mark.asyncio
    async def test_start_monitoring_task(self, manager):
        """Test starting the monitoring task."""
        await manager.start_monitoring_task()

        assert manager.monitoring_task is not None
        assert not manager.monitoring_task.done()

        # Cleanup
        await manager.stop_monitoring_task()

    @pytest.mark.asyncio
    async def test_stop_monitoring_task(self, manager):
        """Test stopping the monitoring task."""
        await manager.start_monitoring_task()
        await manager.stop_monitoring_task()

        assert manager.monitoring_task is None

    @pytest.mark.asyncio
    async def test_stop_monitoring_task_when_not_running(self, manager):
        """Test stopping when no task is running."""
        # Should not raise
        await manager.stop_monitoring_task()
        assert manager.monitoring_task is None
