"""Router-level authorization tests for the backends API.

The write/action routes are admin-only; the read routes stay available to any
authenticated user (their responses are secret-redacted elsewhere). These drive
the real FastAPI router with the auth dependency overridden to a regular user.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.backends.routes import build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import User, AccountType


def _user(account_type):
    return User(
        id="u1", username="u", email="u@example.com",
        password_hash="h", account_type=account_type,
    )


def _make_client(user):
    bcm = Mock()
    bcm.get_backends.return_value = []
    bcm.get_default_backend_ids.return_value = {}
    registry = Mock()
    registry.backend_config_manager = bcm
    registry.refresh_backends = AsyncMock()
    container = SimpleNamespace(
        settings_manager=Mock(),
        backend_registry=registry,
        model_lifecycle_manager=Mock(),
    )
    app = FastAPI()
    app.include_router(build_router(container))

    async def _fake_active_user():
        return user

    app.dependency_overrides[get_current_active_user] = _fake_active_user
    return TestClient(app)


# (method, path, json body) for every admin-gated write/action route.
GATED = [
    ("post", "/api/backends", {"name": "x", "engine": "comfyui"}),
    ("put", "/api/backends/b1", {"name": "x"}),
    ("delete", "/api/backends/b1", None),
    ("post", "/api/backends/b1/test", None),
    ("post", "/api/backends/b1/index-models", None),
    ("post", "/api/backends/b1/set-default", None),
]


@pytest.mark.parametrize("method,path,body", GATED)
def test_write_routes_denied_for_regular_user(method, path, body):
    client = _make_client(_user(AccountType.USER))
    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
    assert response.status_code == 403


@pytest.mark.parametrize("method,path,body", GATED)
def test_write_routes_pass_gate_for_admin(method, path, body):
    """An admin clears the admin gate (the request may still fail downstream on
    the mocked manager, but it must never be a 403)."""
    client = _make_client(_user(AccountType.ADMIN))
    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
    assert response.status_code != 403


def test_list_backends_allowed_for_regular_user():
    client = _make_client(_user(AccountType.USER))
    response = client.get("/api/backends")
    assert response.status_code == 200
    assert response.json()["success"] is True
