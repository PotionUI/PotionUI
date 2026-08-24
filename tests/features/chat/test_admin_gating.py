"""Route-level tests for the chat admin session-debug endpoints.

Mirrors tests/features/llm/test_admin_gating.py: non-admins must be rejected
with 403, admins clear the gate and reach the controller.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.chat import routes as chat_mod
from src.platform.http.base_controller import APIResponse
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import User, AccountType


def _user(account_type: AccountType, uid: str = "user-1") -> User:
    return User(
        username="u",
        email="u@example.com",
        password_hash="x",
        account_type=account_type,
        id=uid,
    )


@pytest.fixture
def make_client():
    def _make(user: User, controller=None) -> TestClient:
        app = FastAPI()
        container = SimpleNamespace(chat_controller=controller or Mock())
        app.include_router(chat_mod.build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)
    return _make


class TestChatAdminGating:
    def test_non_admin_cannot_list_admin_sessions(self, make_client):
        client = make_client(_user(AccountType.USER))
        resp = client.get("/api/chat/admin/sessions")
        assert resp.status_code == 403

    def test_non_admin_cannot_get_admin_session_detail(self, make_client):
        client = make_client(_user(AccountType.USER))
        resp = client.get("/api/chat/admin/sessions/some-session")
        assert resp.status_code == 403

    def test_non_admin_cannot_clear_traces(self, make_client):
        client = make_client(_user(AccountType.USER))
        resp = client.delete("/api/chat/admin/traces")
        assert resp.status_code == 403

    def test_admin_passes_list_sessions_gate(self, make_client):
        stub = Mock()
        stub.list_admin_sessions.return_value = APIResponse(
            success=True, data={"sessions": [], "total": 0}
        )
        client = make_client(_user(AccountType.ADMIN), controller=stub)
        resp = client.get("/api/chat/admin/sessions")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        stub.list_admin_sessions.assert_called_once()

    def test_admin_passes_session_detail_gate(self, make_client):
        stub = Mock()
        stub.get_admin_session_detail.return_value = APIResponse(
            success=True, data={"session": {}, "traces": []}
        )
        client = make_client(_user(AccountType.ADMIN), controller=stub)
        resp = client.get("/api/chat/admin/sessions/session-1")
        assert resp.status_code == 200
        stub.get_admin_session_detail.assert_called_once_with("session-1")

    def test_admin_passes_clear_traces_gate(self, make_client):
        stub = Mock()
        stub.clear_traces.return_value = APIResponse(success=True, data={"deleted": 3})
        client = make_client(_user(AccountType.ADMIN), controller=stub)
        resp = client.delete("/api/chat/admin/traces")
        assert resp.status_code == 200
        stub.clear_traces.assert_called_once()
