"""
Automation WebSocket Connection Manager.

Handles connections for the `/ws/automations` live run-status channel.
Unlike the per-user notification channel, automation run updates are
broadcast to every authenticated connection (there is no per-user
filtering) - keyed by `client_id` rather than `user_id`. Provides the
same sync-to-async bridge
(`schedule_send`) as `NotificationConnectionHub` so the engine's
`emit_ws` callback (itself already async) and any worker-thread callers can
push messages without awaiting a coroutine directly.
"""
import asyncio
import json
import logging
from typing import Dict, Optional
from fastapi import WebSocket

from src.platform.websocket.loop_dispatch import LoopDispatchMixin

logger = logging.getLogger(__name__)


class AutomationConnectionHub(LoopDispatchMixin):
    """Manages WebSocket connections for the automation run-status channel."""

    _DISPATCH_LABEL = "automation send"

    def __init__(self):
        # client_id -> WebSocket
        self.connections: Dict[str, WebSocket] = {}
        self._init_loop_dispatch()

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Accept a WebSocket connection and register it."""
        await websocket.accept()
        self.connections[client_id] = websocket
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        logger.debug(f"Automation client {client_id} connected")

    def disconnect(self, client_id: str) -> None:
        """Remove a client connection."""
        self.connections.pop(client_id, None)
        logger.debug(f"Automation client {client_id} disconnected")

    async def broadcast(self, message: dict) -> None:
        """Send a message to every connected client."""
        try:
            json_message = json.dumps(message)
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize automation message: {e}")
            return

        disconnected = []
        for client_id, websocket in list(self.connections.items()):
            try:
                await websocket.send_text(json_message)
            except Exception as e:
                logger.error(f"Failed to send automation message to client {client_id}: {e}")
                disconnected.append(client_id)

        for client_id in disconnected:
            self.disconnect(client_id)

    def schedule_send(self, message: dict) -> None:
        """Sync-to-async bridge: schedule a broadcast from non-async code."""
        self._schedule(lambda: self.broadcast(message))


# Global instance
automation_connection_hub = AutomationConnectionHub()
