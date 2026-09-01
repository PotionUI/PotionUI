"""`ensure_builtin_attribute_definitions`: inserts each builtin once and never
overwrites an admin's edits on a re-run."""

from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.seeding import BUILTIN_DEFINITIONS, ensure_builtin_attribute_definitions
from src.features.models.attributes.well_known import WellKnownModelAttribute

from tests.fixtures.persistence_base import PersistenceTestBase


class TestEnsureBuiltinAttributeDefinitions(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = AttributeDefinitionRepository()

    def test_seeds_every_builtin(self):
        ensure_builtin_attribute_definitions(self.repo)

        keys = {d.key for d in self.repo.list_all()}
        self.assertEqual(keys, {d.key for d in BUILTIN_DEFINITIONS})
        self.assertIn(WellKnownModelAttribute.TRIGGERS, keys)
        self.assertIn(WellKnownModelAttribute.LORA_STRENGTH, keys)

        strength = self.repo.get_by_key(WellKnownModelAttribute.LORA_STRENGTH)
        self.assertTrue(strength.system)
        self.assertEqual(strength.source, "core")
        self.assertEqual(strength.model_types, ["lora"])

    def test_lora_strength_is_a_range_with_no_stand_in_default(self):
        ensure_builtin_attribute_definitions(self.repo)

        strength = self.repo.get_by_key(WellKnownModelAttribute.LORA_STRENGTH)
        self.assertEqual(strength.field_type, "range")
        # A default here would claim every LoRA recommends it; "not set" has to
        # stay distinguishable so the picker can fall back to the preset's own.
        self.assertIsNone(strength.default_value)
        # Inverted LoRAs are legitimate, so the band may reach below zero.
        self.assertEqual(strength.config["min"], -2)

    def test_rerun_does_not_overwrite_admin_edits(self):
        ensure_builtin_attribute_definitions(self.repo)
        strength = self.repo.get_by_key(WellKnownModelAttribute.LORA_STRENGTH)
        strength.label = "Admin renamed this"
        strength.config = {"min": 0, "max": 5, "step": 0.1}
        self.repo.update(strength)

        ensure_builtin_attribute_definitions(self.repo)

        reloaded = self.repo.get_by_key(WellKnownModelAttribute.LORA_STRENGTH)
        self.assertEqual(reloaded.label, "Admin renamed this")
        self.assertEqual(reloaded.config, {"min": 0, "max": 5, "step": 0.1})

    def test_rerun_is_idempotent_in_row_count(self):
        ensure_builtin_attribute_definitions(self.repo)
        ensure_builtin_attribute_definitions(self.repo)
        self.assertEqual(len(self.repo.list_all()), len(BUILTIN_DEFINITIONS))


if __name__ == '__main__':
    import unittest
    unittest.main()
