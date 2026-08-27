"""Shared `client_id -> WebSocket` registry behavior for the admin and
download WebSocket managers - connect/disconnect/send/broadcast were
line-for-line identical between the two before this was factored out.

Subclasses that need more than a flat registry (per-download subscription
bookkeeping, a cross-loop dispatch hop, ...) override the relevant method and
call back into this base implementation - see `DownloadConnectionHub`.
"""
import json
import logging
from typing import Dict
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class BaseConnectionHub:
    """Manages a flat `client_id -> WebSocket` registry."""

    _CONNECTION_LABEL = "Client"

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str) -> bool:
        """Accept a WebSocket connection"""
        try:
            await websocket.accept()
            self.active_connections[client_id] = websocket
            logger.debug(
                f"{self._CONNECTION_LABEL} {client_id} connected. "
                f"Total connections: {len(self.active_connections)}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to accept {self._CONNECTION_LABEL} WebSocket for client {client_id}: {e}")
            return False

    def disconnect(self, client_id: str) -> None:
        """Disconnect a client"""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.debug(
                f"{self._CONNECTION_LABEL} {client_id} disconnected. "
                f"Total connections: {len(self.active_connections)}"
            )

    async def send_to_client(self, client_id: str, message: dict) -> bool:
        """Send message to a specific client"""
        if client_id not in self.active_connections:
            return False

        try:
            json_message = json.dumps(message)
            await self.active_connections[client_id].send_text(json_message)
            return True
        except Exception as e:
            logger.error(f"Failed to send message to client {client_id}: {e}")
            self.disconnect(client_id)
            return False

    async def broadcast(self, message: dict) -> None:
        """Broadcast message to all connected clients"""
        if not self.active_connections:
            return

        try:
            json_message = json.dumps(message)
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize broadcast message: {e}")
            return

        await self._broadcast_raw(json_message)

    async def _broadcast_raw(self, json_message: str) -> None:
        disconnected_clients = []
        for client_id, websocket in self.active_connections.items():
            try:
                await websocket.send_text(json_message)
            except Exception as e:
                logger.error(f"Failed to broadcast to client {client_id}: {e}")
                disconnected_clients.append(client_id)

        for client_id in disconnected_clients:
            self.disconnect(client_id)

    async def send_notification(
        self,
        category: str,
        level: str,
        title: str,
        message_text: str
    ) -> None:
        """Send generic notification to all connected clients"""
        message = {
            'type': 'notification',
            'category': category,
            'level': level,  # 'success', 'info', 'warning', 'error'
            'title': title,
            'message': message_text
        }
        await self.broadcast(message)

    def is_client_connected(self, client_id: str) -> bool:
        """Check if a client is connected"""
        return client_id in self.active_connections
