"""
Notification WebSocket Connection Manager.

Handles per-user WebSocket connections for the notification bell/panel,
keyed by `user_id -> {client_id: WebSocket}` to support multiple tabs per
user. Also provides a sync-to-async bridge (`schedule_send`) so callers on
worker threads (e.g. generation completion) can push messages without
awaiting a coroutine directly.
"""
import asyncio
import json
import logging
from typing import Dict, Optional
from fastapi import WebSocket

from src.platform.websocket.loop_dispatch import LoopDispatchMixin

logger = logging.getLogger(__name__)


class NotificationConnectionManager(LoopDispatchMixin):
    """Manages WebSocket connections for the per-user notification channel."""

    _DISPATCH_LABEL = "notification send"

    def __init__(self):
        # user_id -> {client_id: WebSocket}
        self.connections: Dict[str, Dict[str, WebSocket]] = {}
        self._init_loop_dispatch()

    async def connect(self, websocket: WebSocket, user_id: str, client_id: str) -> None:
        """Accept a WebSocket connection and register it for the user."""
        await websocket.accept()
        self.connections.setdefault(user_id, {})[client_id] = websocket
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        logger.debug(f"Notification client {client_id} connected for user {user_id}")

    def disconnect(self, user_id: str, client_id: str) -> None:
        """Remove a client connection."""
        user_conns = self.connections.get(user_id)
        if not user_conns:
            return
        user_conns.pop(client_id, None)
        if not user_conns:
            self.connections.pop(user_id, None)
        logger.debug(f"Notification client {client_id} disconnected for user {user_id}")

    async def send_to_user(self, user_id: str, message: dict) -> None:
        """Send a message to all of a user's connected clients (all tabs)."""
        user_conns = self.connections.get(user_id)
        if not user_conns:
            return

        try:
            json_message = json.dumps(message)
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize notification message: {e}")
            return

        disconnected = []
        for client_id, websocket in list(user_conns.items()):
            try:
                await websocket.send_text(json_message)
            except Exception as e:
                logger.error(f"Failed to send notification to client {client_id}: {e}")
                disconnected.append(client_id)

        for client_id in disconnected:
            self.disconnect(user_id, client_id)

    async def broadcast(self, message: dict) -> None:
        """Send a message to every connected user."""
        for user_id in list(self.connections.keys()):
            await self.send_to_user(user_id, message)

    def schedule_send(self, user_id: Optional[str], message: dict) -> None:
        """Sync-to-async bridge: schedule a send from non-async code.
        `user_id=None` broadcasts to all connected users."""
        def _make_coro():
            return self.broadcast(message) if user_id is None else self.send_to_user(user_id, message)

        self._schedule(_make_coro)


# Global instance
notification_connection_manager = NotificationConnectionManager()
