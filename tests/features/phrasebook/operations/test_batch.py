"""Core batch operations against a real SQLite schema through the repository
batch context: ownership, the preview/replace parity, per-value hooks, and
all-or-nothing writes."""
from types import SimpleNamespace
from unittest.mock import Mock

from src.features.phrasebook import operations
from src.features.phrasebook.operations.batch import BatchError
from tests.features.phrasebook.test_repository_find import PhrasebookRepositoryBase


def plugin_registry(blocked_reason=None):
    registry = Mock()
    calls = []

    def execute_hook(hook, initial_data):
        calls.append((hook, dict(initial_data)))
        data = dict(initial_data)
        if blocked_reason:
            data["blocked"] = True
            data["block_reason"] = blocked_reason
        return SimpleNamespace(data=data), None

    registry.execute_hook.side_effect = execute_hook
    registry.calls = calls
    return registry


class BatchBase(PhrasebookRepositoryBase):
    def setUp(self):
        super().setUp()
        self.plugins = plugin_registry()
        self.category("cat", "Animals", "animals")
        self.category("dest", "Dest", "dest")
        self.category("theirs", "Theirs", "theirs", user_id=self.other_user)
        self.value("v1", "cat", "Small dog", "a small dog", sort_order=0)
        self.value("v2", "cat", "Big Dog", "a big DOG in a doghouse", sort_order=1)
        self.value("v3", "cat", "Cat", "a cat", sort_order=2)
        self.value("t1", "theirs", "Their dog", "their dog", user_id=self.other_user)
        self.ctx = operations.RepositoryBatchContext(self.values, self.categories, self.user_id, plugins=self.plugins)

    def assertBatchError(self, code, fn, *args, **kwargs):
        with self.assertRaises(BatchError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception


class TestContextOwnership(BatchBase):
    def test_values_dedupes_and_keeps_order(self):
        got = self.ctx.values(["v2", "v1", "v2"])
        self.assertEqual([v["id"] for v in got], ["v2", "v1"])

    def test_empty_selection(self):
        self.assertBatchError("empty_selection", self.ctx.values, [])

    def test_unknown_or_foreign_ids_listed(self):
        err = self.assertBatchError("unknown_values", self.ctx.values, ["v1", "t1", "nope"])
        self.assertEqual(err.message, "Unknown values: t1, nope")

    def test_category_is_user_scoped(self):
        self.assertEqual(self.ctx.category("cat")["path"], "animals")
        self.assertIsNone(self.ctx.category("theirs"))

    def test_update_value_texts_rejects_foreign_rows_before_writing(self):
        self.assertBatchError(
            "unknown_values", self.ctx.update_value_texts, [("v1", "x", "y"), ("t1", "x", "y")]
        )
        self.assertEqual(self.values.get_by_id("v1").label, "Small dog")


class TestPreviewAndReplace(BatchBase):
    def test_preview_lists_only_changing_fields_and_counts(self):
        result = operations.preview_replace(self.ctx, ["v1", "v2", "v3"], "dog", "hound")

        self.assertEqual(result["items"], [
            {"id": "v1", "field": "label", "before": "Small dog", "after": "Small hound"},
            {"id": "v1", "field": "value", "before": "a small dog", "after": "a small hound"},
            {"id": "v2", "field": "label", "before": "Big Dog", "after": "Big hound"},
            {"id": "v2", "field": "value", "before": "a big DOG in a doghouse", "after": "a big hound in a houndhouse"},
        ])
        self.assertEqual(result["changed"], 2)
        self.assertEqual(result["unchanged"], ["v3"])
        self.assertEqual(self.values.get_by_id("v1").label, "Small dog")

    def test_replace_writes_exactly_what_preview_showed(self):
        preview = operations.preview_replace(self.ctx, ["v1", "v2", "v3"], "dog", "hound", mode="word")
        result = operations.replace_values(
            self.ctx, self.plugins, ["v1", "v2", "v3"], "dog", "hound", mode="word"
        )

        self.assertEqual(result["skipped"], ["v3"])
        self.assertEqual([v["id"] for v in result["updated"]], ["v1", "v2"])
        for item in preview["items"]:
            self.assertEqual(getattr(self.values.get_by_id(item["id"]), item["field"]), item["after"])
        self.assertEqual(self.values.get_by_id("v2").value, "a big hound in a doghouse")
        self.assertEqual(self.values.get_by_id("v3").value, "a cat")

    def test_fields_subset_leaves_the_other_field_alone(self):
        operations.replace_values(self.ctx, self.plugins, ["v1"], "dog", "hound", fields=["value"])
        v1 = self.values.get_by_id("v1")
        self.assertEqual((v1.label, v1.value), ("Small dog", "a small hound"))

    def test_case_sensitive_replace(self):
        result = operations.replace_values(
            self.ctx, self.plugins, ["v1", "v2"], "DOG", "hound", case_sensitive=True
        )
        self.assertEqual(result["skipped"], ["v1"])
        self.assertEqual(self.values.get_by_id("v2").value, "a big hound in a doghouse")

    def test_regex_groups(self):
        operations.replace_values(self.ctx, self.plugins, ["v1"], r"(\w+) (dog)", r"\2 \1", mode="regex")
        self.assertEqual(self.values.get_by_id("v1").label, "dog Small")
        self.assertEqual(self.values.get_by_id("v1").value, "a dog small")

    def test_invalid_pattern_rejected_before_any_write(self):
        self.assertBatchError(
            "invalid_pattern", operations.replace_values, self.ctx, self.plugins, ["v1"], "(dog", "x", mode="regex",
        )
        self.assertBatchError(
            "invalid_pattern", operations.preview_replace, self.ctx, ["v1"], "(dog)", r"\3", mode="regex",
        )
        self.assertEqual(self.values.get_by_id("v1").label, "Small dog")

    def test_unknown_ids_rejected_with_no_writes(self):
        self.assertBatchError(
            "unknown_values", operations.replace_values, self.ctx, self.plugins, ["v1", "t1"], "dog", "hound",
        )
        self.assertEqual(self.values.get_by_id("v1").label, "Small dog")
        self.assertEqual(self.values.get_by_id("t1").label, "Their dog")

    def test_hooks_run_per_changed_value_and_a_block_prevents_every_write(self):
        operations.replace_values(self.ctx, self.plugins, ["v1", "v3"], "dog", "hound")
        self.assertEqual(len(self.plugins.calls), 2)
        before, after = self.plugins.calls
        self.assertEqual(before[1]["new_label"], "Small hound")
        self.assertEqual(after[1]["value_id"], "v1")

        blocking = plugin_registry("nope")
        self.assertBatchError(
            "blocked", operations.replace_values, self.ctx, blocking, ["v1", "v2"], "hound", "dog",
        )
        self.assertEqual(self.values.get_by_id("v1").label, "Small hound")
        self.assertEqual(self.values.get_by_id("v2").label, "Big Dog")

    def test_no_plugin_registry_skips_hooks(self):
        result = operations.replace_values(self.ctx, None, ["v1"], "dog", "hound")
        self.assertEqual([v["id"] for v in result["updated"]], ["v1"])


class TestSetActive(BatchBase):
    def test_deactivate_then_activate(self):
        result = operations.set_values_active(self.ctx, ["v1", "v2"], False)
        self.assertEqual([v["is_active"] for v in result["updated"]], [False, False])
        self.assertFalse(self.values.get_by_id("v1").is_active)
        self.assertTrue(self.values.get_by_id("v3").is_active)

        result = operations.set_values_active(self.ctx, ["v1"], True)
        self.assertTrue(result["updated"][0]["is_active"])
        self.assertFalse(self.values.get_by_id("v2").is_active)

    def test_foreign_id_rejected(self):
        self.assertBatchError("unknown_values", operations.set_values_active, self.ctx, ["v1", "t1"], False)
        self.assertTrue(self.values.get_by_id("v1").is_active)


class TestMove(BatchBase):
    def test_appends_after_the_target_max_in_listed_order(self):
        self.value("d1", "dest", "Existing", "x", sort_order=4)

        result = operations.move_values(self.ctx, ["v2", "v1"], "dest")

        self.assertEqual([v["id"] for v in result["updated"]], ["v2", "v1"])
        v2, v1 = self.values.get_by_id("v2"), self.values.get_by_id("v1")
        self.assertEqual((v2.category_id, v2.sort_order), ("dest", 5))
        self.assertEqual((v1.category_id, v1.sort_order), ("dest", 6))
        self.assertEqual(self.values.get_by_id("v3").category_id, "cat")

    def test_values_already_in_the_target_keep_their_sort_order(self):
        self.value("d1", "dest", "Existing", "x", sort_order=4)

        operations.move_values(self.ctx, ["d1", "v1"], "dest")

        self.assertEqual(self.values.get_by_id("d1").sort_order, 4)
        self.assertEqual(self.values.get_by_id("v1").sort_order, 5)

    def test_empty_target_starts_at_zero(self):
        operations.move_values(self.ctx, ["v3"], "dest")
        self.assertEqual(self.values.get_by_id("v3").sort_order, 0)

    def test_foreign_or_unknown_category_rejected(self):
        self.assertBatchError("unknown_category", operations.move_values, self.ctx, ["v1"], "theirs")
        self.assertBatchError("unknown_category", operations.move_values, self.ctx, ["v1"], "nope")
        self.assertEqual(self.values.get_by_id("v1").category_id, "cat")


class TestDelete(BatchBase):
    def test_deletes_and_fires_hooks(self):
        result = operations.delete_values(self.ctx, self.plugins, ["v1", "v3"])

        self.assertEqual(result["deleted"], ["v1", "v3"])
        self.assertIsNone(self.values.get_by_id("v1"))
        self.assertIsNone(self.values.get_by_id("v3"))
        self.assertIsNotNone(self.values.get_by_id("v2"))
        self.assertEqual(len(self.plugins.calls), 4)

    def test_block_prevents_every_delete(self):
        self.assertBatchError("blocked", operations.delete_values, self.ctx, plugin_registry("keep"), ["v1", "v2"])
        self.assertIsNotNone(self.values.get_by_id("v1"))
        self.assertIsNotNone(self.values.get_by_id("v2"))

    def test_foreign_id_rejected_with_no_writes(self):
        self.assertBatchError("unknown_values", operations.delete_values, self.ctx, self.plugins, ["v1", "t1"])
        self.assertIsNotNone(self.values.get_by_id("v1"))
        self.assertIsNotNone(self.values.get_by_id("t1"))
