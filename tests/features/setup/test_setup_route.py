"""The /api/setup/status route: public, unauthenticated, minimal payload."""

from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.setup.routes import build_router


def _client(*, claimed: bool, policy: str = "closed", token_exists: bool = False) -> TestClient:
    instance_claim = Mock()
    instance_claim.is_claimed.return_value = claimed
    settings = Mock()
    settings.get_setting.return_value = policy
    claim_tokens = Mock()
    claim_tokens.exists.return_value = token_exists
    container = SimpleNamespace(
        instance_claim_repository=instance_claim,
        claim_token_manager=claim_tokens,
        settings_manager=settings,
    )
    app = FastAPI()
    app.include_router(build_router(container))
    return TestClient(app)


def test_status_is_public_and_minimal():
    client = _client(claimed=False, policy="open")

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
    client = _client(claimed=True, policy="closed")
    body = client.get("/api/setup/status").json()
    assert body["needs_owner"] is False
    assert body["registration_open"] is False
