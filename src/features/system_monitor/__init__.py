"""
System monitoring core module.

Provides system monitoring functionality including:
- SystemMonitorCoordinator: Main coordinator for system monitoring operations
- MonitoringConnectionHub: WebSocket connection management for broadcasts
"""
from src.features.system_monitor.coordinator import SystemMonitorCoordinator
from src.features.system_monitor.connection_hub import (
    MonitoringConnectionHub,
    WebSocketProtocol
)

__all__ = [
    "SystemMonitorCoordinator",
    "MonitoringConnectionHub",
    "WebSocketProtocol"
]
