"""Tests for BaseConnectionHub and its use by the admin/download managers.

Admin and download connection managers used to carry byte-identical
connect/disconnect/send_to_client/broadcast/send_notification/is_client_connected
bodies; these tests exercise the shared base implementation directly and
confirm the subclasses that need more than a flat registry (download's
subscription bookkeeping, its cross-loop `_dispatch` hop) still get it.
"""
import json
import pytest
from unittest.mock import AsyncMock
from fastapi import WebSocket

from src.platform.websocket.base_connection_hub import BaseConnectionHub
from src.platform.websocket.admin_connection_hub import AdminConnectionHub
from src.platform.websocket.download_connection_hub import DownloadConnectionHub


class TestBaseConnectionHub:

    @pytest.fixture
    def manager(self):
        return BaseConnectionHub()

    @pytest.fixture
    def mock_websocket(self):
        return AsyncMock(spec=WebSocket)

    def test_init(self, manager):
        assert manager.active_connections == {}

    @pytest.mark.asyncio
    async def test_connect_success(self, manager, mock_websocket):
        result = await manager.connect(mock_websocket, "client-1")

        assert result is True
        mock_websocket.accept.assert_called_once()
        assert manager.active_connections["client-1"] is mock_websocket

    @pytest.mark.asyncio
    async def test_connect_failure(self, manager, mock_websocket):
        mock_websocket.accept.side_effect = Exception("boom")

        result = await manager.connect(mock_websocket, "client-1")

        assert result is False
        assert "client-1" not in manager.active_connections

    def test_disconnect_removes_client(self, manager, mock_websocket):
        manager.active_connections["client-1"] = mock_websocket

        manager.disconnect("client-1")

        assert "client-1" not in manager.active_connections

    def test_disconnect_nonexistent_client_is_noop(self, manager):
        manager.disconnect("client-1")
        assert "client-1" not in manager.active_connections

    @pytest.mark.asyncio
    async def test_send_to_client_not_connected(self, manager):
        assert await manager.send_to_client("client-1", {"type": "x"}) is False

    @pytest.mark.asyncio
    async def test_send_to_client_success(self, manager, mock_websocket):
        manager.active_connections["client-1"] = mock_websocket
        message = {"type": "x"}

        assert await manager.send_to_client("client-1", message) is True
        mock_websocket.send_text.assert_called_once_with(json.dumps(message))

    @pytest.mark.asyncio
    async def test_send_to_client_disconnects_on_failure(self, manager, mock_websocket):
        manager.active_connections["client-1"] = mock_websocket
        mock_websocket.send_text.side_effect = Exception("broken pipe")

        assert await manager.send_to_client("client-1", {"type": "x"}) is False
        assert "client-1" not in manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_no_connections_is_noop(self, manager):
        await manager.broadcast({"type": "x"})

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_clients(self, manager):
        ws1, ws2 = AsyncMock(spec=WebSocket), AsyncMock(spec=WebSocket)
        manager.active_connections["c1"] = ws1
        manager.active_connections["c2"] = ws2
        message = {"type": "x"}

        await manager.broadcast(message)

        ws1.send_text.assert_called_once_with(json.dumps(message))
        ws2.send_text.assert_called_once_with(json.dumps(message))

    @pytest.mark.asyncio
    async def test_broadcast_disconnects_failed_clients(self, manager, mock_websocket):
        manager.active_connections["client-1"] = mock_websocket
        mock_websocket.send_text.side_effect = Exception("broken pipe")

        await manager.broadcast({"type": "x"})

        assert "client-1" not in manager.active_connections

    @pytest.mark.asyncio
    async def test_send_notification_broadcasts_shaped_message(self, manager, mock_websocket):
        manager.active_connections["client-1"] = mock_websocket

        await manager.send_notification("cat", "info", "Title", "Body")

        sent = json.loads(mock_websocket.send_text.call_args[0][0])
        assert sent == {
            "type": "notification",
            "category": "cat",
            "level": "info",
            "title": "Title",
            "message": "Body",
        }

    def test_is_client_connected(self, manager, mock_websocket):
        manager.active_connections["client-1"] = mock_websocket
        assert manager.is_client_connected("client-1") is True
        assert manager.is_client_connected("client-2") is False


class TestAdminConnectionHubInheritsBase:
    """Admin has no logic of its own left - these confirm inheritance wires
    connect/broadcast through, not just that AdminConnectionHub exists."""

    @pytest.mark.asyncio
    async def test_connect_and_broadcast_go_through_base(self):
        manager = AdminConnectionHub()
        websocket = AsyncMock(spec=WebSocket)

        assert await manager.connect(websocket, "client-1") is True

        await manager.broadcast({"type": "x"})
        websocket.send_text.assert_called_once_with(json.dumps({"type": "x"}))


class TestDownloadConnectionHubOverrides:
    """The behavior download.py cannot inherit unmodified: subscription
    cleanup on disconnect, and the cross-loop `_dispatch` hop in broadcast."""

    @pytest.mark.asyncio
    async def test_disconnect_clears_download_subscriptions(self):
        manager = DownloadConnectionHub()
        websocket = AsyncMock(spec=WebSocket)
        await manager.connect(websocket, "client-1")
        await manager.subscribe_to_download("client-1", "dl-1")
        manager.subscribe_to_all_downloads("client-1")

        manager.disconnect("client-1")

        assert "client-1" not in manager.active_connections
        assert "dl-1" not in manager.download_subscriptions
        assert "client-1" not in manager.all_downloads_subscribers

    @pytest.mark.asyncio
    async def test_broadcast_goes_through_dispatch(self, monkeypatch):
        manager = DownloadConnectionHub()
        websocket = AsyncMock(spec=WebSocket)
        await manager.connect(websocket, "client-1")

        calls = []
        original_dispatch = manager._dispatch

        async def _tracking_dispatch(coro):
            calls.append(coro)
            return await original_dispatch(coro)

        monkeypatch.setattr(manager, "_dispatch", _tracking_dispatch)

        await manager.broadcast({"type": "x"})

        assert len(calls) == 1
        websocket.send_text.assert_called_once_with(json.dumps({"type": "x"}))
