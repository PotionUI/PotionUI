"""The restored PUT /{model_id}/description and /{model_id}/tags routes.

Their predecessors were open to any authenticated user; the restored
routes are admin-gated (the non-admin 403 side lives in
tests/features/test_core_route_authz.py). Here an admin drives the REAL router
and controller against a mock manager: the write must land on the manager with
the request payload and the updated model must come back in the 200 response.
"""
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import User, AccountType

from src.features.models.routes import ModelController, build_router
from src.features.models.exceptions import ModelNotFoundException, InvalidTagException


def _admin():
    return User(
        id="a1", username="admin", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )


@pytest.fixture
def manager():
    return Mock()


@pytest.fixture
def client(manager):
    container = MagicMock()
    container.model_controller = ModelController(manager, Mock(), Mock())

    app = FastAPI()
    app.include_router(build_router(container))

    async def _fake_admin():
        return _admin()

    app.dependency_overrides[get_current_active_user] = _fake_admin
    return TestClient(app, raise_server_exceptions=False)


class TestUpdateDescription:
    def test_admin_updates_description(self, client, manager):
        manager.update_model_description.return_value = {
            "message": "Model description updated successfully",
            "model": {"id": "m1", "description": "New description"},
        }

        resp = client.put("/api/models/m1/description", json={"description": "New description"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["model"]["description"] == "New description"
        manager.update_model_description.assert_called_once_with("m1", "New description")

    def test_model_not_found(self, client, manager):
        manager.update_model_description.side_effect = ModelNotFoundException("Model 'm1' not found")

        resp = client.put("/api/models/m1/description", json={"description": "x"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "model_not_found"


class TestUpdateTags:
    def test_admin_updates_tags(self, client, manager):
        manager.update_model_tags.return_value = {
            "message": "Model tags updated successfully",
            "model": {"id": "m1", "tags": [{"id": "t1", "name": "anime"}]},
        }

        resp = client.put("/api/models/m1/tags", json={"tag_ids": ["t1"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["model"]["tags"][0]["id"] == "t1"
        manager.update_model_tags.assert_called_once_with("m1", ["t1"])

    def test_invalid_tag(self, client, manager):
        manager.update_model_tags.side_effect = InvalidTagException("Invalid tag ID: t9")

        resp = client.put("/api/models/m1/tags", json={"tag_ids": ["t9"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "invalid_tag"
