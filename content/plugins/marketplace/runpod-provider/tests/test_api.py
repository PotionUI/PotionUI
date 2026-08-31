"""`backend.api`: the real router, wired to a fake `RunPodClient` (never a
real network call). Provisioning itself moved to core's
`/api/admin/provisioning` routes (see `test_provisioner.py`) - this plugin's
own router now only validates a candidate API key before it's saved.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api as api
from src.plugin_api.identity import AccountType, User, get_current_admin_user


def _admin_user() -> User:
    return User(
        id="admin-1",
        username="admin",
        email="admin@test.com",
        password_hash="hash",
        account_type=AccountType.ADMIN,
    )


class FakeRunPodClient:
    """Swapped in for `backend.api.RunPodClient` - constructed the same way
    (`RunPodClient(api_key=...)`), never touches the network."""

    validate_result = True

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.closed = False

    async def aclose(self):
        self.closed = True

    async def validate_api_key(self) -> bool:
        return FakeRunPodClient.validate_result


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "RunPodClient", FakeRunPodClient)

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_admin_user] = _admin_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_validate_key_true(client):
    FakeRunPodClient.validate_result = True
    response = client.post("/api/plugins/runpod-provider/validate-key", json={"api_key": "any"})
    assert response.status_code == 200
    assert response.json() == {"valid": True}


def test_validate_key_false(client):
    FakeRunPodClient.validate_result = False
    response = client.post("/api/plugins/runpod-provider/validate-key", json={"api_key": "any"})
    assert response.status_code == 200
    assert response.json() == {"valid": False}
