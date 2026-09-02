"""The phrasebook batch-operation registry and the hook points the batch
path declares."""
import unittest

from src.platform.plugins.hooks import hooks_registry
from src.platform.plugins.phrasebook_ops import (
    BatchOutcome,
    DuplicatePhrasebookOperationError,
    PhrasebookBatchOperation,
    PhrasebookBatchOperationDefinition,
    PhrasebookOperationRegistry,
)


class PlainOp(PhrasebookBatchOperation):
    async def run(self, ctx, value_ids, params):
        return BatchOutcome()


class PreviewOp(PlainOp):
    supports_preview = True


def definition(op_id, source="core", backend=None, component=None):
    return PhrasebookBatchOperationDefinition(
        op_id=op_id, label=op_id.title(), backend=backend or PlainOp(),
        frontend_component=component, source=source,
    )


class TestPhrasebookOperationRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = PhrasebookOperationRegistry()

    def test_register_get_all(self):
        self.registry.register(definition("replace"))
        self.registry.register(definition("shout", source="plugin-a"))

        self.assertEqual(self.registry.get("replace").label, "Replace")
        self.assertIsNone(self.registry.get("nope"))
        self.assertEqual([d.op_id for d in self.registry.all()], ["replace", "shout"])

    def test_duplicate_id_rejected(self):
        self.registry.register(definition("replace"))
        with self.assertRaises(DuplicatePhrasebookOperationError):
            self.registry.register(definition("replace", source="plugin-a"))

    def test_unregister_source_leaves_core_and_other_plugins(self):
        self.registry.register(definition("replace"))
        self.registry.register(definition("shout", source="plugin-a"))
        self.registry.register(definition("whisper", source="plugin-a"))
        self.registry.register(definition("other", source="plugin-b"))

        self.registry.unregister_source("plugin-a")

        self.assertEqual([d.op_id for d in self.registry.all()], ["replace", "other"])

    def test_frontend_manifest(self):
        self.registry.register(definition("replace", backend=PreviewOp()))
        self.registry.register(definition("shout", source="plugin-a", component="plugin:plugin-a:Shout.svelte"))

        self.assertEqual(self.registry.frontend_manifest(), [
            {"id": "replace", "label": "Replace", "component": None, "has_preview": True, "source": "core"},
            {"id": "shout", "label": "Shout", "component": "plugin:plugin-a:Shout.svelte",
             "has_preview": False, "source": "plugin-a"},
        ])


class TestPhrasebookBatchHooksDeclared(unittest.TestCase):
    def test_batch_and_find_hooks_are_in_the_catalog_with_payloads(self):
        import src.features.phrasebook.hooks  # noqa: F401

        before = hooks_registry.get("phrasebook.batch.before")
        after = hooks_registry.get("phrasebook.batch.after")
        find = hooks_registry.get("phrasebook.find.results")

        self.assertEqual(before.type, "backend")
        self.assertEqual(sorted(before.payload), ["op", "params", "user_id", "value_ids"])
        self.assertEqual(sorted(before.mutable), ["params", "value_ids"])
        self.assertIn("outcome", after.payload)
        self.assertEqual(list(after.mutable), [])
        self.assertEqual(sorted(find.mutable), ["categories", "values"])
        self.assertIn("matches", find.payload["values"]["description"])
