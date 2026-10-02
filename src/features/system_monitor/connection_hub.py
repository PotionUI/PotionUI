"""
WebSocket connection management for system monitoring.

This module provides framework-agnostic WebSocket connection management
for broadcasting system monitoring updates to connected clients.
"""
from typing import Callable, Optional, Protocol, List, Dict, Any, runtime_checkable
import asyncio
import logging
import json
import time

# A client that cannot absorb a frame within this deadline is treated as dead and
# dropped: without it one stalled socket holds up every other client's update.
SEND_TIMEOUT_SECONDS = 2.0
MAX_CONCURRENT_SENDS = 16
REVOKED_CLOSE_CODE = 4003
REVOKED_CLOSE_REASON = "System monitor is restricted to administrators"


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
        self._access_checks: Dict[int, Callable[[], bool]] = {}
        self.logger = logging.getLogger(__name__)

    def add_connection(
        self,
        websocket: WebSocketProtocol,
        still_allowed: Optional[Callable[[], bool]] = None,
    ) -> None:
        """
        Add a new WebSocket connection to the manager.

        Args:
            websocket: WebSocket connection conforming to WebSocketProtocol
            still_allowed: Optional check re-run before every broadcast; a
                connection that fails it is closed instead of sent to
        """
        self.active_connections.append(websocket)
        if still_allowed is not None:
            self._access_checks[id(websocket)] = still_allowed
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
        self._access_checks.pop(id(websocket), None)
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
        connections = await self._drop_revoked(list(self.active_connections))
        if not connections:
            return []

        payload = json.dumps({
            "type": "system_update",
            "data": message,
            "timestamp": time.time()
        })

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SENDS)

        async def send(connection: WebSocketProtocol) -> None:
            async with semaphore:
                await asyncio.wait_for(
                    connection.send_text(payload),
                    timeout=SEND_TIMEOUT_SECONDS
                )

        results = await asyncio.gather(
            *(send(connection) for connection in connections),
            return_exceptions=True
        )

        disconnected_clients: List[WebSocketProtocol] = []
        for connection, result in zip(connections, results):
            if isinstance(result, BaseException):
                self.logger.warning(f"Failed to send system update to client: {result}")
                disconnected_clients.append(connection)

        for client in disconnected_clients:
            self.remove_connection(client)

        return disconnected_clients

    async def _drop_revoked(self, connections: List[WebSocketProtocol]) -> List[WebSocketProtocol]:
        checked = [c for c in connections if id(c) in self._access_checks]
        if not checked:
            return connections

        verdicts = await asyncio.gather(
            *(asyncio.to_thread(self._access_checks[id(c)]) for c in checked),
            return_exceptions=True
        )
        revoked = [c for c, verdict in zip(checked, verdicts) if verdict is not True]
        for connection in revoked:
            self.remove_connection(connection)
            close = getattr(connection, "close", None)
            if close is None:
                continue
            try:
                await close(code=REVOKED_CLOSE_CODE, reason=REVOKED_CLOSE_REASON)
            except Exception as e:
                self.logger.debug(f"Closing revoked system monitoring client failed: {e}")
        return [c for c in connections if c not in revoked]

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
