"""Regression: the download WebSocket must authenticate (admin-only).

Downloads are admin-only state; the progress stream would otherwise leak
download URLs, filenames and activity to any unauthenticated client. The
handler authenticates the query-string token before accepting the socket.
"""
from unittest.mock import Mock

import pytest

from src.features.downloads import routes as downloads_routes
from src.platform.security.user import User, AccountType
from src.platform.websocket.download_connection_hub import DownloadConnectionHub


def _user(account_type):
    return User(
        id="u-1", username="u", email="u@example.com",
        password_hash="h", account_type=account_type,
    )


class _FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.closed_code = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed_code = code


def _ws_endpoint(connection_hub):
    container = Mock()
    container.download_connection_hub = connection_hub
    router = downloads_routes.build_ws_router(container)
    return router.routes[0].endpoint


@pytest.mark.asyncio
async def test_missing_token_is_rejected(monkeypatch):
    monkeypatch.setattr(
        downloads_routes, "authenticate_websocket_token",
        lambda token: (None, "No token"),
    )
    ws = _FakeWebSocket()
    await _ws_endpoint(DownloadConnectionHub())(ws, client_id=None, token=None)
    assert ws.closed_code == 4001


@pytest.mark.asyncio
async def test_non_admin_is_rejected(monkeypatch):
    monkeypatch.setattr(
        downloads_routes, "authenticate_websocket_token",
        lambda token: (_user(AccountType.USER), None),
    )
    ws = _FakeWebSocket()
    await _ws_endpoint(DownloadConnectionHub())(ws, client_id=None, token="t")
    assert ws.closed_code == 4001


@pytest.mark.asyncio
async def test_admin_passes_gate(monkeypatch):
    monkeypatch.setattr(
        downloads_routes, "authenticate_websocket_token",
        lambda token: (_user(AccountType.ADMIN), None),
    )
    # Fail the connection right after the gate so the handler returns without
    # entering the receive loop; a False return proves the gate was passed.
    connection_hub = DownloadConnectionHub()

    async def _reject_connect(websocket, client_id):
        return False

    monkeypatch.setattr(connection_hub, "connect", _reject_connect)
    ws = _FakeWebSocket()
    await _ws_endpoint(connection_hub)(ws, client_id=None, token="t")
    assert ws.closed_code != 4001
