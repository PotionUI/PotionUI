"""`AttributeDefinitionRepository` and `UserModelAttributeRepository` against a
real scratch DB (migration 135's tables)."""

from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.records import Model

from tests.fixtures.persistence_base import PersistenceTestBase


class TestAttributeDefinitionRepository(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        import src.features.models.attributes.repository as repo_module
        repo_module.db = self.db
        self.repo = AttributeDefinitionRepository()

    def test_create_and_get_by_id(self):
        created = self.repo.create(ModelAttributeDefinition(
            key="clip_skip", label="CLIP Skip", field_type="number",
            model_types=["checkpoint"], config={"min": 1, "max": 4}, default_value=1,
        ))
        fetched = self.repo.get_by_id(created.id)
        self.assertEqual(fetched.key, "clip_skip")
        self.assertEqual(fetched.model_types, ["checkpoint"])
        self.assertEqual(fetched.config, {"min": 1, "max": 4})
        self.assertEqual(fetched.default_value, 1)

    def test_get_by_key(self):
        self.repo.create(ModelAttributeDefinition(key="clip_skip", label="CLIP Skip", field_type="number"))
        self.assertIsNotNone(self.repo.get_by_key("clip_skip"))
        self.assertIsNone(self.repo.get_by_key("nope"))

    def test_for_model_type_includes_wildcard_and_specific(self):
        self.repo.create(ModelAttributeDefinition(
            key="triggers", label="Trigger words", field_type="tags", model_types=[],
        ))
        self.repo.create(ModelAttributeDefinition(
            key="strength", label="Strength", field_type="slider", model_types=["lora"],
        ))
        self.repo.create(ModelAttributeDefinition(
            key="clip_skip", label="CLIP Skip", field_type="number", model_types=["checkpoint"],
        ))

        lora_keys = {d.key for d in self.repo.for_model_type("lora")}
        self.assertEqual(lora_keys, {"triggers", "strength"})

        checkpoint_keys = {d.key for d in self.repo.for_model_type("checkpoint")}
        self.assertEqual(checkpoint_keys, {"triggers", "clip_skip"})

    def test_update_persists_changes(self):
        created = self.repo.create(ModelAttributeDefinition(key="clip_skip", label="CLIP Skip", field_type="number"))
        created.label = "CLIP Skip (renamed)"
        created.config = {"min": 1, "max": 8}
        updated = self.repo.update(created)
        self.assertEqual(updated.label, "CLIP Skip (renamed)")
        self.assertEqual(updated.config, {"min": 1, "max": 8})

    def test_delete(self):
        created = self.repo.create(ModelAttributeDefinition(key="clip_skip", label="CLIP Skip", field_type="number"))
        self.assertTrue(self.repo.delete(created.id))
        self.assertIsNone(self.repo.get_by_id(created.id))

    def test_delete_by_source(self):
        self.repo.create(ModelAttributeDefinition(key="a", label="A", field_type="text", source="plugin-x"))
        self.repo.create(ModelAttributeDefinition(key="b", label="B", field_type="text", source="plugin-x"))
        self.repo.create(ModelAttributeDefinition(key="c", label="C", field_type="text", source="core"))

        removed = self.repo.delete_by_source("plugin-x")

        self.assertEqual(removed, 2)
        remaining_sources = {d.source for d in self.repo.list_all()}
        self.assertEqual(remaining_sources, {"core"})


class TestUserModelAttributeRepository(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        import src.features.models.attributes.user_repository as user_repo_module
        user_repo_module.db = self.db
        import src.features.models.repository as model_repository_module
        model_repository_module.db = self.db
        import src.features.tags.repository as tag_repository_module
        tag_repository_module.db = self.db

        self.repo = UserModelAttributeRepository()

        from src.features.models.repository import ModelRepository
        self.model_repo = ModelRepository()
        self.model = self.model_repo.create(Model(
            filename="test.safetensors", file_path="/models/loras/test.safetensors",
            file_size=1, model_type="lora",
        ))
        self.create_test_user(user_id="user-1")
        self.create_test_user(user_id="user-2", username="user2", email="user2@example.com")

    def test_upsert_then_get_map(self):
        self.repo.upsert("user-1", self.model.id, "strength", 0.7)
        self.assertEqual(self.repo.get_map("user-1", self.model.id), {"strength": 0.7})

    def test_upsert_overwrites_existing_value(self):
        self.repo.upsert("user-1", self.model.id, "strength", 0.7)
        self.repo.upsert("user-1", self.model.id, "strength", 1.2)
        self.assertEqual(self.repo.get_map("user-1", self.model.id), {"strength": 1.2})

    def test_overlay_is_isolated_between_users(self):
        self.repo.upsert("user-1", self.model.id, "strength", 0.7)
        self.repo.upsert("user-2", self.model.id, "strength", 1.5)

        self.assertEqual(self.repo.get_map("user-1", self.model.id), {"strength": 0.7})
        self.assertEqual(self.repo.get_map("user-2", self.model.id), {"strength": 1.5})

    def test_upsert_many_merges_keys(self):
        self.repo.upsert("user-1", self.model.id, "strength", 0.7)
        result = self.repo.upsert_many("user-1", self.model.id, {"clip_skip": 2})
        self.assertEqual(result, {"strength": 0.7, "clip_skip": 2})

    def test_get_maps_batches_across_models(self):
        other = self.model_repo.create(Model(
            filename="other.safetensors", file_path="/models/loras/other.safetensors",
            file_size=1, model_type="lora",
        ))
        self.repo.upsert("user-1", self.model.id, "strength", 0.7)
        self.repo.upsert("user-1", other.id, "strength", 1.9)

        maps = self.repo.get_maps("user-1", [self.model.id, other.id])

        self.assertEqual(maps[self.model.id], {"strength": 0.7})
        self.assertEqual(maps[other.id], {"strength": 1.9})

    def test_get_maps_empty_list_is_empty(self):
        self.assertEqual(self.repo.get_maps("user-1", []), {})
