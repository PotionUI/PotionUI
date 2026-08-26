"""Attributes v2 routes: `GET/POST /api/models/attributes`,
`PUT/DELETE /api/models/attributes/{id}`, `PUT /{model_id}/metadata`, and
`PUT /{model_id}/attributes/user`.

Mirrors test_metadata_admin_routes.py: an admin drives the REAL router and
controller against a mock manager for the route-shape assertions, plus a real
ModelIndexCollaborators/ModelMetadataEditor/ModelAttributeDefinitionsManager wired to
a scratch DB for the validation behavior (undeclared key / out-of-range /
wrong type / per-user-only), so the test exercises the actual coercion and
rejection logic rather than a mock configured to match the assertion.
"""
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import User, AccountType

from src.features.models.routes import ModelController, build_router
from src.features.models.exceptions import ModelNotFoundException, InvalidModelMetadataException
from src.features.models import operations
from src.features.models.collaborators import build_model_index_collaborators
from src.features.models.repository import ModelRepository
from src.features.models.records import Model
from src.features.tags.repository import TagRepository
from src.features.models.attributes.exceptions import (
    AttributeDefinitionNotFoundException,
    InvalidAttributeDefinitionException,
    SystemAttributeDefinitionException,
)
from src.features.models.attributes.manager import ModelAttributeDefinitionsManager
from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository

from tests.fixtures.persistence_base import PersistenceTestBase


def _admin():
    return User(
        id="a1", username="admin", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )


def _user():
    return User(
        id="u1", username="regular", email="u@example.com",
        password_hash="h", account_type=AccountType.USER,
    )


@pytest.fixture
def manager():
    return Mock()


@pytest.fixture
def attribute_repo():
    return Mock()


@pytest.fixture
def attributes_manager():
    return Mock()


@pytest.fixture
def client(manager, attribute_repo, attributes_manager):
    container = MagicMock()
    container.model_controller = ModelController(
        manager, Mock(), Mock(),
        attribute_definition_repository=attribute_repo,
        model_attributes_manager=attributes_manager,
    )

    app = FastAPI()
    app.include_router(build_router(container))

    async def _fake_admin():
        return _admin()

    app.dependency_overrides[get_current_active_user] = _fake_admin
    return TestClient(app, raise_server_exceptions=False)


class TestListAttributeDefinitionsRouteShape:
    def test_admin_sees_admin_only_definitions(self, client, attribute_repo):
        attribute_repo.list_all.return_value = [
            ModelAttributeDefinition(key="strength", label="Strength", field_type="slider"),
            ModelAttributeDefinition(key="secret", label="Secret", field_type="text", admin_only=True),
        ]

        resp = client.get("/api/models/attributes")

        assert resp.status_code == 200
        body = resp.json()
        keys = {d["key"] for d in body["data"]["definitions"]}
        assert keys == {"strength", "secret"}

    def test_non_admin_never_sees_admin_only_definitions(self, manager, attribute_repo, attributes_manager):
        attribute_repo.list_all.return_value = [
            ModelAttributeDefinition(key="strength", label="Strength", field_type="slider"),
            ModelAttributeDefinition(key="secret", label="Secret", field_type="text", admin_only=True),
        ]
        container = MagicMock()
        container.model_controller = ModelController(
            manager, Mock(), Mock(),
            attribute_definition_repository=attribute_repo,
            model_attributes_manager=attributes_manager,
        )
        app = FastAPI()
        app.include_router(build_router(container))

        async def _fake_user():
            return _user()
        app.dependency_overrides[get_current_active_user] = _fake_user
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/models/attributes")

        keys = {d["key"] for d in resp.json()["data"]["definitions"]}
        assert keys == {"strength"}


class TestCreateAttributeDefinitionRouteShape:
    def test_admin_creates_definition(self, client, attributes_manager):
        attributes_manager.create.return_value = ModelAttributeDefinition(
            id="d1", key="clip_skip", label="CLIP Skip", field_type="number",
        )

        resp = client.post("/api/models/attributes", json={
            "key": "clip_skip", "label": "CLIP Skip", "field_type": "number",
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["definition"]["key"] == "clip_skip"

    def test_invalid_definition_reports_error(self, client, attributes_manager):
        attributes_manager.create.side_effect = InvalidAttributeDefinitionException("'Bad Key' is not a valid attribute key")

        resp = client.post("/api/models/attributes", json={
            "key": "Bad Key", "label": "x", "field_type": "text",
        })

        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "invalid_attribute_definition"


class TestUpdateAttributeDefinitionRouteShape:
    def test_system_key_change_rejected(self, client, attributes_manager):
        attributes_manager.update.side_effect = SystemAttributeDefinitionException(
            "'triggers' is a system attribute - its key can't change"
        )

        resp = client.put("/api/models/attributes/d1", json={"key": "renamed"})

        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "invalid_attribute_definition"

    def test_missing_definition_reports_not_found(self, client, attributes_manager):
        attributes_manager.update.side_effect = AttributeDefinitionNotFoundException("not found")

        resp = client.put("/api/models/attributes/nope", json={"label": "x"})

        assert resp.json()["error"] == "attribute_not_found"


class TestDeleteAttributeDefinitionRouteShape:
    def test_system_definition_delete_rejected(self, client, attributes_manager):
        attributes_manager.delete.side_effect = SystemAttributeDefinitionException(
            "'triggers' is a system attribute and can't be deleted"
        )

        resp = client.delete("/api/models/attributes/d1")

        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "system_attribute_definition"


class TestUpdateModelMetadataRouteShape:
    def test_admin_updates_metadata(self, client, manager):
        manager.metadata.update_model_metadata.return_value = {
            "message": "Model metadata updated successfully",
            "model": {"id": "m1", "model_metadata": {"strength": 0.8}},
        }

        resp = client.put("/api/models/m1/metadata", json={"values": {"strength": 0.8}})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["model"]["model_metadata"]["strength"] == 0.8
        manager.metadata.update_model_metadata.assert_called_once_with("m1", {"strength": 0.8})

    def test_model_not_found(self, client, manager):
        manager.metadata.update_model_metadata.side_effect = ModelNotFoundException("Model 'm1' not found")

        resp = client.put("/api/models/m1/metadata", json={"values": {"strength": 0.8}})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "model_not_found"

    def test_invalid_metadata(self, client, manager):
        manager.metadata.update_model_metadata.side_effect = InvalidModelMetadataException(
            "'bogus' is not a declared attribute for model type 'lora'"
        )

        resp = client.put("/api/models/m1/metadata", json={"values": {"bogus": 1}})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "invalid_model_metadata"
        assert "bogus" in body["message"]


class TestUpdateModelUserAttributesRouteShape:
    def test_updates_and_returns_overlay(self, client, attributes_manager):
        attributes_manager.update_user_values.return_value = {"strength": 0.8}

        resp = client.put("/api/models/m1/attributes/user", json={"values": {"strength": 0.8}})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["values"] == {"strength": 0.8}
        attributes_manager.update_user_values.assert_called_once_with("m1", "a1", {"strength": 0.8})

    def test_non_per_user_key_rejected(self, client, attributes_manager):
        attributes_manager.update_user_values.side_effect = InvalidModelMetadataException(
            "'triggers' is not a per-user attribute"
        )

        resp = client.put("/api/models/m1/attributes/user", json={"values": {"triggers": ["a"]}})

        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "invalid_model_metadata"


class TestUpdateModelMetadataValidation(PersistenceTestBase):
    """Drives the real manager/editor/repository against a scratch DB, so the
    validation logic (not a mock's canned response) is what's under test."""

    def setUp(self):
        super().setUp()
        import src.features.models.repository as model_repository_module
        model_repository_module.db = self.db
        import src.features.tags.repository as tag_repository_module
        tag_repository_module.db = self.db
        import src.features.models.attributes.repository as attribute_repo_module
        attribute_repo_module.db = self.db
        import src.features.models.attributes.user_repository as user_attribute_repo_module
        user_attribute_repo_module.db = self.db
        import src.features.models.availability_repository as availability_repository_module
        availability_repository_module.db = self.db
        # `settings.repository` binds `db` at its own top-level import - a
        # third, separate name from `database.database` - so the real,
        # module-level ModelScanner singleton's SettingRepository().get_setting_by_key
        # call has to be redirected here too, or it queries the real database.
        import src.platform.settings.repository as settings_repository_module
        settings_repository_module.db = self.db

        self.model_repo = ModelRepository()
        self.tag_repo = TagRepository()

        self.attribute_definitions = AttributeDefinitionRepository()
        self.attribute_definitions.create(ModelAttributeDefinition(
            key="strength", label="Strength", field_type="slider",
            model_types=["lora"], config={"min": 0, "max": 2}, default_value=1.0,
        ))

        self.collaborators = build_model_index_collaborators(
            model_repository=self.model_repo,
            tag_repository=self.tag_repo,
            plugin_registry=Mock(),
            settings_manager=Mock(),
            download_manager=Mock(),
            attribute_definition_repository=self.attribute_definitions,
            user_attribute_repository=UserModelAttributeRepository(),
            # Without it, `__init__` falls through to the real, lazily-constructed
            # module-level scanner singleton to resolve models_dir, hitting the
            # settings DB for real.
            models_root=Path("/tmp/potionui-test-models"),
        )

        model = Model(
            filename="test_lora.safetensors",
            file_path="/models/loras/test_lora.safetensors",
            file_size=1024,
            model_type="lora",
        )
        self.model = self.model_repo.create(model)

    def test_valid_value_persists_and_reads_back(self):
        result = operations.update_model_metadata(self.collaborators, self.model.id, {"strength": 0.8})
        assert result["model"]["model_metadata"] == {"strength": 0.8}

        reloaded = self.model_repo.get_by_id(self.model.id, include_providers=False)
        assert reloaded.model_metadata == {"strength": 0.8}

    def test_undeclared_key_is_rejected_and_names_it(self):
        with self.assertRaises(InvalidModelMetadataException) as ctx:
            operations.update_model_metadata(self.collaborators, self.model.id, {"bogus": 1})
        self.assertIn("bogus", str(ctx.exception))

        # rejected, not stored
        reloaded = self.model_repo.get_by_id(self.model.id, include_providers=False)
        assert reloaded.model_metadata == {}

    def test_out_of_range_value_is_rejected_not_clamped(self):
        with self.assertRaises(InvalidModelMetadataException) as ctx:
            operations.update_model_metadata(self.collaborators, self.model.id, {"strength": 5.0})
        message = str(ctx.exception)
        self.assertIn("strength", message)
        self.assertIn("2", message)

        reloaded = self.model_repo.get_by_id(self.model.id, include_providers=False)
        assert reloaded.model_metadata == {}

    def test_wrong_type_value_is_rejected(self):
        with self.assertRaises(InvalidModelMetadataException):
            operations.update_model_metadata(self.collaborators, self.model.id, {"strength": "not-a-number"})

    def test_user_scoped_list_models_threads_user_model_metadata(self):
        """The picker's model source (`list_models`) surfaces the caller's
        per-user overlay alongside each model - the contract new frontend code
        was built against."""
        from src.features.models.catalog import ListModelsParams

        self.create_test_user(user_id="a1")
        user_attrs = self.collaborators.catalog.user_attributes
        user_attrs.upsert("a1", self.model.id, "strength", 0.42)

        result = operations.list_models(self.collaborators, ListModelsParams(all_models=True), _admin())

        entry = next(m for m in result["models"] if m["id"] == self.model.id)
        assert entry["user_model_metadata"] == {"strength": 0.42}


if __name__ == '__main__':
    import unittest
    unittest.main()
