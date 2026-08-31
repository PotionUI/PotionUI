"""Router-level tests for the compute-provisioning admin API: every route is
admin-gated (`Depends(get_current_admin_user)` at the router level, mirroring
`src.features.backends.routes`), and the real router drives the real
`ProvisioningController` -> `operations` path against fake collaborators.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.provisioning.routes import ProvisioningController, build_admin_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

from tests.features.provisioning.test_operations import (
    FakeBackendRegistry,
    FakeProvisioner,
    FakeProvisionerRegistry,
    FakeRepository,
)


def _user(account_type):
    return User(
        id="u1", username="u", email="u@example.com",
        password_hash="h", account_type=account_type,
    )


def _make_client(*, provisioner=None, repository=None, backend_registry=None):
    provisioner = provisioner or FakeProvisioner()
    registry = FakeProvisionerRegistry([provisioner])
    repository = repository if repository is not None else FakeRepository()
    backend_registry = backend_registry or FakeBackendRegistry()

    controller = ProvisioningController(registry, repository, backend_registry)

    class _Container:
        provisioning_controller = controller

    app = FastAPI()
    app.include_router(build_admin_router(_Container()))
    return app, provisioner, repository, backend_registry


GATED = [
    ("get", "/api/admin/provisioning/providers", None),
    ("get", "/api/admin/provisioning/providers/fake/fields", None),
    ("get", "/api/admin/provisioning", None),
    ("get", "/api/admin/provisioning/by-backend/backend-1", None),
    ("post", "/api/admin/provisioning", {"provider_id": "fake", "name": "prof-1", "values": {"gpu_type_id": "fake-gpu"}}),
    ("get", "/api/admin/provisioning/row-1", None),
    ("post", "/api/admin/provisioning/row-1/stop", None),
    ("post", "/api/admin/provisioning/row-1/terminate", None),
]


@pytest.mark.parametrize("method,path,body", GATED)
def test_every_route_is_denied_for_a_regular_user(method, path, body):
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.USER)
    client = TestClient(app)

    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)

    assert response.status_code == 403


@pytest.mark.parametrize("method,path,body", GATED)
def test_every_route_passes_the_admin_gate(method, path, body):
    """An admin clears the gate - the request may still 200/404 downstream on
    the fakes, but it must never be a 403."""
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)

    assert response.status_code != 403


def test_provision_creates_and_enables_a_backend_through_the_real_router():
    app, provisioner, repository, backend_registry = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.post(
        "/api/admin/provisioning",
        json={"provider_id": "fake", "name": "prof-1", "values": {"gpu_type_id": "fake-gpu"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    row_id = body["data"]["id"]
    backend_id = body["data"]["backend_id"]
    assert backend_id is not None
    assert backend_registry.backend_config_store.get_backend(backend_id).enabled is True

    by_backend_response = client.get(f"/api/admin/provisioning/by-backend/{backend_id}")
    assert by_backend_response.status_code == 200
    assert by_backend_response.json()["data"]["id"] == row_id

    stop_response = client.post(f"/api/admin/provisioning/{row_id}/stop")
    assert stop_response.status_code == 200
    assert backend_registry.backend_config_store.get_backend(backend_id).enabled is False

    terminate_response = client.post(f"/api/admin/provisioning/{row_id}/terminate")
    assert terminate_response.status_code == 200
    assert backend_registry.backend_config_store.get_backend(backend_id) is None
    assert repository.get_by_id(row_id) is None


def test_provision_with_illegal_values_is_a_clean_error_not_a_500():
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.post(
        "/api/admin/provisioning",
        json={"provider_id": "fake", "name": "prof-1", "values": {}},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False


def test_fields_route_delegates_to_the_provisioner():
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.get("/api/admin/provisioning/providers/fake/fields")

    assert response.status_code == 200
    fields = response.json()["data"]["fields"]
    assert fields[0]["key"] == "gpu_type_id"
    assert fields[0]["options"] == [{"value": "fake-gpu", "label": "Fake GPU", "detail": "24 GB VRAM"}]


def test_fields_route_unknown_provider_is_a_clean_error_not_a_500():
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.get("/api/admin/provisioning/providers/missing/fields")

    assert response.status_code == 200  # error_api_response, not a raised HTTPException
    assert response.json()["success"] is False


def test_by_backend_route_returns_404_when_no_row_is_linked():
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.get("/api/admin/provisioning/by-backend/missing-backend")

    assert response.status_code == 404
