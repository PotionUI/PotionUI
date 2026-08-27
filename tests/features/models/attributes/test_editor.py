"""`ModelAttributeDefinitionsEditor`: definition CRUD + system guards, plugin
manifest upsert/collision/removal, and the per-user overlay write."""

from src.features.models.attributes.exceptions import (
    AttributeDefinitionNotFoundException,
    InvalidAttributeDefinitionException,
    SystemAttributeDefinitionException,
)
from src.features.models.attributes.editor import ModelAttributeDefinitionsEditor
from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.exceptions import InvalidModelMetadataException
from src.features.models.records import Model

from tests.fixtures.persistence_base import PersistenceTestBase


class ModelAttributeDefinitionsEditorTestBase(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        import src.features.models.attributes.repository as def_repo_module
        def_repo_module.db = self.db
        import src.features.models.attributes.user_repository as user_repo_module
        user_repo_module.db = self.db
        import src.features.models.repository as model_repository_module
        model_repository_module.db = self.db
        import src.features.tags.repository as tag_repository_module
        tag_repository_module.db = self.db

        self.definitions = AttributeDefinitionRepository()
        self.user_attributes = UserModelAttributeRepository()
        self.manager = ModelAttributeDefinitionsEditor(self.definitions, self.user_attributes)


class TestDefinitionCrud(ModelAttributeDefinitionsEditorTestBase):

    def test_create_persists(self):
        definition = self.manager.create({
            "key": "clip_skip", "label": "CLIP Skip", "field_type": "number",
            "model_types": ["checkpoint"], "config": {"min": 1, "max": 4},
        })
        self.assertEqual(definition.source, "user")
        self.assertFalse(definition.system)
        self.assertIsNotNone(self.definitions.get_by_key("clip_skip"))

    def test_create_rejects_bad_key_format(self):
        with self.assertRaises(InvalidAttributeDefinitionException):
            self.manager.create({"key": "Clip-Skip", "label": "x", "field_type": "text"})

    def test_create_rejects_duplicate_key(self):
        self.manager.create({"key": "clip_skip", "label": "x", "field_type": "text"})
        with self.assertRaises(InvalidAttributeDefinitionException):
            self.manager.create({"key": "clip_skip", "label": "y", "field_type": "text"})

    def test_create_rejects_unknown_field_type(self):
        with self.assertRaises(InvalidAttributeDefinitionException):
            self.manager.create({"key": "x", "label": "x", "field_type": "bogus"})

    def test_update_non_system_definition(self):
        created = self.manager.create({"key": "clip_skip", "label": "CLIP Skip", "field_type": "number"})
        updated = self.manager.update(created.id, {"label": "Renamed", "config": {"min": 1, "max": 8}})
        self.assertEqual(updated.label, "Renamed")
        self.assertEqual(updated.config, {"min": 1, "max": 8})

    def test_update_missing_definition_raises(self):
        with self.assertRaises(AttributeDefinitionNotFoundException):
            self.manager.update("nope", {"label": "x"})

    def test_delete_non_system_definition(self):
        created = self.manager.create({"key": "clip_skip", "label": "CLIP Skip", "field_type": "number"})
        self.manager.delete(created.id)
        self.assertIsNone(self.definitions.get_by_id(created.id))

    def test_delete_missing_definition_raises(self):
        with self.assertRaises(AttributeDefinitionNotFoundException):
            self.manager.delete("nope")


class TestSystemDefinitionGuards(ModelAttributeDefinitionsEditorTestBase):

    def setUp(self):
        super().setUp()
        self.system_def = self.definitions.create(ModelAttributeDefinition(
            key="triggers", label="Trigger words", field_type="tags", system=True, source="core",
        ))

    def test_key_change_rejected(self):
        with self.assertRaises(SystemAttributeDefinitionException):
            self.manager.update(self.system_def.id, {"key": "renamed"})

    def test_field_type_change_rejected(self):
        with self.assertRaises(SystemAttributeDefinitionException):
            self.manager.update(self.system_def.id, {"field_type": "text"})

    def test_label_and_config_still_editable(self):
        updated = self.manager.update(self.system_def.id, {"label": "Renamed label", "per_user": True})
        self.assertEqual(updated.label, "Renamed label")
        self.assertTrue(updated.per_user)
        self.assertEqual(updated.key, "triggers")
        self.assertEqual(updated.field_type, "tags")

    def test_delete_rejected(self):
        with self.assertRaises(SystemAttributeDefinitionException):
            self.manager.delete(self.system_def.id)


class TestPluginManifestWiring(ModelAttributeDefinitionsEditorTestBase):

    def test_upsert_from_plugin_creates_owned_definitions(self):
        error = self.manager.upsert_from_plugin("plugin-a", [
            {"key": "clip_skip", "label": "CLIP Skip", "field_type": "number", "model_types": ["checkpoint"]},
        ])
        self.assertIsNone(error)
        definition = self.definitions.get_by_key("clip_skip")
        self.assertEqual(definition.source, "plugin-a")
        self.assertFalse(definition.system)

    def test_upsert_from_plugin_is_idempotent_for_same_source(self):
        self.manager.upsert_from_plugin("plugin-a", [
            {"key": "clip_skip", "label": "CLIP Skip", "field_type": "number"},
        ])
        error = self.manager.upsert_from_plugin("plugin-a", [
            {"key": "clip_skip", "label": "CLIP Skip (updated)", "field_type": "number"},
        ])
        self.assertIsNone(error)
        self.assertEqual(len(self.definitions.list_all()), 1)
        self.assertEqual(self.definitions.get_by_key("clip_skip").label, "CLIP Skip (updated)")

    def test_upsert_from_plugin_rejects_core_owned_collision(self):
        self.definitions.create(ModelAttributeDefinition(
            key="strength", label="Strength", field_type="slider", system=True, source="core",
        ))

        error = self.manager.upsert_from_plugin("plugin-a", [
            {"key": "strength", "label": "Strength", "field_type": "slider"},
        ])

        self.assertIsNotNone(error)
        self.assertIn("strength", error)
        self.assertEqual(self.definitions.get_by_key("strength").source, "core")

    def test_upsert_from_plugin_rejects_other_plugin_collision(self):
        self.manager.upsert_from_plugin("plugin-a", [{"key": "x", "label": "X", "field_type": "text"}])
        error = self.manager.upsert_from_plugin("plugin-b", [{"key": "x", "label": "X", "field_type": "text"}])
        self.assertIsNotNone(error)
        self.assertIn("plugin-a", error)

    def test_remove_source_deletes_only_that_plugins_definitions(self):
        self.manager.upsert_from_plugin("plugin-a", [{"key": "x", "label": "X", "field_type": "text"}])
        self.definitions.create(ModelAttributeDefinition(key="y", label="Y", field_type="text", source="core"))

        self.manager.remove_source("plugin-a")

        keys = {d.key for d in self.definitions.list_all()}
        self.assertEqual(keys, {"y"})


class TestUpdateUserValues(ModelAttributeDefinitionsEditorTestBase):

    def setUp(self):
        super().setUp()
        self.definitions.create(ModelAttributeDefinition(
            key="strength", label="Strength", field_type="slider",
            model_types=["lora"], config={"min": 0, "max": 2}, per_user=True,
        ))
        self.definitions.create(ModelAttributeDefinition(
            key="triggers", label="Trigger words", field_type="tags", per_user=False,
        ))

        from src.features.models.repository import ModelRepository
        self.model_repo = ModelRepository()
        self.model = self.model_repo.create(Model(
            filename="test.safetensors", file_path="/models/loras/test.safetensors",
            file_size=1, model_type="lora",
        ))
        self.create_test_user(user_id="user-1")
        self.create_test_user(user_id="user-2", username="user2", email="user2@example.com")

    def test_valid_value_persists(self):
        result = self.manager.update_user_values(self.model.id, "user-1", {"strength": 0.8})
        self.assertEqual(result, {"strength": 0.8})
        self.assertEqual(self.user_attributes.get_map("user-1", self.model.id), {"strength": 0.8})

    def test_non_per_user_key_rejected(self):
        with self.assertRaises(InvalidModelMetadataException) as ctx:
            self.manager.update_user_values(self.model.id, "user-1", {"triggers": ["a"]})
        self.assertIn("triggers", str(ctx.exception))

    def test_undeclared_key_rejected(self):
        with self.assertRaises(InvalidModelMetadataException):
            self.manager.update_user_values(self.model.id, "user-1", {"bogus": 1})

    def test_out_of_range_value_rejected(self):
        with self.assertRaises(InvalidModelMetadataException):
            self.manager.update_user_values(self.model.id, "user-1", {"strength": 9.0})

    def test_overlay_isolated_between_users(self):
        self.manager.update_user_values(self.model.id, "user-1", {"strength": 0.8})
        self.manager.update_user_values(self.model.id, "user-2", {"strength": 1.4})

        self.assertEqual(self.user_attributes.get_map("user-1", self.model.id), {"strength": 0.8})
        self.assertEqual(self.user_attributes.get_map("user-2", self.model.id), {"strength": 1.4})


if __name__ == '__main__':
    import unittest
    unittest.main()
