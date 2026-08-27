"""Tests for immutable automation templates and safe instantiation."""

import json
import tempfile
import unittest
from pathlib import Path

from src.features.automation.runtime import (
    AutomationRuntime,
    AutomationTemplateUnavailableError,
)
from src.features.automation.nodes import register_builtin_nodes
from src.features.automation.templates import register_builtin_templates
from src.platform.plugins.automation_templates import (
    AutomationTemplateRegistrationError,
    AutomationTemplateRegistry,
)
from src.platform.plugins.automation_nodes import NodeTypeRegistry

from tests.features.automation.test_runtime import FakeEngine, FakeRepository


def _document(*, node_type: str = "trigger.manual", declared=None) -> dict:
    return {
        "schema": "potionui.automation",
        "schema_version": 1,
        "kind": "automation",
        "automation": {
            "name": "Starter workflow",
            "description": "A template used by tests.",
            "graph": {
                "nodes": [{"id": "start", "type": node_type, "config": {}}],
                "edges": [],
            },
        },
        "node_types": list(declared if declared is not None else [node_type]),
    }


def _register_document(
    registry: AutomationTemplateRegistry, root: Path, document: dict
):
    path = root / "starter.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return registry.register_from_file(
        source="example-plugin",
        source_name="Example Plugin",
        template_id="starter",
        title="Starter",
        description="Start here",
        category="examples",
        icon="bolt",
        tags=["example"],
        path=path,
        root=root,
    )


class TestAutomationTemplateRegistry(unittest.TestCase):
    def test_registers_namespaced_metadata_and_derives_graph_requirements(self):
        registry = AutomationTemplateRegistry()
        with tempfile.TemporaryDirectory() as temp_dir:
            template = _register_document(
                registry,
                Path(temp_dir),
                _document(declared=["action.declared_only"]),
            )

        self.assertEqual(template.key, "example-plugin:starter")
        self.assertEqual(template.source_name, "Example Plugin")
        self.assertEqual(
            template.node_types,
            ("action.declared_only", "trigger.manual"),
        )
        self.assertEqual(registry.get(template.key), template)
        self.assertEqual(registry.all(), [template])

    def test_clone_is_mutable_without_changing_catalog_document(self):
        registry = AutomationTemplateRegistry()
        with tempfile.TemporaryDirectory() as temp_dir:
            template = _register_document(registry, Path(temp_dir), _document())

        clone = template.clone_document()
        clone["automation"]["name"] = "Changed"
        clone["automation"]["graph"]["nodes"][0]["type"] = "action.changed"

        fresh = template.clone_document()
        self.assertEqual(fresh["automation"]["name"], "Starter workflow")
        self.assertEqual(template.node_types, ("trigger.manual",))

    def test_rejects_duplicate_namespaced_key(self):
        registry = AutomationTemplateRegistry()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _register_document(registry, root, _document())
            with self.assertRaises(AutomationTemplateRegistrationError) as context:
                _register_document(registry, root, _document())

        self.assertIn("example-plugin:starter", str(context.exception))

    def test_rejects_path_outside_source_directory(self):
        registry = AutomationTemplateRegistry()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "plugin"
            root.mkdir()
            outside = base / "outside.json"
            outside.write_text(json.dumps(_document()), encoding="utf-8")

            with self.assertRaises(AutomationTemplateRegistrationError) as context:
                registry.register_from_file(
                    source="plugin",
                    source_name="Plugin",
                    template_id="outside",
                    title="Outside",
                    path=outside,
                    root=root,
                )

        self.assertIn("escapes its source directory", str(context.exception))

    def test_rejects_invalid_portable_envelope(self):
        registry = AutomationTemplateRegistry()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(AutomationTemplateRegistrationError) as context:
                _register_document(registry, root, {"schema": "something.else"})

        self.assertIn("not a PotionUI automation envelope", str(context.exception))

    def test_rejects_malformed_graph_node(self):
        registry = AutomationTemplateRegistry()
        document = _document()
        document["automation"]["graph"]["nodes"] = ["not-a-node"]
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(AutomationTemplateRegistrationError) as context:
                _register_document(registry, Path(temp_dir), document)

        self.assertIn("invalid graph node", str(context.exception))

    def test_unregister_source_only_removes_that_sources_templates(self):
        registry = AutomationTemplateRegistry()
        register_builtin_templates(registry)
        with tempfile.TemporaryDirectory() as temp_dir:
            _register_document(registry, Path(temp_dir), _document())

        registry.unregister_source("example-plugin")

        self.assertIsNone(registry.get("example-plugin:starter"))
        self.assertIsNotNone(registry.get("core:index-new-model-files"))


class TestAutomationTemplates(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.node_registry = NodeTypeRegistry()
        register_builtin_nodes(self.node_registry)
        self.template_registry = AutomationTemplateRegistry()
        register_builtin_templates(self.template_registry)
        self.repository = FakeRepository()
        self.manager = AutomationRuntime(
            repository=self.repository,
            engine=FakeEngine(),
            registry=self.node_registry,
            template_registry=self.template_registry,
        )

    def test_catalog_reports_runtime_availability_without_exposing_document(self):
        templates = self.manager.list_templates()

        self.assertEqual(len(templates), 3)
        by_key = {t["key"]: t for t in templates}
        summary = by_key["core:index-new-model-files"]
        self.assertTrue(summary["available"])
        self.assertEqual(summary["missing_node_types"], [])
        self.assertNotIn("document", summary)
        self.assertNotIn("document_json", summary)
        self.assertNotIn("automation", summary)

        # The Ollama-eviction template references a plugin node type, so with
        # only built-in nodes registered it lists but reports itself unavailable
        # rather than being hidden or blowing up - the disabled-plugin path.
        evict = by_key["core:evict-ollama-before-generation"]
        self.assertFalse(evict["available"])
        self.assertEqual(evict["missing_node_types"], ["action.ollama_unload"])

    def test_index_gallery_when_idle_template_is_available_with_builtin_nodes(self):
        summary = next(
            t for t in self.manager.list_templates()
            if t["key"] == "core:index-gallery-when-idle"
        )

        self.assertEqual(summary["title"], "Index gallery when idle")
        self.assertTrue(summary["available"])
        self.assertEqual(summary["missing_node_types"], [])
        self.assertEqual(
            set(summary["node_types"]),
            {"trigger.gpu_threshold", "action.index_media_queue", "action.send_notification"},
        )

    def test_catalog_names_missing_node_types(self):
        manager = AutomationRuntime(
            repository=FakeRepository(),
            engine=FakeEngine(),
            registry=NodeTypeRegistry(),
            template_registry=self.template_registry,
        )

        summary = manager.list_templates()[0]

        self.assertFalse(summary["available"])
        self.assertEqual(summary["missing_node_types"], summary["node_types"])

    async def test_instantiation_creates_fresh_disabled_user_owned_automation(self):
        automation, warnings = await self.manager.instantiate_template(
            "core:index-new-model-files",
            user_id="user-123",
        )

        self.assertEqual(automation.name, "Index new model files")
        self.assertEqual(automation.user_id, "user-123")
        self.assertFalse(automation.enabled)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["category"], "environment")

    async def test_name_override_does_not_mutate_template_or_next_copy(self):
        renamed, _ = await self.manager.instantiate_template(
            "core:index-new-model-files",
            name="My model watcher",
        )
        original, _ = await self.manager.instantiate_template(
            "core:index-new-model-files"
        )

        self.assertEqual(renamed.name, "My model watcher")
        self.assertEqual(original.name, "Index new model files")
        self.assertNotEqual(renamed.id, original.id)

    async def test_unavailable_template_cannot_be_instantiated(self):
        manager = AutomationRuntime(
            repository=FakeRepository(),
            engine=FakeEngine(),
            registry=NodeTypeRegistry(),
            template_registry=self.template_registry,
        )

        with self.assertRaises(AutomationTemplateUnavailableError) as context:
            await manager.instantiate_template("core:index-new-model-files")

        self.assertIn("not installed", str(context.exception))
        self.assertEqual(manager.repository.get_all(), [])


class TestLocalTemplateRoot(unittest.TestCase):
    """`content/automation/local/*.json` - mirrors `content/plugins/local`/
    `content/presets/local`: user-owned, `.gitignored`, scanned additionally
    to the curated core catalog. See `register_local_templates`."""

    def _document(self, name="My Automation", description="Does a thing"):
        return {
            "schema": "potionui.automation",
            "schema_version": 1,
            "kind": "automation",
            "automation": {
                "name": name,
                "description": description,
                "graph": {"nodes": [{"id": "start", "type": "trigger.manual", "config": {}}], "edges": []},
            },
            "node_types": ["trigger.manual"],
        }

    def test_local_template_is_discovered_with_metadata_from_the_envelope(self):
        registry = AutomationTemplateRegistry()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            (local_root / "my-template.json").write_text(json.dumps(self._document()), encoding="utf-8")

            register_builtin_templates(registry, marketplace_root=Path(temp_dir) / "empty-marketplace", local_root=local_root)

        template = registry.get("local:my-template")
        self.assertIsNotNone(template)
        self.assertEqual(template.title, "My Automation")
        self.assertEqual(template.description, "Does a thing")

    def test_local_template_without_a_name_falls_back_to_filename(self):
        registry = AutomationTemplateRegistry()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            document = self._document(name=None)
            del document["automation"]["name"]
            (local_root / "nameless.json").write_text(json.dumps(document), encoding="utf-8")

            register_builtin_templates(registry, marketplace_root=Path(temp_dir) / "empty-marketplace", local_root=local_root)

        template = registry.get("local:nameless")
        self.assertEqual(template.title, "nameless")

    def test_malformed_local_template_is_skipped_not_fatal(self):
        registry = AutomationTemplateRegistry()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            (local_root / "broken.json").write_text("{not json", encoding="utf-8")
            (local_root / "good.json").write_text(json.dumps(self._document()), encoding="utf-8")

            register_builtin_templates(registry, marketplace_root=Path(temp_dir) / "empty-marketplace", local_root=local_root)

        self.assertIsNone(registry.get("local:broken"))
        self.assertIsNotNone(registry.get("local:good"))

    def test_absent_local_root_does_not_crash(self):
        registry = AutomationTemplateRegistry()
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_local = Path(temp_dir) / "does-not-exist"
            self.assertFalse(missing_local.exists())

            register_builtin_templates(
                registry, marketplace_root=Path(temp_dir) / "empty-marketplace", local_root=missing_local
            )

        self.assertEqual([t for t in registry.all() if t.source == "local"], [])


if __name__ == "__main__":
    unittest.main()
