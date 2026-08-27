"""Tests for AutomationRuntime (src/core/automation/manager.py)."""

import unittest
from typing import Dict, List, Optional

from src.features.automation.context import AutomationServices
from src.features.automation.runtime import AutomationRuntime, GraphValidationError
from src.platform.plugins.automation_nodes import NodeTypeRegistry, NodeTypeSpec
from src.features.automation.records import Automation


class FakeRepository:
    def __init__(self):
        self._by_id: Dict[str, Automation] = {}
        self._counter = 0

    def create(self, automation: Automation) -> Automation:
        self._counter += 1
        automation.id = automation.id or f"auto-{self._counter}"
        self._by_id[automation.id] = automation
        return automation

    def get_by_id(self, automation_id: str, user_id=None) -> Optional[Automation]:
        return self._by_id.get(automation_id)

    def get_all(self, user_id=None, enabled_only: bool = False) -> List[Automation]:
        values = list(self._by_id.values())
        if enabled_only:
            values = [a for a in values if a.enabled]
        return values

    def update(self, automation: Automation, bump_version: bool = False) -> Optional[Automation]:
        if automation.id not in self._by_id:
            return None
        if bump_version:
            automation.version += 1
        self._by_id[automation.id] = automation
        return automation

    def set_enabled(self, automation_id: str, enabled: bool) -> bool:
        automation = self._by_id.get(automation_id)
        if automation is None:
            return False
        automation.enabled = enabled
        return True

    def delete(self, automation_id: str) -> bool:
        return self._by_id.pop(automation_id, None) is not None


class FakeEngine:
    def __init__(self):
        self.services = AutomationServices()
        self.runs = []

    def enqueue_trigger(self, automation_id, node_id, payload):
        self.runs.append((automation_id, node_id, payload))

    async def run(self, automation_id, trigger_node_id, event_payload):
        self.runs.append((automation_id, trigger_node_id, event_payload))
        return "fake-run-id"


def _registry_with_builtins() -> NodeTypeRegistry:
    registry = NodeTypeRegistry()

    async def noop_execute(ctx):
        from src.platform.plugins.automation_nodes import NodeResult
        return NodeResult(output={})

    registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
    registry.register(NodeTypeSpec(key="action.noop", kind="action", title="Noop", execute=noop_execute))
    registry.register(NodeTypeSpec(key="condition.compare", kind="condition", title="Compare", execute=noop_execute))
    return registry


def _valid_graph():
    return {
        "nodes": [
            {"id": "t1", "type": "trigger.manual", "config": {}},
            {"id": "a1", "type": "action.noop", "config": {}},
        ],
        "edges": [{"id": "e1", "source": "t1", "source_handle": "out", "target": "a1", "target_handle": "in"}],
    }


class TestValidateGraph(unittest.TestCase):

    def setUp(self):
        self.manager = AutomationRuntime(repository=FakeRepository(), engine=FakeEngine(), registry=_registry_with_builtins())

    def test_valid_graph_has_no_issues(self):
        issues = self.manager.validate_graph(_valid_graph())
        self.assertEqual(issues, [])

    def test_unknown_node_type_is_an_error(self):
        graph = {"nodes": [{"id": "n1", "type": "action.does_not_exist", "config": {}}], "edges": []}
        issues = self.manager.validate_graph(graph)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "error")
        self.assertIn("Unknown node type", issues[0]["message"])

    def test_dangling_edge_source_is_an_error(self):
        graph = {
            "nodes": [{"id": "a1", "type": "action.noop", "config": {}}],
            "edges": [{"id": "e1", "source": "ghost", "target": "a1"}],
        }
        issues = self.manager.validate_graph(graph)

        self.assertTrue(any("Dangling edge" in i["message"] and "ghost" in i["message"] for i in issues))

    def test_dangling_edge_target_is_an_error(self):
        graph = {
            "nodes": [{"id": "t1", "type": "trigger.manual", "config": {}}],
            "edges": [{"id": "e1", "source": "t1", "target": "ghost"}],
        }
        issues = self.manager.validate_graph(graph)

        self.assertTrue(any("Dangling edge" in i["message"] and "ghost" in i["message"] for i in issues))

    def test_cycle_is_detected(self):
        graph = {
            "nodes": [
                {"id": "t1", "type": "trigger.manual", "config": {}},
                {"id": "a1", "type": "action.noop", "config": {}},
                {"id": "a2", "type": "action.noop", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t1", "target": "a1"},
                {"id": "e2", "source": "a1", "target": "a2"},
                {"id": "e3", "source": "a2", "target": "a1"},
            ],
        }
        issues = self.manager.validate_graph(graph)

        self.assertTrue(any("cycle" in i["message"].lower() for i in issues))

    def test_acyclic_diamond_graph_has_no_cycle_issue(self):
        graph = {
            "nodes": [
                {"id": "t1", "type": "trigger.manual", "config": {}},
                {"id": "c1", "type": "condition.compare", "config": {}},
                {"id": "a1", "type": "action.noop", "config": {}},
                {"id": "a2", "type": "action.noop", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t1", "target": "c1"},
                {"id": "e2", "source": "c1", "source_handle": "true", "target": "a1"},
                {"id": "e3", "source": "c1", "source_handle": "false", "target": "a2"},
            ],
        }
        issues = self.manager.validate_graph(graph)
        self.assertEqual(issues, [])


class TestValidateGraphFilesystemDirectory(unittest.TestCase):
    """`trigger.filesystem` directory checks - custom absolute paths are allowed
    (no allow-list) but must exist and be a directory."""

    def setUp(self):
        registry = _registry_with_builtins()
        from src.features.automation.triggers.filesystem import CUSTOM_PATH_VALUE, list_app_directories
        registry.register(NodeTypeSpec(
            key="trigger.filesystem", kind="trigger", title="File Watcher",
            config_schema=[
                {"name": "directory", "type": "select", "title": "Directory",
                 "options_provider": list_app_directories},
                {"name": "custom_path", "type": "textbox", "title": "Custom Path", "visible": False},
            ],
        ))
        self.manager = AutomationRuntime(repository=FakeRepository(), engine=FakeEngine(), registry=registry)

    def _graph(self, config):
        return {"nodes": [{"id": "t1", "type": "trigger.filesystem", "config": config}], "edges": []}

    def test_missing_directory_is_an_error(self):
        issues = self.manager.validate_graph(self._graph({}))
        self.assertTrue(any("required" in i["message"].lower() for i in issues))

    def test_custom_path_that_does_not_exist_is_an_error(self):
        import uuid
        bogus = f"/tmp/does-not-exist-{uuid.uuid4().hex}"
        issues = self.manager.validate_graph(self._graph({"directory": "__custom__", "custom_path": bogus}))
        self.assertTrue(any("does not exist" in i["message"] for i in issues))

    def test_custom_path_that_exists_is_allowed_no_allow_list(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            issues = self.manager.validate_graph(self._graph({"directory": "__custom__", "custom_path": tmp}))
            self.assertEqual(issues, [])

    def test_app_directory_choice_pointing_at_a_file_not_a_dir_is_an_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as tmp_file:
            issues = self.manager.validate_graph(self._graph({"directory": tmp_file.name}))
            self.assertTrue(any("does not exist" in i["message"] for i in issues))


class TestValidateGraphOutputPorts(unittest.TestCase):
    """Edges must target one of a node's actual output ports - dynamic (condition.switch
    cases + "default") or static (plain condition true/false, action/trigger "out")."""

    def setUp(self):
        registry = _registry_with_builtins()
        from src.features.automation.nodes.conditions import _execute_switch
        registry.register(NodeTypeSpec(
            key="condition.switch", kind="condition", title="Switch",
            output_ports=("default",), dynamic_ports_config_key="cases", execute=_execute_switch,
        ))
        self.manager = AutomationRuntime(repository=FakeRepository(), engine=FakeEngine(), registry=registry)

    def test_edge_on_a_current_case_is_valid(self):
        graph = {
            "nodes": [
                {"id": "t1", "type": "trigger.manual", "config": {}},
                {"id": "sw", "type": "condition.switch", "config": {"field": "event.x", "cases": "a, b"}},
                {"id": "act", "type": "action.noop", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t1", "target": "sw"},
                {"id": "e2", "source": "sw", "source_handle": "a", "target": "act"},
            ],
        }
        issues = self.manager.validate_graph(graph)
        self.assertEqual(issues, [])

    def test_edge_on_default_port_is_always_valid(self):
        graph = {
            "nodes": [
                {"id": "t1", "type": "trigger.manual", "config": {}},
                {"id": "sw", "type": "condition.switch", "config": {"field": "event.x", "cases": "a, b"}},
                {"id": "act", "type": "action.noop", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t1", "target": "sw"},
                {"id": "e2", "source": "sw", "source_handle": "default", "target": "act"},
            ],
        }
        issues = self.manager.validate_graph(graph)
        self.assertEqual(issues, [])

    def test_edge_on_a_removed_case_is_an_error(self):
        graph = {
            "nodes": [
                {"id": "t1", "type": "trigger.manual", "config": {}},
                # "cases" no longer includes "vae" - the switch was edited after this edge was drawn.
                {"id": "sw", "type": "condition.switch", "config": {"field": "event.x", "cases": "a, b"}},
                {"id": "act", "type": "action.noop", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t1", "target": "sw"},
                {"id": "e2", "source": "sw", "source_handle": "vae", "target": "act"},
            ],
        }
        issues = self.manager.validate_graph(graph)
        self.assertTrue(any("removed case" in i["message"] and "vae" in i["message"] for i in issues))

    def test_plain_condition_true_false_still_validated(self):
        graph = {
            "nodes": [
                {"id": "t1", "type": "trigger.manual", "config": {}},
                {"id": "c1", "type": "condition.compare", "config": {}},
                {"id": "act", "type": "action.noop", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t1", "target": "c1"},
                # "maybe" isn't a valid handle for a plain true/false condition.
                {"id": "e2", "source": "c1", "source_handle": "maybe", "target": "act"},
            ],
        }
        issues = self.manager.validate_graph(graph)
        self.assertTrue(any("unknown output port" in i["message"] for i in issues))

    def test_plain_condition_true_false_edges_are_valid(self):
        graph = {
            "nodes": [
                {"id": "t1", "type": "trigger.manual", "config": {}},
                {"id": "c1", "type": "condition.compare", "config": {}},
                {"id": "act", "type": "action.noop", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t1", "target": "c1"},
                {"id": "e2", "source": "c1", "source_handle": "true", "target": "act"},
            ],
        }
        issues = self.manager.validate_graph(graph)
        self.assertEqual(issues, [])


class TestCreateRaisesOnInvalidGraph(unittest.IsolatedAsyncioTestCase):

    async def test_create_raises_graph_validation_error(self):
        manager = AutomationRuntime(repository=FakeRepository(), engine=FakeEngine(), registry=_registry_with_builtins())
        bad_graph = {"nodes": [{"id": "n1", "type": "action.ghost", "config": {}}], "edges": []}

        with self.assertRaises(GraphValidationError):
            await manager.create(name="Bad", graph=bad_graph)


class TestTriggerLifecycle(unittest.IsolatedAsyncioTestCase):

    async def test_start_all_enabled_starts_manual_trigger_and_stop_all_stops_it(self):
        repository = FakeRepository()
        engine = FakeEngine()
        manager = AutomationRuntime(repository=repository, engine=engine, registry=_registry_with_builtins())

        automation = await manager.create(name="Manual Flow", graph=_valid_graph(), enabled=True)
        self.assertEqual(len(manager._active_triggers), 1)

        await manager.stop_all()
        self.assertEqual(len(manager._active_triggers), 0)

    async def test_disabling_stops_trigger(self):
        repository = FakeRepository()
        engine = FakeEngine()
        manager = AutomationRuntime(repository=repository, engine=engine, registry=_registry_with_builtins())

        automation = await manager.create(name="Manual Flow", graph=_valid_graph(), enabled=True)
        self.assertEqual(len(manager._active_triggers), 1)

        await manager.set_enabled(automation.id, False)
        self.assertEqual(len(manager._active_triggers), 0)

    async def test_run_now_uses_first_trigger_node(self):
        repository = FakeRepository()
        engine = FakeEngine()
        manager = AutomationRuntime(repository=repository, engine=engine, registry=_registry_with_builtins())

        automation = await manager.create(name="Manual Flow", graph=_valid_graph(), enabled=False)
        run_id = await manager.run_now(automation.id)

        self.assertEqual(run_id, "fake-run-id")
        self.assertEqual(engine.runs[-1][:2], (automation.id, "t1"))


if __name__ == '__main__':
    unittest.main()
