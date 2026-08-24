"""
Tests for `AutomationManager.export_automation` / `import_automation`.

The scenario that drives the design: Alice exports a workflow that watches
`/home/alice/models/loras`. Bob imports it. That directory does not exist on
Bob's machine, and `validate_graph` reports it as `severity: error`. If import
went through `create()` - which refuses any error-severity issue - Bob could
never import the workflow at all. So import splits issues by `category`:
structural problems block, environment problems become warnings and the
automation lands disabled for Bob to fix.
"""

import os
import tempfile
import unittest
from typing import Dict, List, Optional

from src.features.automation.manager import (
    EXPORT_SCHEMA,
    EXPORT_SCHEMA_VERSION,
    AutomationImportError,
    AutomationManager,
    GraphValidationError,
)
from src.features.automation.nodes import register_builtin_nodes
from src.platform.plugins.automation_nodes import NodeTypeRegistry
from src.features.automation.records import Automation

from tests.features.automation.test_manager import FakeEngine, FakeRepository


def _manager(repository=None) -> AutomationManager:
    registry = NodeTypeRegistry()
    register_builtin_nodes(registry)
    return AutomationManager(
        repository=repository or FakeRepository(),
        engine=FakeEngine(),
        registry=registry,
    )


def _graph(watch_dir: str) -> Dict:
    """A filesystem-triggered graph whose action references the trigger by node id."""
    return {
        "nodes": [
            {"id": "fs_1", "type": "trigger.filesystem", "position": {"x": 0, "y": 0},
             "config": {"directory": "__custom__", "custom_path": watch_dir, "event": "created"}},
            {"id": "idx_1", "type": "action.index_model", "position": {"x": 300, "y": 0},
             "config": {"path": "{{ event.path }}"}},
            {"id": "tag_1", "type": "action.add_tag", "position": {"x": 600, "y": 0},
             "config": {"model_id": "{{ upstream.idx_1.model_id }}", "tag_name": "auto"}},
        ],
        "edges": [
            {"id": "e1", "source": "fs_1", "source_handle": "out", "target": "idx_1", "target_handle": "in"},
            {"id": "e2", "source": "idx_1", "source_handle": "out", "target": "tag_1", "target_handle": "in"},
        ],
    }


def _envelope(graph: Dict, **overrides) -> Dict:
    envelope = {
        "schema": EXPORT_SCHEMA,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "kind": "automation",
        "exported_at": "2026-07-09T12:00:00+00:00",
        "automation": {"name": "Auto-index loras", "description": "d", "graph": graph},
        "node_types": sorted({n["type"] for n in graph["nodes"]}),
    }
    envelope.update(overrides)
    return envelope


class TestExport(unittest.TestCase):

    def setUp(self):
        self.repository = FakeRepository()
        self.manager = _manager(self.repository)

    def _store(self, graph) -> Automation:
        return self.repository.create(Automation(
            id="", name="Auto-index loras", description="d", graph=graph,
            user_id="alice", enabled=True,
        ))

    def test_returns_none_for_unknown_automation(self):
        self.assertIsNone(self.manager.export_automation("nope"))

    def test_envelope_shape(self):
        with tempfile.TemporaryDirectory() as watch_dir:
            automation = self._store(_graph(watch_dir))
            envelope = self.manager.export_automation(automation.id)

        self.assertEqual(envelope["schema"], EXPORT_SCHEMA)
        self.assertEqual(envelope["schema_version"], EXPORT_SCHEMA_VERSION)
        self.assertEqual(envelope["kind"], "automation")
        self.assertEqual(envelope["automation"]["name"], "Auto-index loras")
        self.assertIn("exported_at", envelope)
        self.assertEqual(
            envelope["node_types"],
            ["action.add_tag", "action.index_model", "trigger.filesystem"],
        )

    def test_envelope_omits_machine_local_state(self):
        """id / user_id / enabled / version / timestamps describe *this* copy, not the workflow."""
        with tempfile.TemporaryDirectory() as watch_dir:
            automation = self._store(_graph(watch_dir))
            envelope = self.manager.export_automation(automation.id)

        for machine_local in ("id", "user_id", "enabled", "version", "created_at", "updated_at"):
            self.assertNotIn(machine_local, envelope)
            self.assertNotIn(machine_local, envelope["automation"])

    def test_graph_is_copied_verbatim(self):
        with tempfile.TemporaryDirectory() as watch_dir:
            graph = _graph(watch_dir)
            automation = self._store(graph)
            envelope = self.manager.export_automation(automation.id)

        self.assertEqual(envelope["automation"]["graph"], graph)


class TestImport(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.repository = FakeRepository()
        self.manager = _manager(self.repository)

    async def test_round_trip_preserves_node_ids(self):
        """
        Node ids must survive: config values reference them as
        `{{ upstream.idx_1.model_id }}`. Rewriting ids would silently break that.
        """
        with tempfile.TemporaryDirectory() as watch_dir:
            graph = _graph(watch_dir)
            stored = self.repository.create(Automation(id="", name="n", graph=graph, enabled=True))
            envelope = self.manager.export_automation(stored.id)

            imported, warnings = await self.manager.import_automation(envelope, user_id="bob")

        self.assertEqual(warnings, [])
        self.assertEqual([n["id"] for n in imported.graph["nodes"]], ["fs_1", "idx_1", "tag_1"])
        tag_node = next(n for n in imported.graph["nodes"] if n["id"] == "tag_1")
        self.assertEqual(tag_node["config"]["model_id"], "{{ upstream.idx_1.model_id }}")

    async def test_import_creates_a_fresh_disabled_automation(self):
        with tempfile.TemporaryDirectory() as watch_dir:
            stored = self.repository.create(Automation(id="", name="n", graph=_graph(watch_dir), enabled=True))
            envelope = self.manager.export_automation(stored.id)

            imported, _ = await self.manager.import_automation(envelope, user_id="bob")

        self.assertNotEqual(imported.id, stored.id)
        self.assertFalse(imported.enabled)
        self.assertEqual(imported.user_id, "bob")

    async def test_missing_watch_directory_is_a_warning_not_a_block(self):
        """The cross-machine case: Alice's path doesn't exist for Bob."""
        alice_path = os.path.join(tempfile.gettempdir(), "definitely-not-here-potionui-test")
        self.assertFalse(os.path.isdir(alice_path))

        imported, warnings = await self.manager.import_automation(_envelope(_graph(alice_path)), user_id="bob")

        self.assertIsNotNone(imported)
        self.assertFalse(imported.enabled)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["category"], "environment")
        self.assertEqual(warnings[0]["node_id"], "fs_1")
        self.assertIn("does not exist", warnings[0]["message"])

    async def test_name_comes_from_the_envelope(self):
        with tempfile.TemporaryDirectory() as watch_dir:
            envelope = _envelope(_graph(watch_dir))
            envelope["automation"]["name"] = "Copy of Original"
            imported, _ = await self.manager.import_automation(envelope)

        self.assertEqual(imported.name, "Copy of Original")

    async def test_falls_back_to_a_default_name(self):
        with tempfile.TemporaryDirectory() as watch_dir:
            envelope = _envelope(_graph(watch_dir))
            envelope["automation"].pop("name")
            imported, _ = await self.manager.import_automation(envelope)

        self.assertEqual(imported.name, "Imported automation")


class TestImportRejects(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.manager = _manager()

    async def _assert_import_error(self, document, needle):
        with self.assertRaises(AutomationImportError) as ctx:
            await self.manager.import_automation(document)
        self.assertIn(needle, str(ctx.exception))

    async def test_rejects_non_object(self):
        await self._assert_import_error("not a dict", "must be a JSON object")

    async def test_rejects_foreign_document(self):
        await self._assert_import_error({"schema": "something.else", "kind": "automation"},
                                        "Not a PotionUI automation export")

    async def test_rejects_wrong_kind(self):
        await self._assert_import_error({"schema": EXPORT_SCHEMA, "kind": "preset"},
                                        "Not a PotionUI automation export")

    async def test_rejects_future_schema_version(self):
        with tempfile.TemporaryDirectory() as watch_dir:
            document = _envelope(_graph(watch_dir), schema_version=99)
        await self._assert_import_error(document, "Unsupported export schema_version")

    async def test_rejects_missing_graph(self):
        document = _envelope({"nodes": [], "edges": []})
        document["automation"].pop("graph")
        await self._assert_import_error(document, "missing 'automation.graph'")

    async def test_names_uninstalled_node_types(self):
        """A workflow using a plugin node type Bob hasn't installed."""
        graph = {
            "nodes": [
                {"id": "t1", "type": "trigger.manual", "config": {}},
                {"id": "a1", "type": "action.from_some_plugin", "config": {}},
                {"id": "a2", "type": "action.another_missing", "config": {}},
            ],
            "edges": [],
        }
        with self.assertRaises(AutomationImportError) as ctx:
            await self.manager.import_automation(_envelope(graph))

        message = str(ctx.exception)
        self.assertIn("not installed on this system", message)
        self.assertIn("action.another_missing", message)
        self.assertIn("action.from_some_plugin", message)
        # The generic per-node validation message must not be what the user sees.
        self.assertNotIn("Unknown node type", message)

    async def test_structural_errors_block_import(self):
        """A cycle is wrong on every machine, so it must not import."""
        graph = {
            "nodes": [
                {"id": "t1", "type": "trigger.manual", "config": {}},
                {"id": "c1", "type": "condition.compare", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t1", "source_handle": "out", "target": "c1", "target_handle": "in"},
                {"id": "e2", "source": "c1", "source_handle": "true", "target": "t1", "target_handle": "in"},
            ],
        }
        with self.assertRaises(GraphValidationError) as ctx:
            await self.manager.import_automation(_envelope(graph))

        self.assertTrue(any("cycle" in i["message"].lower() for i in ctx.exception.issues))
        self.assertTrue(all(i["category"] == "structural" for i in ctx.exception.issues))

    async def test_dangling_edge_blocks_import(self):
        graph = {
            "nodes": [{"id": "t1", "type": "trigger.manual", "config": {}}],
            "edges": [{"id": "e1", "source": "t1", "source_handle": "out",
                       "target": "ghost", "target_handle": "in"}],
        }
        with self.assertRaises(GraphValidationError):
            await self.manager.import_automation(_envelope(graph))


class TestValidateGraphCategories(unittest.TestCase):
    """`category` is what makes the import split possible; `severity` is unchanged."""

    def setUp(self):
        self.manager = _manager()

    def test_every_issue_carries_a_category(self):
        graph = {
            "nodes": [{"id": "n1", "type": "action.does_not_exist", "config": {}}],
            "edges": [],
        }
        issues = self.manager.validate_graph(graph)
        self.assertTrue(issues)
        for issue in issues:
            self.assertIn(issue["category"], ("structural", "environment"))

    def test_unknown_node_type_is_structural(self):
        issues = self.manager.validate_graph({"nodes": [{"id": "n1", "type": "nope", "config": {}}], "edges": []})
        self.assertEqual(issues[0]["category"], "structural")

    def test_absent_watch_directory_is_environment(self):
        graph = {"nodes": [{"id": "fs_1", "type": "trigger.filesystem",
                            "config": {"directory": "__custom__", "custom_path": "/no/such/dir"}}],
                 "edges": []}
        issues = self.manager.validate_graph(graph)
        self.assertEqual([i["category"] for i in issues], ["environment"])
        # Severity is untouched, so create()/update() still refuse it.
        self.assertEqual(issues[0]["severity"], "error")

    def test_unconfigured_watch_directory_is_structural(self):
        """An empty directory config is an incomplete graph, not a machine difference."""
        graph = {"nodes": [{"id": "fs_1", "type": "trigger.filesystem", "config": {}}], "edges": []}
        issues = self.manager.validate_graph(graph)
        self.assertEqual([i["category"] for i in issues], ["structural"])


if __name__ == "__main__":
    unittest.main()
