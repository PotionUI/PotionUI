"""Route-level tests for src.features.mcp.routes: token CRUD, the admin
per-user toggle, and the /api/mcp Bearer-auth + toggle gating in front of the
JSON-RPC endpoint. Mirrors tests/features/llm/tools/test_governance_routes.py's
stub-container-behind-a-bare-router pattern.

Mutations/toggle reads go through `src.features.mcp.operations` (formerly
`McpManager`) against real repositories/settings; the JSON-RPC dispatch
itself (`src.features.mcp.protocol.handle_method`, formerly
`McpProtocolManager`) is patched to a Mock, as `container.mcp_tool_collaborators`
carries no real tool registry here.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.mcp import operations, routes as mcp_routes
from src.features.mcp.repository import McpTokenRepository
from src.features.users.repository import UserRepository
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import AccountType, User
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import SettingsManager


def _user(account_type: AccountType, uid: str = "user-1") -> User:
    return User(username="u", email="u@example.com", password_hash="x", account_type=account_type, id=uid)


@pytest.fixture
def stack(mcp_db, monkeypatch):
    token_repository = McpTokenRepository()
    settings_manager = SettingsManager(SettingRepository())
    user_repository = UserRepository()
    mock_handle_method = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(mcp_routes, "handle_method", mock_handle_method)
    container = SimpleNamespace(
        mcp_token_repository=token_repository,
        settings_manager=settings_manager,
        mcp_tool_collaborators=Mock(),
        user_repository=user_repository,
    )
    return container, token_repository, settings_manager, user_repository, mock_handle_method


@pytest.fixture
def make_client(stack):
    container, token_repository, settings_manager, user_repository, mock_handle_method = stack

    def _make(user: User) -> TestClient:
        app = FastAPI()
        app.include_router(mcp_routes.build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: user
        app.dependency_overrides[get_current_admin_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    return _make, container, token_repository, settings_manager, user_repository, mock_handle_method


class TestTokenCrud:
    def test_create_then_list_round_trips(self, make_client):
        make, *_ = make_client
        client = make(_user(AccountType.USER))
        create_resp = client.post("/api/mcp/tokens", json={"name": "laptop"})
        assert create_resp.status_code == 200
        body = create_resp.json()
        assert body["success"] is True
        assert body["data"]["token"].startswith("pui_mcp_")
        assert body["data"]["name"] == "laptop"

        list_resp = client.get("/api/mcp/tokens")
        assert list_resp.status_code == 200
        listed = list_resp.json()["data"]
        assert len(listed) == 1
        assert listed[0]["id"] == body["data"]["id"]
        # The listing never carries the plaintext back.
        assert "token" not in listed[0]

    def test_create_rejects_empty_name(self, make_client):
        make, *_ = make_client
        client = make(_user(AccountType.USER))
        resp = client.post("/api/mcp/tokens", json={"name": "   "})
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_revoke_then_list_is_empty_of_owned_active_tokens(self, make_client):
        make, *_ = make_client
        client = make(_user(AccountType.USER))
        created = client.post("/api/mcp/tokens", json={"name": "a"}).json()["data"]
        resp = client.delete(f"/api/mcp/tokens/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["data"] == {"id": created["id"], "revoked": True}
        listed = client.get("/api/mcp/tokens").json()["data"]
        assert listed[0]["revoked_at"] is not None

    def test_revoke_unknown_token_is_404(self, make_client):
        make, *_ = make_client
        client = make(_user(AccountType.USER))
        resp = client.delete("/api/mcp/tokens/ghost")
        assert resp.status_code == 404

    def test_a_users_tokens_are_isolated_from_another_users(self, make_client):
        make, *_ = make_client
        owner = make(_user(AccountType.USER, uid="user-1"))
        owner.post("/api/mcp/tokens", json={"name": "mine"})

        other = make(_user(AccountType.USER, uid="user-2"))
        assert other.get("/api/mcp/tokens").json()["data"] == []

    def test_tokens_stay_listable_creatable_and_revocable_while_mcp_is_disabled(self, make_client, stack):
        """Token CRUD is never gated on either toggle - a user whose MCP
        access is off must still be able to see and manage their tokens. Only
        POST /api/mcp itself is gated (see TestMcpEndpointAuth)."""
        _container, _tokens, settings_manager, user_repository, _handle_method = stack
        real_user = user_repository.create(username="dana", email="dana@example.com", password_hash="x")
        operations.set_user_enabled(settings_manager, user_repository, real_user.id, False)
        # mcp_enabled defaults to False too (migration 129) - both flags off.
        assert operations.is_globally_enabled(settings_manager) is False

        make, *_ = make_client
        client = make(_user(AccountType.USER, uid=real_user.id))

        created = client.post("/api/mcp/tokens", json={"name": "still works"})
        assert created.status_code == 200
        assert created.json()["success"] is True
        token_id = created.json()["data"]["id"]

        listed = client.get("/api/mcp/tokens")
        assert listed.status_code == 200
        assert len(listed.json()["data"]) == 1

        revoked = client.delete(f"/api/mcp/tokens/{token_id}")
        assert revoked.status_code == 200
        assert revoked.json()["data"] == {"id": token_id, "revoked": True}


class TestStatus:
    def test_defaults_reflect_global_off_user_on(self, make_client):
        make, *_ = make_client
        client = make(_user(AccountType.USER))
        resp = client.get("/api/mcp/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data == {"enabled": False, "global_enabled": False, "user_enabled": True}

    def test_global_on_user_off(self, make_client, stack):
        _container, _tokens, settings_manager, user_repository, _handle_method = stack
        settings_manager.set_setting("mcp_enabled", True)
        real_user = user_repository.create(username="carol", email="carol@example.com", password_hash="x")
        operations.set_user_enabled(settings_manager, user_repository, real_user.id, False)

        make, *_ = make_client
        client = make(_user(AccountType.USER, uid=real_user.id))
        data = client.get("/api/mcp/status").json()["data"]
        assert data == {"enabled": False, "global_enabled": True, "user_enabled": False}

    def test_global_off_user_off(self, make_client, stack):
        _container, _tokens, settings_manager, user_repository, _handle_method = stack
        real_user = user_repository.create(username="eve", email="eve@example.com", password_hash="x")
        operations.set_user_enabled(settings_manager, user_repository, real_user.id, False)

        make, *_ = make_client
        client = make(_user(AccountType.USER, uid=real_user.id))
        data = client.get("/api/mcp/status").json()["data"]
        assert data == {"enabled": False, "global_enabled": False, "user_enabled": False}

    def test_both_enabled(self, make_client, stack):
        _container, _tokens, settings_manager, user_repository, _handle_method = stack
        settings_manager.set_setting("mcp_enabled", True)
        real_user = user_repository.create(username="frank", email="frank@example.com", password_hash="x")

        make, *_ = make_client
        client = make(_user(AccountType.USER, uid=real_user.id))
        data = client.get("/api/mcp/status").json()["data"]
        assert data == {"enabled": True, "global_enabled": True, "user_enabled": True}


class TestAdminUserToggle:
    def test_non_admin_cannot_read_the_toggle(self, stack):
        container, *_ = stack
        app = FastAPI()
        app.include_router(mcp_routes.build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.USER)
        # No override for get_current_admin_user -> its real 403 check runs.
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/mcp/admin/users/user-1")
        assert resp.status_code == 403

    def test_admin_can_read_and_set_the_toggle(self, make_client, stack):
        _container, _tokens, _settings, user_repository, _handle_method = stack
        real_user = user_repository.create(username="bob", email="bob@example.com", password_hash="x")
        make, *_ = make_client
        client = make(_user(AccountType.ADMIN))

        default = client.get(f"/api/mcp/admin/users/{real_user.id}")
        assert default.status_code == 200
        assert default.json()["data"]["enabled"] is True

        updated = client.put(f"/api/mcp/admin/users/{real_user.id}", json={"enabled": False})
        assert updated.status_code == 200
        assert updated.json()["data"]["enabled"] is False

        confirm = client.get(f"/api/mcp/admin/users/{real_user.id}")
        assert confirm.json()["data"]["enabled"] is False

    def test_toggle_for_unknown_user_is_404(self, make_client):
        make, *_ = make_client
        client = make(_user(AccountType.ADMIN))
        resp = client.put("/api/mcp/admin/users/ghost-user", json={"enabled": False})
        assert resp.status_code == 404


class TestMcpEndpointAuth:
    def _minted(self, stack, uid: str = "user-1"):
        _container, token_repository, _settings, user_repository, _handle_method = stack
        real_user = user_repository.create(username=f"u-{uid}", email=f"{uid}@example.com", password_hash="x")
        token, plaintext = operations.mint_token(token_repository, real_user.id, "cli")
        return real_user, token, plaintext

    def _client(self, stack):
        container, *_ = stack
        app = FastAPI()
        app.include_router(mcp_routes.build_router(container))
        return TestClient(app, raise_server_exceptions=False)

    def test_missing_bearer_header_is_401_with_www_authenticate(self, stack):
        client = self._client(stack)
        resp = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_garbage_token_is_401(self, stack):
        client = self._client(stack)
        resp = client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401

    def test_revoked_token_is_401(self, stack):
        _container, token_repository, _settings, _users, _handle_method = stack
        _real_user, token, plaintext = self._minted(stack)
        operations.revoke_token(token_repository, token.user_id, token.id)
        client = self._client(stack)
        resp = client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 401

    def test_global_disabled_is_403_even_with_a_good_token(self, stack):
        _container, _tokens, _settings, _users, _handle_method = stack
        _real_user, _token, plaintext = self._minted(stack)
        # mcp_enabled defaults to False (see migration 129) - nothing to flip.
        client = self._client(stack)
        resp = client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 403

    def test_user_disabled_is_403_even_when_globally_enabled(self, stack):
        _container, _tokens, settings_manager, user_repository, _handle_method = stack
        settings_manager.set_setting("mcp_enabled", True)
        real_user, _token, plaintext = self._minted(stack)
        operations.set_user_enabled(settings_manager, user_repository, real_user.id, False)

        client = self._client(stack)
        resp = client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 403

    def test_globally_and_per_user_enabled_reaches_the_protocol_manager(self, stack):
        _container, _tokens, settings_manager, _users, mock_handle_method = stack
        settings_manager.set_setting("mcp_enabled", True)
        _real_user, _token, plaintext = self._minted(stack)

        client = self._client(stack)
        resp = client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        mock_handle_method.assert_called_once()

    def test_notification_gets_202_with_no_body(self, stack):
        _container, _tokens, settings_manager, _users, mock_handle_method = stack
        mock_handle_method.return_value = None
        settings_manager.set_setting("mcp_enabled", True)
        _real_user, _token, plaintext = self._minted(stack)

        client = self._client(stack)
        resp = client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 202
