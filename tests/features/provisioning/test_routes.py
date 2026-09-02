"""Router-level tests for the compute-provisioning admin API: every route is
admin-gated (`Depends(get_current_admin_user)` at the router level, mirroring
`src.features.backends.routes`), and the real router drives the real
`ProvisioningController` -> `operations` path against fake collaborators.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.provisioning.contracts import ComputeProvisionerError
from src.features.provisioning.routes import ProvisioningController, build_admin_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

from src.features.provisioning.operations import ComputeProvisioningJobs

from tests.features.provisioning.test_operations import (
    FakeBackendRegistry,
    FakeHub,
    FakeProvisioner,
    FakeProvisionerRegistry,
    FakeRepository,
    _seed_remote_backend,
)


class _AuthFailingProvisioner(FakeProvisioner):
    """Simulates a provider rejecting credentials while describing its fields
    (e.g. the real RunPod plugin's 401 when its API key is wrong) - the shape
    of failure that used to reach the client as an unhandled 500. A failure
    inside provision() itself lands on the row instead (see test_operations)."""

    async def describe_fields(self, values=None):
        raise ComputeProvisionerError("RunPod API error 401: RunPod API key was rejected")


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

    hub = FakeHub()
    jobs = ComputeProvisioningJobs(registry, repository, backend_registry, hub)
    controller = ProvisioningController(registry, repository, backend_registry, jobs, hub)

    class _Container:
        provisioning_controller = controller

    app = FastAPI()
    app.include_router(build_admin_router(_Container()))
    return app, provisioner, repository, backend_registry


GATED = [
    ("get", "/api/admin/provisioning/providers", None),
    ("post", "/api/admin/provisioning/providers/fake/fields", {"values": {}}),
    ("get", "/api/admin/provisioning", None),
    ("get", "/api/admin/provisioning/by-backend/backend-1", None),
    (
        "post", "/api/admin/provisioning",
        {"provider_id": "fake", "backend_id": "remote-1", "values": {"gpu_type_id": "fake-gpu"}},
    ),
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


def test_provision_returns_a_provisioning_row_at_once_then_the_job_fills_the_backend():
    app, provisioner, repository, backend_registry = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    _seed_remote_backend_sync(backend_registry, backend_id="remote-1", name="RunPod A100")

    # `with` so the app's event loop outlives the request and the background
    # job started inside it can run to completion.
    with TestClient(app) as client:
        response = client.post(
            "/api/admin/provisioning",
            json={"provider_id": "fake", "backend_id": "remote-1", "values": {"gpu_type_id": "fake-gpu"}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        row_id = body["data"]["id"]
        backend_id = body["data"]["backend_id"]
        assert backend_id == "remote-1"
        assert body["data"]["status"] == "provisioning"
        assert body["data"]["progress"] == []

        deadline = 50
        while backend_registry.backend_config_store.get_backend(backend_id).enabled is False and deadline:
            import time
            time.sleep(0.02)
            deadline -= 1
        assert backend_registry.backend_config_store.get_backend(backend_id).enabled is True

        by_backend_response = client.get(f"/api/admin/provisioning/by-backend/{backend_id}")
        assert by_backend_response.status_code == 200
        assert by_backend_response.json()["data"]["id"] == row_id
        assert by_backend_response.json()["data"]["status"] == "running"
        assert [e["stage"] for e in by_backend_response.json()["data"]["progress"]] == ["preparing", "creating", "ready"]

        refreshed = client.get(f"/api/admin/provisioning/{row_id}")
        assert refreshed.status_code == 200
        assert refreshed.json()["data"]["status_checked_at"] is not None

        stop_response = client.post(f"/api/admin/provisioning/{row_id}/stop")
        assert stop_response.status_code == 200
        assert stop_response.json()["data"]["status"] == "stopped"
        assert backend_registry.backend_config_store.get_backend(backend_id).enabled is False

        terminate_response = client.post(f"/api/admin/provisioning/{row_id}/terminate")
        assert terminate_response.status_code == 200
        surviving_backend = backend_registry.backend_config_store.get_backend(backend_id)
        assert surviving_backend is not None  # the backend row survives termination
        assert surviving_backend.enabled is False
        assert surviving_backend.base_url == ""
        assert repository.get_by_id(row_id) is None


def test_provision_unknown_backend_is_a_clean_error_not_a_500():
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.post(
        "/api/admin/provisioning",
        json={"provider_id": "fake", "backend_id": "does-not-exist", "values": {"gpu_type_id": "fake-gpu"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "backend_not_found"


def test_provision_with_illegal_values_is_a_clean_error_not_a_500():
    app, provisioner, repository, backend_registry = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    _seed_remote_backend_sync(backend_registry, backend_id="remote-1")
    client = TestClient(app)

    response = client.post(
        "/api/admin/provisioning",
        json={"provider_id": "fake", "backend_id": "remote-1", "values": {}},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False


def test_provision_provisioner_error_is_a_clean_error_not_a_500():
    app, provisioner, repository, backend_registry = _make_client(provisioner=_AuthFailingProvisioner())
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    _seed_remote_backend_sync(backend_registry, backend_id="remote-1")
    client = TestClient(app)

    response = client.post(
        "/api/admin/provisioning",
        json={"provider_id": "fake", "backend_id": "remote-1", "values": {"gpu_type_id": "fake-gpu"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "provision_failed"
    assert "RunPod API key was rejected" in body["message"]


def test_fields_route_delegates_to_the_provisioner():
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.post("/api/admin/provisioning/providers/fake/fields", json={"values": {}})

    assert response.status_code == 200
    fields = response.json()["data"]["fields"]
    assert fields[0]["key"] == "gpu_type_id"
    assert fields[0]["options"] == [{"value": "fake-gpu", "label": "Fake GPU", "detail": "24 GB VRAM"}]
    assert fields[0]["depends_on"] == []


def test_fields_route_passes_values_through_for_dependent_fields():
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.post(
        "/api/admin/provisioning/providers/fake/fields",
        json={"values": {"gpu_type_id": "fake-gpu"}},
    )

    assert response.status_code == 200
    fields = response.json()["data"]["fields"]
    volume_field = next(f for f in fields if f["key"] == "network_volume_id")
    assert volume_field["depends_on"] == ["gpu_type_id"]
    assert volume_field["options"] == [{"value": "vol-fake-gpu-1", "label": "1x NVMe (fake-gpu)", "detail": None}]


def test_fields_route_unknown_provider_is_a_clean_error_not_a_500():
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.post("/api/admin/provisioning/providers/missing/fields", json={"values": {}})

    assert response.status_code == 200  # error_api_response, not a raised HTTPException
    assert response.json()["success"] is False


def test_by_backend_route_returns_404_when_no_row_is_linked():
    app, *_ = _make_client()
    app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.ADMIN)
    client = TestClient(app)

    response = client.get("/api/admin/provisioning/by-backend/missing-backend")

    assert response.status_code == 404


def _seed_remote_backend_sync(backend_registry, **kwargs):
    """`TestClient` calls are synchronous but `_seed_remote_backend` is async
    (it awaits `BackendRegistry.add_backend`) - run it to completion up front,
    before the client ever makes a request."""
    import asyncio

    asyncio.run(_seed_remote_backend(backend_registry, **kwargs))
