"""
System monitoring coordinator - coordinates system monitoring operations.

This module provides the SystemMonitorCoordinator class that coordinates system
monitoring operations by delegating to existing services and managing
WebSocket broadcasting for real-time updates.
"""
from typing import Dict, Any, Optional, Tuple
import asyncio
import logging
import json
import time

from src.platform.observability.system_probe import SystemMonitor
from src.platform.runtime.gpu import GpuMonitor
from src.features.system_monitor.connection_hub import MonitoringConnectionHub, WebSocketProtocol
from src.platform.plugins import PluginRegistry
from src.features.system_monitor.hooks import SYSTEM_MONITOR_HOOKS


class SystemMonitorCoordinator:
    """
    Coordinates system monitoring operations.

    Uses existing SystemMonitor for stats collection (no duplication).
    Manages WebSocket broadcasting and background monitoring task.
    Executes plugin hooks for extensibility.
    """

    def __init__(
        self,
        system_monitor: SystemMonitor,
        gpu_monitor: Optional[GpuMonitor] = None,
        plugin_registry: Optional[PluginRegistry] = None
    ):
        """
        Initialize the SystemMonitorCoordinator.

        Args:
            system_monitor: SystemMonitor instance for collecting stats
            gpu_monitor: Optional GpuMonitor for GPU-specific operations
            plugin_registry: Optional PluginRegistry for hook execution
        """
        self.system_monitor = system_monitor
        self.gpu_monitor = gpu_monitor
        self.plugins = plugin_registry
        self.connection_hub = MonitoringConnectionHub()
        self.monitoring_task: Optional[asyncio.Task] = None
        self.monitoring_interval: float = 3.0
        self.logger = logging.getLogger(__name__)

    def get_system_stats(self) -> Dict[str, Any]:
        """
        Get current system stats using existing SystemMonitor.

        Returns:
            Dictionary containing GPU, RAM, and CPU statistics

        Raises:
            ValueError: If stats collection is blocked by a plugin hook
        """
        # Execute before hook
        hook_data, blocked = self._execute_hook(
            SYSTEM_MONITOR_HOOKS.before_stats,
            {}
        )
        if blocked:
            raise ValueError(hook_data.get("block_reason", "Stats collection blocked"))

        # Use existing SystemMonitor to get snapshot
        snapshot = self.system_monitor.get_system_snapshot()

        # Transform to API format
        stats = {
            "timestamp": time.time(),
            "gpu": self._format_gpu_stats(snapshot),
            "ram": self._format_ram_stats(snapshot),
            "cpu": self._format_cpu_stats(snapshot)
        }

        # Execute after hook (allows modification)
        hook_data, _ = self._execute_hook(
            SYSTEM_MONITOR_HOOKS.after_stats,
            {"stats": stats}
        )

        return hook_data.get("stats", stats)

    def set_monitoring_interval(self, interval: float) -> None:
        """
        Set monitoring interval with validation.

        Args:
            interval: Update interval in seconds (0.1 to 60)

        Raises:
            ValueError: If interval is out of valid range
        """
        if interval < 0.1:
            raise ValueError("Monitoring interval must be at least 0.1 seconds")
        if interval > 60:
            raise ValueError("Monitoring interval cannot exceed 60 seconds")

        old_interval = self.monitoring_interval
        self.monitoring_interval = interval

        # Execute hook for interval change notification
        self._execute_hook(
            SYSTEM_MONITOR_HOOKS.interval_changed,
            {"old_interval": old_interval, "new_interval": interval}
        )

    async def start_monitoring_task(self) -> None:
        """Start background monitoring if not running."""
        if self.monitoring_task is None or self.monitoring_task.done():
            self.monitoring_task = asyncio.create_task(self._monitor_system())

    async def stop_monitoring_task(self) -> None:
        """Stop background monitoring task."""
        if self.monitoring_task and not self.monitoring_task.done():
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            self.monitoring_task = None

    async def handle_websocket_connection(
        self,
        websocket: WebSocketProtocol,
        client_id: str,
        accept_callback=None,
        receive_callback=None
    ) -> None:
        """
        Handle a WebSocket connection for system monitoring.

        Args:
            websocket: WebSocket connection (FastAPI WebSocket)
            client_id: Unique client identifier
            accept_callback: Optional callback to accept the connection
            receive_callback: Optional callback to receive messages
        """
        # Accept connection if callback provided
        if accept_callback:
            await accept_callback()

        self.connection_hub.add_connection(websocket)

        try:
            # Send initial system stats
            try:
                stats = self.get_system_stats()
                await websocket.send_text(json.dumps({
                    "type": "system_update",
                    "data": stats,
                    "timestamp": time.time()
                }))
            except Exception as e:
                self.logger.warning(f"Error sending initial stats: {e}")

            # Start monitoring task if needed
            await self.start_monitoring_task()

            # Keep connection alive
            while True:
                try:
                    if receive_callback:
                        message = await asyncio.wait_for(receive_callback(), timeout=60.0)
                    else:
                        # Default wait
                        await asyncio.sleep(60.0)
                        message = None

                    # Handle client messages
                    if message:
                        try:
                            msg_data = json.loads(message)
                            if msg_data.get("type") == "ping":
                                await websocket.send_text(json.dumps({"type": "pong"}))
                        except json.JSONDecodeError:
                            pass

                except asyncio.TimeoutError:
                    # Send periodic ping to keep connection alive
                    try:
                        await websocket.send_text(json.dumps({"type": "ping"}))
                    except Exception as e:
                        self.logger.warning(f"Failed to send ping to client {client_id}: {e}")
                        break

        except Exception as e:
            self.logger.info(f"System monitoring client {client_id} disconnected: {e}")
        finally:
            self.connection_hub.remove_connection(websocket)

            # Stop monitoring if no clients connected
            if not self.connection_hub.has_connections():
                await self.stop_monitoring_task()

    async def _monitor_system(self) -> None:
        """Background task to monitor system and broadcast updates."""
        self.logger.info("Starting system monitoring task")

        while self.connection_hub.has_connections():
            try:
                # Get system stats
                stats = self.get_system_stats()

                # Broadcast to all connected clients
                await self.connection_hub.broadcast(stats)

                # Wait for next update
                await asyncio.sleep(self.monitoring_interval)

            except asyncio.CancelledError:
                self.logger.info("System monitoring task cancelled")
                break
            except Exception as e:
                self.logger.error(f"Error in system monitoring task: {e}")
                await asyncio.sleep(self.monitoring_interval)

        self.logger.info("System monitoring task stopped")

    def _format_gpu_stats(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format GPU stats from SystemMonitor snapshot to API format.

        Args:
            snapshot: System snapshot from SystemMonitor

        Returns:
            Formatted GPU stats dictionary
        """
        vram = snapshot.get("vram", {})
        gpu = snapshot.get("gpu", {})

        if not gpu.get("available", False):
            return {"available": False}

        # Convert GB to MB for API compatibility
        return {
            "vram_used": int(vram.get("used_gb", 0) * 1024),
            "vram_free": int(vram.get("free_gb", 0) * 1024),
            "vram_total": int(vram.get("total_gb", 0) * 1024),
            "vram_usage_percent": vram.get("usage_percent", 0),
            "temperature": gpu.get("temperature_c", 0),
            "available": True
        }

    def _format_ram_stats(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format RAM stats from SystemMonitor snapshot to API format.

        Args:
            snapshot: System snapshot from SystemMonitor

        Returns:
            Formatted RAM stats dictionary
        """
        ram = snapshot.get("ram", {})

        if ram.get("total_gb", 0) == 0:
            return {"available": False}

        # Convert GB to MB for API compatibility
        return {
            "used": int(ram.get("used_gb", 0) * 1024),
            "free": int(ram.get("available_gb", 0) * 1024),
            "total": int(ram.get("total_gb", 0) * 1024),
            "usage_percent": ram.get("usage_percent", 0),
            "available": True
        }

    def _format_cpu_stats(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format CPU stats from SystemMonitor snapshot to API format.

        Args:
            snapshot: System snapshot from SystemMonitor

        Returns:
            Formatted CPU stats dictionary
        """
        cpu = snapshot.get("cpu", {})

        return {
            "usage_percent": cpu.get("usage_percent", 0),
            "core_count": cpu.get("core_count", 1),
            "available": True
        }

    def _execute_hook(
        self,
        hook: str,
        data: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Execute plugin hook.

        Args:
            hook: Hook definition to execute
            data: Initial data to pass to hook handlers

        Returns:
            Tuple of (result data, blocked flag)
        """
        if not self.plugins:
            return data, False

        try:
            context, _ = self.plugins.execute_hook(hook, initial_data=data)
            blocked = context.data.get("blocked", False)
            return context.data, blocked
        except Exception as e:
            self.logger.warning(f"Error executing hook {hook}: {e}")
            return data, False
