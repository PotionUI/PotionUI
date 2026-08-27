"""Download-specific WebSocket connection manager.

Handles WebSocket connections for real-time download progress updates,
status notifications, and queue management feedback.
"""
import asyncio
import json
import logging
from typing import Awaitable, Dict, List, Optional, TypeVar
from fastapi import WebSocket

from src.platform.websocket.base_connection_hub import BaseConnectionHub

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class DownloadConnectionHub(BaseConnectionHub):
    """Manages WebSocket connections for download progress and status updates."""

    _CONNECTION_LABEL = "Download client"

    def __init__(self):
        super().__init__()
        # Download subscriptions: download_id -> [client_ids]
        self.download_subscriptions: Dict[str, List[str]] = {}
        # Clients subscribed to ALL download updates
        self.all_downloads_subscribers: set = set()
        # The loop that accepted these WebSocket connections (always the
        # app's real request-handling loop - `connect()` only ever runs
        # inside a live WS route handler). The download worker's queue
        # consumer runs on its own persistent background loop
        # (see src/features/downloads/persistent_loop.py), so broadcasts
        # triggered from inside the worker run on a *different* loop/thread
        # than the one that owns these sockets. `_dispatch` hops back onto
        # this loop when needed instead of touching a socket from the wrong
        # thread.
        self._app_loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket, client_id: str) -> bool:
        accepted = await super().connect(websocket, client_id)
        if accepted:
            self._app_loop = asyncio.get_running_loop()
        return accepted

    async def _dispatch(self, coro: Awaitable[_T]) -> _T:
        """Run `coro` on the loop that owns the live WebSocket connections,
        hopping across threads if the caller is on a different loop (e.g.
        the download worker's own persistent loop). A plain `await coro` if
        no connection has ever been accepted yet, or the caller already is
        on the app loop.
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if self._app_loop is not None and running is not self._app_loop and self._app_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self._app_loop)
            return await asyncio.wrap_future(future)
        return await coro

    def disconnect(self, client_id: str) -> None:
        """Disconnect a client and clean up all its subscriptions."""
        super().disconnect(client_id)

        # Remove from all downloads subscribers
        self.all_downloads_subscribers.discard(client_id)

        # Remove from download subscriptions
        for download_id, clients in list(self.download_subscriptions.items()):
            if client_id in clients:
                clients.remove(client_id)
            # Clean up empty subscription lists
            if not clients:
                del self.download_subscriptions[download_id]

    async def subscribe_to_download(self, client_id: str, download_id: str) -> bool:
        """Subscribe a client to updates for a specific download.

        Args:
            client_id: The client to subscribe
            download_id: The download to subscribe to

        Returns:
            True if subscription was created, False if client not connected
        """
        if client_id not in self.active_connections:
            return False

        if download_id not in self.download_subscriptions:
            self.download_subscriptions[download_id] = []

        if client_id not in self.download_subscriptions[download_id]:
            self.download_subscriptions[download_id].append(client_id)
            logger.debug(f"Client {client_id} subscribed to download {download_id}")

        return True

    async def unsubscribe_from_download(self, client_id: str, download_id: str) -> bool:
        """Unsubscribe a client from a specific download's updates.

        Args:
            client_id: The client to unsubscribe
            download_id: The download to unsubscribe from

        Returns:
            True if unsubscribed, False if subscription not found
        """
        if download_id in self.download_subscriptions:
            if client_id in self.download_subscriptions[download_id]:
                self.download_subscriptions[download_id].remove(client_id)
                logger.debug(f"Client {client_id} unsubscribed from download {download_id}")
                return True
        return False

    def subscribe_to_all_downloads(self, client_id: str) -> bool:
        """Subscribe a client to updates for all downloads.

        Args:
            client_id: The client to subscribe

        Returns:
            True if subscription was created, False if client not connected
        """
        if client_id not in self.active_connections:
            return False
        self.all_downloads_subscribers.add(client_id)
        logger.debug(f"Client {client_id} subscribed to all downloads")
        return True

    async def broadcast(self, message: dict) -> None:
        """Broadcast a message to all connected clients.

        Args:
            message: The message dict to serialize and broadcast
        """
        if not self.active_connections:
            return

        try:
            json_message = json.dumps(message)
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize broadcast message: {e}")
            return

        await self._dispatch(self._broadcast_raw(json_message))

    async def broadcast_to_download(self, download_id: str, message: dict) -> None:
        """Broadcast a message to clients subscribed to a specific download or to all downloads.

        Args:
            download_id: The download ID to target
            message: The message dict to serialize and send
        """
        # Get clients subscribed to this specific download
        specific_subscribers = set()
        if download_id in self.download_subscriptions:
            specific_subscribers = set(self.download_subscriptions[download_id])

        # Combine with clients subscribed to all downloads
        all_client_ids = specific_subscribers | self.all_downloads_subscribers

        if not all_client_ids:
            return

        try:
            json_message = json.dumps(message)
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize download message: {e}")
            return

        await self._dispatch(self._broadcast_to_download_raw(all_client_ids, json_message))

    async def _broadcast_to_download_raw(self, all_client_ids: set, json_message: str) -> None:
        disconnected_clients = []
        for client_id in all_client_ids:
            if client_id in self.active_connections:
                try:
                    await self.active_connections[client_id].send_text(json_message)
                except Exception as e:
                    logger.error(f"Failed to send to client {client_id}: {e}")
                    disconnected_clients.append(client_id)

        for client_id in disconnected_clients:
            self.disconnect(client_id)

    async def send_download_progress(
        self,
        download_id: str,
        progress: float,
        downloaded_bytes: int,
        total_bytes: Optional[int],
        speed_bytes_per_sec: Optional[float],
        filename: str
    ) -> None:
        """Send a download progress update to subscribed clients.

        Args:
            download_id: The download being updated
            progress: Progress value from 0.0 to 1.0
            downloaded_bytes: Number of bytes downloaded so far
            total_bytes: Total file size in bytes (None if unknown)
            speed_bytes_per_sec: Current download speed (None if unknown)
            filename: Name of the file being downloaded
        """
        message = {
            'type': 'download_progress',
            'download_id': download_id,
            'progress': round(progress, 4),
            'downloaded_bytes': downloaded_bytes,
            'total_bytes': total_bytes,
            'speed_bytes_per_sec': round(speed_bytes_per_sec, 2) if speed_bytes_per_sec else None,
            'filename': filename
        }
        await self.broadcast_to_download(download_id, message)

    async def send_download_status(
        self,
        download_id: str,
        status: str,
        filename: str,
        error_message: Optional[str] = None,
        path: Optional[str] = None
    ) -> None:
        """Send a download status change notification.

        Args:
            download_id: The download whose status changed
            status: New status string (e.g. 'started', 'completed', 'failed', 'cancelled')
            filename: Name of the file
            error_message: Optional error details if status is 'failed'
            path: Optional destination path if status is 'completed'
        """
        message = {
            'type': f'download_{status}',
            'download_id': download_id,
            'status': status,
            'filename': filename
        }
        if error_message:
            message['error'] = error_message
        if path:
            message['path'] = path

        await self.broadcast_to_download(download_id, message)

    async def send_download_queued(
        self,
        download_id: str,
        filename: str,
        position: int
    ) -> None:
        """Send notification that a download has been queued.

        Args:
            download_id: The newly queued download's ID
            filename: Name of the file to be downloaded
            position: Position in the download queue
        """
        message = {
            'type': 'download_queued',
            'download_id': download_id,
            'filename': filename,
            'position': position
        }
        await self.broadcast(message)


# Module-level singleton, injected by the composition root (mirrors
# notification_connection_hub / automation_connection_hub).
download_connection_hub = DownloadConnectionHub()
