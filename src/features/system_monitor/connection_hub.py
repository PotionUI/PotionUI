"""
WebSocket connection management for system monitoring.

This module provides framework-agnostic WebSocket connection management
for broadcasting system monitoring updates to connected clients.
"""
from typing import Protocol, List, Dict, Any, runtime_checkable
import logging
import json
import time


@runtime_checkable
class WebSocketProtocol(Protocol):
    """Protocol for WebSocket-like connections."""

    async def send_text(self, data: str) -> None:
        """Send text data over the WebSocket connection."""
        ...


class MonitoringConnectionHub:
    """
    Manages WebSocket connections for system monitoring broadcasts.

    This class is framework-agnostic and works with any WebSocket implementation
    that conforms to the WebSocketProtocol interface.
    """

    def __init__(self):
        self.active_connections: List[WebSocketProtocol] = []
        self.logger = logging.getLogger(__name__)

    def add_connection(self, websocket: WebSocketProtocol) -> None:
        """
        Add a new WebSocket connection to the manager.

        Args:
            websocket: WebSocket connection conforming to WebSocketProtocol
        """
        self.active_connections.append(websocket)
        self.logger.info(
            f"System monitoring client connected. Total connections: {len(self.active_connections)}"
        )

    def remove_connection(self, websocket: WebSocketProtocol) -> None:
        """
        Remove a WebSocket connection from the manager.

        Args:
            websocket: WebSocket connection to remove
        """
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.logger.info(
            f"System monitoring client disconnected. Total connections: {len(self.active_connections)}"
        )

    async def broadcast(self, message: Dict[str, Any]) -> List[WebSocketProtocol]:
        """
        Broadcast a message to all connected clients.

        Args:
            message: Dictionary containing the message data to broadcast

        Returns:
            List of connections that failed and were removed
        """
        if not self.active_connections:
            return []

        # Create message payload
        message_payload = {
            "type": "system_update",
            "data": message,
            "timestamp": time.time()
        }

        # Send to all connected clients
        disconnected_clients: List[WebSocketProtocol] = []
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message_payload))
            except Exception as e:
                self.logger.warning(f"Failed to send system update to client: {e}")
                disconnected_clients.append(connection)

        # Remove disconnected clients
        for client in disconnected_clients:
            self.remove_connection(client)

        return disconnected_clients

    def has_connections(self) -> bool:
        """
        Check if there are any active connections.

        Returns:
            True if at least one connection is active
        """
        return len(self.active_connections) > 0

    def connection_count(self) -> int:
        """
        Get the number of active connections.

        Returns:
            Number of active WebSocket connections
        """
        return len(self.active_connections)
