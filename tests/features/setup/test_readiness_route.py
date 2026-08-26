"""The /api/readiness route: authenticated, role-filtered, doctor-row shape."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.setup.routes import build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User


def _user(admin=False):
    return User(
        username="u",
        email="u@example.com",
        password_hash="x",
        account_type=AccountType.ADMIN if admin else AccountType.USER,
        id="u1",
    )


def _client(current_user=None) -> TestClient:
    """A client over the setup router. `current_user=None` leaves auth intact
    (so unauthenticated requests 401); passing a user overrides the auth
    dependency to simulate that caller."""
    backend_registry = MagicMock()
    backend_registry.get_all_backends.return_value = {}  # -> execution not_ready (deterministic)
    preset_manager = MagicMock()
    preset_manager.list_presets.return_value = []  # -> content not_ready (no model probing)
    model_repository = MagicMock()
    generation_repository = MagicMock()
    generation_repository.count_by_status.return_value = 0

    instance_claim_repository = MagicMock()
    instance_claim_repository.check_connection.return_value = None

    container = SimpleNamespace(
        setup_run_manager=Mock(),
        backend_registry=backend_registry,
        preset_manager=preset_manager,
        model_repository=model_repository,
        generation_repository=generation_repository,
        instance_claim_repository=instance_claim_repository,
        claim_token_manager=Mock(),
        settings_manager=Mock(),
    )
    app = FastAPI()
    app.include_router(build_router(container))
    if current_user is not None:
        app.dependency_overrides[get_current_active_user] = lambda: current_user
    return TestClient(app)


def test_readiness_requires_authentication():
    # No auth override and no token -> the dependency rejects the request.
    response = _client().get("/api/readiness")
    assert response.status_code == 401


def test_admin_payload_carries_actions_and_report_shape():
    response = _client(current_user=_user(admin=True)).get("/api/readiness")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"overall", "checks"}
    assert {c["area"] for c in body["checks"]} == {
        "service", "execution", "content", "generation_proven",
    }
    execution = next(c for c in body["checks"] if c["area"] == "execution")
    assert execution["status"] == "not_ready"
    assert execution["action"] and "Administration" in execution["action"]


def test_user_payload_is_role_filtered():
    response = _client(current_user=_user(admin=False)).get("/api/readiness")
    assert response.status_code == 200
    body = response.json()
    execution = next(c for c in body["checks"] if c["area"] == "execution")
    # Same verdict, but no admin action and an "ask your administrator" nudge.
    assert execution["status"] == "not_ready"
    assert execution["action"] is None
    assert "administrator" in execution["message"].lower()


def test_recipe_id_query_param_is_accepted():
    response = _client(current_user=_user(admin=True)).get("/api/readiness?recipe_id=abc")
    assert response.status_code == 200
    content = next(c for c in response.json()["checks"] if c["area"] == "content")
    assert content["code"] == "RECIPE_NOT_IMPLEMENTED"
