"""Route-level tests for LLM tool governance: admin gating + config-existence
gating on /api/llm/configurations/{id}/toolset, and the
locked/admin-disabled/unknown-tool status codes on the user preference route.
Mirrors tests/features/llm/test_admin_gating.py's stub-controller-behind-a-
bare-router pattern.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.llm.tools import governance_routes as gov_mod
from src.features.llm.tools.governance import ToolGovernanceEditor, ToolGovernanceRepository
from src.platform.http.base_controller import APIResponse
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User


def _user(account_type: AccountType, uid: str = "user-1") -> User:
    return User(username="u", email="u@example.com", password_hash="x", account_type=account_type, id=uid)


def _llm_repository(config_exists: bool = True) -> Mock:
    llm_repository = Mock()
    llm_repository.config_repo.exists.return_value = config_exists
    return llm_repository


@pytest.fixture
def make_client():
    def _make(user: User, controller=None) -> TestClient:
        app = FastAPI()
        container = SimpleNamespace(tool_governance_controller=controller or Mock())
        app.include_router(gov_mod.build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)
    return _make


class TestAdminGating:
    def test_non_admin_cannot_list_admin_toolset(self, make_client):
        client = make_client(_user(AccountType.USER))
        resp = client.get("/api/llm/configurations/cfg-a/toolset")
        assert resp.status_code == 403

    def test_non_admin_cannot_update_admin_toolset(self, make_client):
        client = make_client(_user(AccountType.USER))
        resp = client.put("/api/llm/configurations/cfg-a/toolset/search_gallery", json={"enabled": False})
        assert resp.status_code == 403

    def test_admin_passes_the_gate(self, make_client):
        stub = Mock()
        stub.get_admin_toolset.return_value = APIResponse(success=True, data=[])
        client = make_client(_user(AccountType.ADMIN), controller=stub)
        resp = client.get("/api/llm/configurations/cfg-a/toolset")
        assert resp.status_code == 200
        stub.get_admin_toolset.assert_called_once_with("cfg-a")

    def test_any_user_can_read_their_own_preferences(self, make_client):
        stub = Mock()
        stub.get_user_toolset_preferences.return_value = APIResponse(success=True, data=[])
        client = make_client(_user(AccountType.USER), controller=stub)
        resp = client.get("/api/llm/toolset/preferences", params={"llm_config_id": "cfg-a"})
        assert resp.status_code == 200

    def test_llm_config_id_is_required_on_preferences(self, make_client):
        client = make_client(_user(AccountType.USER))
        resp = client.get("/api/llm/toolset/preferences")
        assert resp.status_code == 422


class TestAdminToolsetConfigGating:
    """Exercises the real controller against a stub llm_repository, so the
    config-not-found 404 is proven end to end."""

    def _client(self, config_exists: bool):
        repo = Mock(spec=ToolGovernanceRepository)
        registry = Mock()
        registry.get_all.return_value = []
        llm_repository = _llm_repository(config_exists)
        manager = ToolGovernanceEditor(repository=repo, tool_registry=registry)
        controller = gov_mod.ToolGovernanceController(
            repository=repo, manager=manager, tool_registry=registry, llm_repository=llm_repository
        )
        app = FastAPI()
        container = SimpleNamespace(tool_governance_controller=controller)
        app.include_router(gov_mod.build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
        return TestClient(app, raise_server_exceptions=False)

    def test_get_toolset_for_unknown_config_is_404(self):
        client = self._client(config_exists=False)
        resp = client.get("/api/llm/configurations/ghost-cfg/toolset")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "llm_config_not_found"

    def test_get_toolset_for_known_config_is_200(self):
        client = self._client(config_exists=True)
        resp = client.get("/api/llm/configurations/cfg-a/toolset")
        assert resp.status_code == 200

    def test_update_toolset_for_unknown_config_is_404(self):
        client = self._client(config_exists=False)
        resp = client.put("/api/llm/configurations/ghost-cfg/toolset/search_gallery", json={"enabled": False})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "llm_config_not_found"


class TestUserPreferenceStatusCodes:
    """Exercises the real controller (not a stub) against a real
    ToolGovernanceEditor, so the exception -> status-code mapping is proven
    end to end rather than by construction."""

    def _client(self, config=None):
        repo = Mock(spec=ToolGovernanceRepository)
        repo.get_config.return_value = config
        registry = Mock()
        registry.get.return_value = object()  # tool is registered
        manager = ToolGovernanceEditor(repository=repo, tool_registry=registry)
        controller = gov_mod.ToolGovernanceController(
            repository=repo, manager=manager, tool_registry=registry, llm_repository=_llm_repository()
        )

        app = FastAPI()
        container = SimpleNamespace(tool_governance_controller=controller)
        app.include_router(gov_mod.build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.USER)
        return TestClient(app, raise_server_exceptions=False), repo

    def test_disabling_a_tool_locked_by_the_named_config_is_409(self):
        client, repo = self._client(config={"enabled": True, "locked": True})
        resp = client.put(
            "/api/llm/toolset/preferences/search_gallery", json={"disabled": True, "llm_config_id": "cfg-a"}
        )
        assert resp.status_code == 409
        repo.set_user_disabled.assert_not_called()

    def test_disabling_a_tool_the_named_config_admin_disabled_is_403(self):
        client, repo = self._client(config={"enabled": False, "locked": False})
        resp = client.put(
            "/api/llm/toolset/preferences/search_gallery", json={"disabled": True, "llm_config_id": "cfg-a"}
        )
        assert resp.status_code == 403
        repo.set_user_disabled.assert_not_called()

    def test_opting_out_with_no_config_named_is_a_validation_error(self):
        client, _repo = self._client(config={"enabled": True, "locked": False})
        resp = client.put("/api/llm/toolset/preferences/search_gallery", json={"disabled": True})
        assert resp.status_code == 422

    def test_unknown_tool_is_404(self):
        repo = Mock(spec=ToolGovernanceRepository)
        repo.get_config.return_value = None  # no governance row for it either
        registry = Mock()
        registry.get.return_value = None  # not registered
        manager = ToolGovernanceEditor(repository=repo, tool_registry=registry)
        controller = gov_mod.ToolGovernanceController(
            repository=repo, manager=manager, tool_registry=registry, llm_repository=_llm_repository()
        )
        app = FastAPI()
        container = SimpleNamespace(tool_governance_controller=controller)
        app.include_router(gov_mod.build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.USER)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.put("/api/llm/toolset/preferences/ghost", json={"disabled": True, "llm_config_id": "cfg-a"})
        assert resp.status_code == 404

    def test_opting_out_of_an_unlocked_enabled_named_config_tool_succeeds(self):
        client, repo = self._client(config={"enabled": True, "locked": False})
        resp = client.put(
            "/api/llm/toolset/preferences/search_gallery", json={"disabled": True, "llm_config_id": "cfg-a"}
        )
        assert resp.status_code == 200
        repo.set_user_disabled.assert_called_once_with("user-1", "search_gallery", True)


class TestUserPreferencesGetConfigScoping:
    def _client(self, admin_config_by_config: dict):
        repo = Mock(spec=ToolGovernanceRepository)
        repo.get_all_config.side_effect = lambda config_id: admin_config_by_config.get(config_id, {})
        repo.get_user_disabled.return_value = set()
        tool = Mock()
        tool.name = "search_gallery"
        tool.label = "Search Gallery"
        tool.user_description = ""
        registry = Mock()
        registry.get_all.return_value = [tool]
        manager = ToolGovernanceEditor(repository=repo, tool_registry=registry)
        controller = gov_mod.ToolGovernanceController(
            repository=repo, manager=manager, tool_registry=registry, llm_repository=_llm_repository()
        )
        app = FastAPI()
        container = SimpleNamespace(tool_governance_controller=controller)
        app.include_router(gov_mod.build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.USER)
        return TestClient(app, raise_server_exceptions=False), repo

    def test_llm_config_id_is_required(self):
        client, repo = self._client({})
        resp = client.get("/api/llm/toolset/preferences")
        assert resp.status_code == 422
        repo.get_all_config.assert_not_called()

    def test_llm_config_id_scopes_the_admin_disabled_filter(self):
        client, repo = self._client({"cfg-a": {"search_gallery": {"enabled": False, "locked": False}}})
        resp = client.get("/api/llm/toolset/preferences", params={"llm_config_id": "cfg-a"})
        assert resp.status_code == 200
        assert resp.json()["data"] == []
        repo.get_all_config.assert_called_once_with("cfg-a")

    def test_a_different_llm_config_id_sees_the_tool(self):
        client, repo = self._client({"cfg-a": {"search_gallery": {"enabled": False, "locked": False}}})
        resp = client.get("/api/llm/toolset/preferences", params={"llm_config_id": "cfg-b"})
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["data"]]
        assert names == ["search_gallery"]
