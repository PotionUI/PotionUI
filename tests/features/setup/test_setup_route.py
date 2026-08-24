"""The /api/setup/status route: public, unauthenticated, minimal payload."""

from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.setup.dto import SetupStatus
from src.features.setup.routes import build_router


def _client(status: SetupStatus) -> TestClient:
    setup_manager = Mock()
    setup_manager.status.return_value = status
    container = SimpleNamespace(setup_manager=setup_manager)
    app = FastAPI()
    app.include_router(build_router(container))
    return TestClient(app)


def test_status_is_public_and_minimal():
    client = _client(SetupStatus(
        needs_owner=True, registration_open=True, claim_requires_token=False,
    ))

    # No Authorization header -> must still succeed (unauthenticated by design).
    response = client.get("/api/setup/status")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "needs_owner", "registration_open", "claim_requires_token"
    }
    assert body == {
        "needs_owner": True,
        "registration_open": True,
        "claim_requires_token": False,
    }


def test_status_reports_claimed_instance():
    client = _client(SetupStatus(
        needs_owner=False, registration_open=False, claim_requires_token=False,
    ))
    body = client.get("/api/setup/status").json()
    assert body["needs_owner"] is False
    assert body["registration_open"] is False
