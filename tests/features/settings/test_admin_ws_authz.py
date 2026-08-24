"""Regression: the admin WebSocket (/ws/admin) must authenticate (admin-only).

It is the admin panel's real-time channel and was previously reachable with no
authentication at all. The handler now authenticates the query-string token and
requires an administrator before accepting the connection.
"""
import pytest

from src.features.settings import admin_websocket as admin_ws
from src.platform.security.user import User, AccountType


def _user(account_type):
    return User(
        id="u1", username="u", email="u@example.com",
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


@pytest.mark.asyncio
async def test_missing_token_rejected(monkeypatch):
    monkeypatch.setattr(admin_ws, "authenticate_websocket_token", lambda token: (None, "No token"))
    ws = _FakeWebSocket()
    await admin_ws.admin_websocket_endpoint(ws, client_id=None, token=None)
    assert ws.closed_code == 4001


@pytest.mark.asyncio
async def test_non_admin_rejected(monkeypatch):
    monkeypatch.setattr(
        admin_ws, "authenticate_websocket_token",
        lambda token: (_user(AccountType.USER), None),
    )
    ws = _FakeWebSocket()
    await admin_ws.admin_websocket_endpoint(ws, client_id=None, token="t")
    assert ws.closed_code == 4001


@pytest.mark.asyncio
async def test_admin_passes_gate(monkeypatch):
    monkeypatch.setattr(
        admin_ws, "authenticate_websocket_token",
        lambda token: (_user(AccountType.ADMIN), None),
    )
    # Fail the connection right after the gate so the handler returns without
    # entering the receive loop; a False return proves the gate was passed.
    async def _reject_connect(websocket, client_id):
        return False
    monkeypatch.setattr(admin_ws.admin_connection_manager, "connect", _reject_connect)
    ws = _FakeWebSocket()
    await admin_ws.admin_websocket_endpoint(ws, client_id=None, token="t")
    assert ws.closed_code != 4001
