"""Route-level tests for LLM admin gating and API-key redaction.

These verify the security contract added in the Stage 6 pass:
- Global LLM configuration and user-assignment endpoints require an admin.
- User-scoped endpoints (`/configurations/my`) remain available to any user.
- `LLMConfigResponse` never carries the API key; it reports `api_key_set`.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.llm import routes as llm_mod
from src.platform.http.base_controller import APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.llm.dto import LLMConfigResponse
from src.platform.security.user import User, AccountType


def _user(account_type: AccountType, uid: str = "user-1") -> User:
    return User(
        username="u",
        email="u@example.com",
        password_hash="x",
        account_type=account_type,
        id=uid,
    )


_VALID_CONFIG_BODY = {
    "name": "Test",
    "type": "ollama",
    "enabled": True,
    "base_url": "http://localhost:11434",
    "model": "llama2",
    "system_message": "hi",
}


@pytest.fixture
def make_client():
    def _make(user: User, controller=None) -> TestClient:
        app = FastAPI()
        container = SimpleNamespace(llm_controller=controller or Mock())
        app.include_router(llm_mod.build_router(container))
        app.dependency_overrides[get_current_active_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)
    return _make


class TestLLMAdminGating:
    def test_non_admin_cannot_list_configurations(self, make_client):
        client = make_client(_user(AccountType.USER))
        resp = client.get("/api/llm/configurations")
        assert resp.status_code == 403

    def test_non_admin_cannot_create_configuration(self, make_client):
        client = make_client(_user(AccountType.USER))
        resp = client.post("/api/llm/configurations", json=_VALID_CONFIG_BODY)
        assert resp.status_code == 403

    def test_non_admin_cannot_list_user_assignments(self, make_client):
        client = make_client(_user(AccountType.USER))
        resp = client.get("/api/llm/user-assignments")
        assert resp.status_code == 403

    def test_admin_passes_configuration_gate(self, make_client):
        # The admin clears the gate and reaches the controller.
        stub = Mock()
        stub.get_all_configurations.return_value = APIResponse(success=True, data={"configurations": []})
        client = make_client(_user(AccountType.ADMIN), controller=stub)
        resp = client.get("/api/llm/configurations")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        stub.get_all_configurations.assert_called_once()

    def test_user_can_read_own_assignments(self, make_client):
        # A user may read their own assignments via the {user_id} route.
        stub = Mock()
        stub.get_user_llm_assignments.return_value = APIResponse(success=True, data={"user_id": "user-1", "llm_configs": []})
        client = make_client(_user(AccountType.USER, uid="user-1"), controller=stub)
        resp = client.get("/api/llm/user-assignments/user-1")
        assert resp.status_code == 200
        stub.get_user_llm_assignments.assert_called_once_with("user-1")

    def test_user_cannot_read_other_users_assignments(self, make_client):
        client = make_client(_user(AccountType.USER, uid="user-1"))
        resp = client.get("/api/llm/user-assignments/someone-else")
        assert resp.status_code == 403


class TestLLMConfigResponseRedaction:
    def test_response_has_api_key_set_not_api_key(self):
        resp = LLMConfigResponse(
            id="c1",
            name="Test",
            type="ollama",
            enabled=True,
            base_url="http://localhost:11434",
            api_key_set=True,
            model="llama2",
            system_message="hi",
            temperature=0.7,
            max_tokens=1000,
            timeout=30,
        )
        assert resp.api_key_set is True
        # The API key field must not exist on the response model at all.
        assert not hasattr(resp, "api_key")
        assert "api_key" not in resp.model_dump()
        assert "api_key" not in LLMConfigResponse.model_fields

    def test_api_key_kwarg_is_dropped(self):
        # Even if a caller passes api_key, it is ignored (extra fields dropped),
        # so it can never round-trip into a response body.
        resp = LLMConfigResponse(
            id="c1",
            name="Test",
            type="ollama",
            enabled=True,
            base_url="http://localhost:11434",
            api_key="super-secret",
            model="llama2",
            system_message="hi",
            temperature=0.7,
            max_tokens=1000,
            timeout=30,
        )
        assert "api_key" not in resp.model_dump()
        assert "super-secret" not in str(resp.model_dump())
