import threading
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import WebSocketDisconnect

from src.features.notifications import routes as notification_routes
from src.platform.security.user import AccountType, User


def _auth(result):
    async def authenticate(websocket, scope=None):
        return result
    return authenticate


class _FakeWebSocket:
    scope = {}

    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def close(self, code=1000, reason=""):
        pass

    async def send_json(self, message):
        self.sent.append(message)

    async def receive_json(self):
        raise WebSocketDisconnect()


@pytest.mark.asyncio
async def test_connect_reports_the_unread_count_read_off_the_event_loop(monkeypatch):
    user = User(id="u-1", username="u", email="u@example.com", password_hash="h", account_type=AccountType.USER)
    monkeypatch.setattr(
        notification_routes, "authenticate_websocket", _auth((user, None)),
    )
    hub = Mock(connect=AsyncMock(), disconnect=Mock())
    monkeypatch.setattr(notification_routes, "notification_connection_hub", hub)
    threads = []
    controller = Mock()
    controller.repository.unread_count.side_effect = lambda user_id: threads.append(threading.get_ident()) or 7
    endpoint = notification_routes.build_ws_router(Mock(notification_controller=controller)).routes[0].endpoint
    websocket = _FakeWebSocket()

    await endpoint(websocket)

    assert websocket.sent[0]["type"] == "connection_established"
    assert websocket.sent[0]["unread_count"] == 7
    controller.repository.unread_count.assert_called_once_with("u-1")
    assert threads[0] != threading.get_ident()
    hub.disconnect.assert_called_once_with(websocket.sent[0]["client_id"])
