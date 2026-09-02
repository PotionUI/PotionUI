"""Core batch tools driven the way the registry drives them: `run()` /
`preview()` on the real SQLite context with a raw `params` dict."""
import asyncio

from src.features.phrasebook.operations import core_ops
from src.platform.plugins.phrasebook_ops import (
    BatchOperationError,
    BatchOutcome,
    BatchPreview,
    PhrasebookOperationRegistry,
)
from tests.features.phrasebook.operations.test_batch import BatchBase


def run(coro):
    # not get_event_loop(): pytest-asyncio clears the thread's current loop after any async test
    return asyncio.run(coro)


class TestCoreOperations(BatchBase):
    def test_registration_marks_core_and_preview_only_on_replace(self):
        registry = PhrasebookOperationRegistry()
        core_ops.register_core_batch_operations(registry)
        manifest = {m["id"]: m for m in registry.frontend_manifest()}

        self.assertEqual(sorted(manifest), ["delete", "move", "replace", "set_active"])
        self.assertTrue(all(m["source"] == "core" and m["component"] is None for m in manifest.values()))
        self.assertEqual({k: m["has_preview"] for k, m in manifest.items()},
                         {"replace": True, "set_active": False, "move": False, "delete": False})

        core_ops.register_core_batch_operations(registry)
        self.assertEqual(len(registry.all()), 4)

    def test_replace_preview_and_run_agree(self):
        op = core_ops.ReplaceOperation()
        params = {"find": "dog", "replace": "hound", "mode": "word"}
        preview = run(op.preview(self.ctx, ["v1", "v2", "v3"], params))
        outcome = run(op.run(self.ctx, ["v1", "v2", "v3"], params))

        self.assertIsInstance(preview, BatchPreview)
        self.assertEqual(preview.changed, 2)
        self.assertEqual(preview.unchanged, ["v3"])
        self.assertIsInstance(outcome, BatchOutcome)
        self.assertEqual(outcome.skipped, ["v3"])
        self.assertEqual(outcome.message, "Replaced in 2 values")
        for item in preview.items:
            self.assertEqual(getattr(self.values.get_by_id(item["id"]), item["field"]), item["after"])

    def test_replace_invalid_params(self):
        with self.assertRaises(BatchOperationError) as ctx:
            run(core_ops.ReplaceOperation().run(self.ctx, ["v1"], {"replace": "x"}))
        self.assertEqual(ctx.exception.code, "invalid_params")
        with self.assertRaises(BatchOperationError) as ctx:
            run(core_ops.ReplaceOperation().run(self.ctx, ["v1"], {"find": "x", "fields": ["description"]}))
        self.assertEqual(ctx.exception.code, "invalid_params")

    def test_set_active_messages(self):
        outcome = run(core_ops.SetActiveOperation().run(self.ctx, ["v1", "v2"], {"is_active": False}))
        self.assertEqual(outcome.message, "Deactivated 2 values")
        self.assertFalse(self.values.get_by_id("v1").is_active)

        outcome = run(core_ops.SetActiveOperation().run(self.ctx, ["v1"], {"is_active": True}))
        self.assertEqual(outcome.message, "Activated 1 value")

        with self.assertRaises(BatchOperationError) as ctx:
            run(core_ops.SetActiveOperation().run(self.ctx, ["v1"], {}))
        self.assertEqual(ctx.exception.code, "invalid_params")

    def test_move(self):
        outcome = run(core_ops.MoveOperation().run(self.ctx, ["v1"], {"category_id": "dest"}))
        self.assertEqual(outcome.message, "Moved 1 value")
        self.assertEqual(self.values.get_by_id("v1").category_id, "dest")

        with self.assertRaises(BatchOperationError) as ctx:
            run(core_ops.MoveOperation().run(self.ctx, ["v2"], {"category_id": "theirs"}))
        self.assertEqual(ctx.exception.code, "unknown_category")

    def test_delete(self):
        outcome = run(core_ops.DeleteOperation().run(self.ctx, ["v1", "v3"], {}))
        self.assertEqual(outcome.deleted, ["v1", "v3"])
        self.assertEqual(outcome.message, "Deleted 2 values")
        self.assertIsNone(self.values.get_by_id("v1"))

    def test_ops_without_preview_refuse_it(self):
        with self.assertRaises(BatchOperationError) as ctx:
            run(core_ops.DeleteOperation().preview(self.ctx, ["v1"], {}))
        self.assertEqual(ctx.exception.code, "no_preview")
