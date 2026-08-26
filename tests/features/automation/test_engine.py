"""Tests for AutomationEngine (src/core/automation/engine.py)."""

import asyncio
import json
import threading
import time
import unittest
from typing import Any, Dict, List, Optional

from src.features.automation.context import AutomationServices
from src.features.automation.engine import AutomationEngine
from src.platform.plugins.automation_nodes import NodeResult, NodeTypeRegistry, NodeTypeSpec
from src.features.automation.records import Automation, AutomationRun, AutomationRunNode


class FakeAutomationRepository:
    """In-memory stand-in for AutomationRepository, just enough for the engine's needs."""

    def __init__(self, automation: Automation):
        self._automation = automation
        self._runs: Dict[str, AutomationRun] = {}
        self._run_nodes: Dict[str, AutomationRunNode] = {}
        self._counter = 0
        self.last_run_status: Optional[str] = None

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def get_by_id(self, automation_id: str, user_id=None) -> Optional[Automation]:
        return self._automation if automation_id == self._automation.id else None

    def create_run(self, run: AutomationRun) -> AutomationRun:
        run.id = run.id or self._next_id("run")
        self._runs[run.id] = run
        return run

    def finish_run(self, run_id: str, status: str, error=None, duration_ms=None) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return False
        run.status = status
        run.error = error
        run.duration_ms = duration_ms
        return True

    def touch_last_run(self, automation_id: str, status: str) -> bool:
        self.last_run_status = status
        return True

    def create_run_node(self, run_node: AutomationRunNode) -> AutomationRunNode:
        run_node.id = run_node.id or self._next_id("node")
        self._run_nodes[run_node.id] = run_node
        return run_node

    def update_run_node(self, run_node_id: str, status: str, output=None, error=None, finished: bool = False) -> bool:
        run_node = self._run_nodes.get(run_node_id)
        if run_node is None:
            return False
        run_node.status = status
        run_node.output = output
        run_node.error = error
        return True

    def get_run(self, run_id: str) -> Optional[AutomationRun]:
        return self._runs.get(run_id)

    def list_run_nodes(self, run_id: str) -> List[AutomationRunNode]:
        return [n for n in self._run_nodes.values() if n.run_id == run_id]


def _graph(nodes, edges):
    return {"nodes": nodes, "edges": edges}


def _trigger_node(node_id="trigger1"):
    return {"id": node_id, "type": "trigger.manual", "position": {"x": 0, "y": 0}, "config": {}}


class TestAutomationEngineLinearWalk(unittest.IsolatedAsyncioTestCase):

    async def test_linear_walk_persists_run_and_node_rows(self):
        registry = NodeTypeRegistry()

        async def echo_execute(ctx):
            return NodeResult(output={"upstream_event": ctx.event})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.echo", kind="action", title="Echo", execute=echo_execute))

        automation = Automation(id="auto1", name="Linear", graph=_graph(
            nodes=[_trigger_node(), {"id": "n2", "type": "action.echo", "config": {}}],
            edges=[{"id": "e1", "source": "trigger1", "source_handle": "out", "target": "n2", "target_handle": "in"}],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        run_id = await engine.run("auto1", "trigger1", {"path": "/models/loras/x.safetensors"})

        run = repo.get_run(run_id)
        self.assertEqual(run.status, "success")
        self.assertEqual(repo.last_run_status, "success")

        run_nodes = repo.list_run_nodes(run_id)
        self.assertEqual(len(run_nodes), 1)
        self.assertEqual(run_nodes[0].node_id, "n2")
        self.assertEqual(run_nodes[0].status, "success")

    async def test_branch_routing_follows_condition_result(self):
        registry = NodeTypeRegistry()

        async def condition_execute(ctx):
            passed = ctx.event.get("value") == "krea"
            return NodeResult(output={"passed": passed}, branch="true" if passed else "false")

        calls = []

        async def action_execute(ctx):
            calls.append(ctx.node_id)
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="condition.check", kind="condition", title="Check", execute=condition_execute))
        registry.register(NodeTypeSpec(key="action.on_true", kind="action", title="OnTrue", execute=action_execute))
        registry.register(NodeTypeSpec(key="action.on_false", kind="action", title="OnFalse", execute=action_execute))

        automation = Automation(id="auto1", name="Branch", graph=_graph(
            nodes=[
                _trigger_node(),
                {"id": "cond", "type": "condition.check", "config": {}},
                {"id": "true_action", "type": "action.on_true", "config": {}},
                {"id": "false_action", "type": "action.on_false", "config": {}},
            ],
            edges=[
                {"id": "e1", "source": "trigger1", "source_handle": "out", "target": "cond", "target_handle": "in"},
                {"id": "e2", "source": "cond", "source_handle": "true", "target": "true_action", "target_handle": "in"},
                {"id": "e3", "source": "cond", "source_handle": "false", "target": "false_action", "target_handle": "in"},
            ],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        await engine.run("auto1", "trigger1", {"value": "krea"})

        self.assertEqual(calls, ["true_action"])

    async def test_walk_through_3_case_switch_node_routes_to_matching_case(self):
        """Uses the real condition.switch execute() (nodes/conditions.py), not a fake - full engine integration."""
        from src.features.automation.nodes.conditions import _execute_switch

        registry = NodeTypeRegistry()
        calls = []

        async def action_execute(ctx):
            calls.append(ctx.node_id)
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(
            key="condition.switch", kind="condition", title="Switch",
            output_ports=("default",), dynamic_ports_config_key="cases", execute=_execute_switch,
        ))
        registry.register(NodeTypeSpec(key="action.on_loras", kind="action", title="OnLoras", execute=action_execute))
        registry.register(NodeTypeSpec(key="action.on_checkpoints", kind="action", title="OnCheckpoints", execute=action_execute))
        registry.register(NodeTypeSpec(key="action.on_vae", kind="action", title="OnVae", execute=action_execute))
        registry.register(NodeTypeSpec(key="action.on_default", kind="action", title="OnDefault", execute=action_execute))

        automation = Automation(id="auto1", name="Switch", graph=_graph(
            nodes=[
                _trigger_node(),
                {"id": "switch", "type": "condition.switch",
                 "config": {"field": "event.model_type", "cases": "loras, checkpoints, vae"}},
                {"id": "on_loras", "type": "action.on_loras", "config": {}},
                {"id": "on_checkpoints", "type": "action.on_checkpoints", "config": {}},
                {"id": "on_vae", "type": "action.on_vae", "config": {}},
                {"id": "on_default", "type": "action.on_default", "config": {}},
            ],
            edges=[
                {"id": "e1", "source": "trigger1", "source_handle": "out", "target": "switch", "target_handle": "in"},
                {"id": "e2", "source": "switch", "source_handle": "loras", "target": "on_loras", "target_handle": "in"},
                {"id": "e3", "source": "switch", "source_handle": "checkpoints", "target": "on_checkpoints", "target_handle": "in"},
                {"id": "e4", "source": "switch", "source_handle": "vae", "target": "on_vae", "target_handle": "in"},
                {"id": "e5", "source": "switch", "source_handle": "default", "target": "on_default", "target_handle": "in"},
            ],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        await engine.run("auto1", "trigger1", {"model_type": "checkpoints"})
        self.assertEqual(calls, ["on_checkpoints"])

        calls.clear()
        await engine.run("auto1", "trigger1", {"model_type": "embeddings"})
        self.assertEqual(calls, ["on_default"])

    async def test_diamond_convergence_executes_shared_node_once(self):
        """Regression: a node reached through two
        converging branches (trigger -> a -> c, trigger -> b -> c - a valid,
        non-cyclic DAG shape) must execute exactly once per run, not once per
        incoming edge. Left unfixed, an `action.send_notification` node
        wired this way double-fires, producing two identical notifications
        for one trigger."""
        registry = NodeTypeRegistry()

        async def passthrough(ctx):
            return NodeResult(output={})

        calls = []

        async def notify_execute(ctx):
            calls.append(ctx.node_id)
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.echo", kind="action", title="Echo", execute=passthrough))
        registry.register(NodeTypeSpec(key="action.notify", kind="action", title="Notify", execute=notify_execute))

        automation = Automation(id="auto1", name="Diamond", graph=_graph(
            nodes=[
                _trigger_node(),
                {"id": "a", "type": "action.echo", "config": {}},
                {"id": "b", "type": "action.echo", "config": {}},
                {"id": "c", "type": "action.notify", "config": {}},
            ],
            edges=[
                {"id": "e1", "source": "trigger1", "source_handle": "out", "target": "a", "target_handle": "in"},
                {"id": "e2", "source": "trigger1", "source_handle": "out", "target": "b", "target_handle": "in"},
                {"id": "e3", "source": "a", "source_handle": "out", "target": "c", "target_handle": "in"},
                {"id": "e4", "source": "b", "source_handle": "out", "target": "c", "target_handle": "in"},
            ],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        run_id = await engine.run("auto1", "trigger1", {})

        run = repo.get_run(run_id)
        self.assertEqual(run.status, "success")
        self.assertEqual(calls, ["c"])

        run_nodes = [n for n in repo.list_run_nodes(run_id) if n.node_id == "c"]
        self.assertEqual(len(run_nodes), 1)

    async def test_cycle_is_detected_and_run_marked_failed(self):
        registry = NodeTypeRegistry()

        async def action_execute(ctx):
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.loopy", kind="action", title="Loopy", execute=action_execute))

        automation = Automation(id="auto1", name="Cyclic", graph=_graph(
            nodes=[_trigger_node(), {"id": "a", "type": "action.loopy", "config": {}}, {"id": "b", "type": "action.loopy", "config": {}}],
            edges=[
                {"id": "e1", "source": "trigger1", "source_handle": "out", "target": "a", "target_handle": "in"},
                {"id": "e2", "source": "a", "source_handle": "out", "target": "b", "target_handle": "in"},
                {"id": "e3", "source": "b", "source_handle": "out", "target": "a", "target_handle": "in"},
            ],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        run_id = await engine.run("auto1", "trigger1", {})

        run = repo.get_run(run_id)
        self.assertEqual(run.status, "failed")
        self.assertIn("MAX_NODE_VISITS", run.error)

    async def test_action_timeout_fails_run_and_node(self):
        registry = NodeTypeRegistry()

        async def slow_execute(ctx):
            await asyncio.sleep(10)
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.slow", kind="action", title="Slow", execute=slow_execute))

        automation = Automation(id="auto1", name="Slow", graph=_graph(
            nodes=[_trigger_node(), {"id": "n2", "type": "action.slow", "config": {"timeout_s": 0.05}}],
            edges=[{"id": "e1", "source": "trigger1", "source_handle": "out", "target": "n2", "target_handle": "in"}],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        run_id = await engine.run("auto1", "trigger1", {})

        run = repo.get_run(run_id)
        self.assertEqual(run.status, "failed")

        run_nodes = repo.list_run_nodes(run_id)
        self.assertEqual(run_nodes[0].status, "failed")
        self.assertIn("Timed out", run_nodes[0].error)

    async def test_unknown_node_type_marks_node_skipped_and_continues(self):
        registry = NodeTypeRegistry()

        called = []

        async def action_execute(ctx):
            called.append(ctx.node_id)
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.known", kind="action", title="Known", execute=action_execute))
        # Deliberately no "action.mystery" registered.

        automation = Automation(id="auto1", name="Unknown", graph=_graph(
            nodes=[_trigger_node(), {"id": "mystery", "type": "action.mystery", "config": {}}],
            edges=[{"id": "e1", "source": "trigger1", "source_handle": "out", "target": "mystery", "target_handle": "in"}],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        run_id = await engine.run("auto1", "trigger1", {})

        run = repo.get_run(run_id)
        self.assertEqual(run.status, "success")
        run_nodes = repo.list_run_nodes(run_id)
        self.assertEqual(run_nodes[0].status, "skipped")

    async def test_emit_ws_called_for_run_and_node_updates(self):
        registry = NodeTypeRegistry()

        async def action_execute(ctx):
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.echo", kind="action", title="Echo", execute=action_execute))

        automation = Automation(id="auto1", name="Emit", graph=_graph(
            nodes=[_trigger_node(), {"id": "n2", "type": "action.echo", "config": {}}],
            edges=[{"id": "e1", "source": "trigger1", "source_handle": "out", "target": "n2", "target_handle": "in"}],
        ))
        repo = FakeAutomationRepository(automation)

        messages = []

        async def emit_ws(message):
            messages.append(message)

        engine = AutomationEngine(repository=repo, registry=registry, emit_ws=emit_ws)
        await engine.run("auto1", "trigger1", {})

        types = [(m.get("node_id"), m["status"]) for m in messages]
        self.assertIn((None, "running"), types)
        self.assertIn(("n2", "running"), types)
        self.assertIn(("n2", "success"), types)
        self.assertIn((None, "success"), types)

    async def test_failure_sends_notification(self):
        registry = NodeTypeRegistry()

        async def failing_execute(ctx):
            raise RuntimeError("boom")

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.boom", kind="action", title="Boom", execute=failing_execute))

        automation = Automation(id="auto1", name="Fails", graph=_graph(
            nodes=[_trigger_node(), {"id": "n2", "type": "action.boom", "config": {}}],
            edges=[{"id": "e1", "source": "trigger1", "source_handle": "out", "target": "n2", "target_handle": "in"}],
        ))
        repo = FakeAutomationRepository(automation)

        notified = []

        def fake_notification_manager(**kwargs):
            notified.append(kwargs)
            return []

        services = AutomationServices(notification_manager=fake_notification_manager)
        engine = AutomationEngine(repository=repo, registry=registry, services=services)

        run_id = await engine.run("auto1", "trigger1", {})

        run = repo.get_run(run_id)
        self.assertEqual(run.status, "failed")
        self.assertEqual(len(notified), 1)
        self.assertEqual(notified[0]["level"], "error")


class TestAutomationEngineFanOut(unittest.IsolatedAsyncioTestCase):
    """`NodeResult.items` fan-out: see `AutomationEngine._walk_fanout`."""

    def _scanner_registry(self, items):
        registry = NodeTypeRegistry()

        async def scanner_execute(ctx):
            return NodeResult(output={"emitted": len(items)}, items=list(items))

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.scanner", kind="action", title="Scanner", execute=scanner_execute))
        return registry

    async def test_fanout_runs_downstream_subtree_once_per_item_with_per_item_upstream(self):
        items = [{"path": "/a"}, {"path": "/b"}, {"path": "/c"}]
        registry = self._scanner_registry(items)

        seen_paths = []

        async def sink_execute(ctx):
            seen_paths.append(ctx.upstream["scanner"]["path"])
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="action.sink", kind="action", title="Sink", execute=sink_execute))

        automation = Automation(id="auto1", name="Fanout", graph=_graph(
            nodes=[_trigger_node(), {"id": "scanner", "type": "action.scanner", "config": {}},
                   {"id": "sink", "type": "action.sink", "config": {}}],
            edges=[
                {"id": "e1", "source": "trigger1", "source_handle": "out", "target": "scanner", "target_handle": "in"},
                {"id": "e2", "source": "scanner", "source_handle": "out", "target": "sink", "target_handle": "in"},
            ],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        run_id = await engine.run("auto1", "trigger1", {})

        run = repo.get_run(run_id)
        self.assertEqual(run.status, "success")
        self.assertEqual(sorted(seen_paths), ["/a", "/b", "/c"])

        # One row for the scanner, ONE aggregate row for the sink - not one per item.
        run_nodes = repo.list_run_nodes(run_id)
        sink_rows = [n for n in run_nodes if n.node_id == "sink"]
        self.assertEqual(len(sink_rows), 1)
        self.assertEqual(json.loads(sink_rows[0].output), {"executed": 3, "succeeded": 3, "failed": 0})

    async def test_fanout_nested_condition_routes_per_item(self):
        items = [{"kind": "lora"}, {"kind": "checkpoint"}, {"kind": "lora"}]
        registry = self._scanner_registry(items)

        lora_calls = []
        other_calls = []

        async def condition_execute(ctx):
            passed = ctx.upstream["scanner"]["kind"] == "lora"
            return NodeResult(output={"passed": passed}, branch="true" if passed else "false")

        async def lora_execute(ctx):
            lora_calls.append(ctx.upstream["scanner"]["kind"])
            return NodeResult(output={})

        async def other_execute(ctx):
            other_calls.append(ctx.upstream["scanner"]["kind"])
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="condition.is_lora", kind="condition", title="IsLora", execute=condition_execute))
        registry.register(NodeTypeSpec(key="action.on_lora", kind="action", title="OnLora", execute=lora_execute))
        registry.register(NodeTypeSpec(key="action.on_other", kind="action", title="OnOther", execute=other_execute))

        automation = Automation(id="auto1", name="FanoutBranch", graph=_graph(
            nodes=[
                _trigger_node(), {"id": "scanner", "type": "action.scanner", "config": {}},
                {"id": "cond", "type": "condition.is_lora", "config": {}},
                {"id": "on_lora", "type": "action.on_lora", "config": {}},
                {"id": "on_other", "type": "action.on_other", "config": {}},
            ],
            edges=[
                {"id": "e1", "source": "trigger1", "source_handle": "out", "target": "scanner", "target_handle": "in"},
                {"id": "e2", "source": "scanner", "source_handle": "out", "target": "cond", "target_handle": "in"},
                {"id": "e3", "source": "cond", "source_handle": "true", "target": "on_lora", "target_handle": "in"},
                {"id": "e4", "source": "cond", "source_handle": "false", "target": "on_other", "target_handle": "in"},
            ],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        await engine.run("auto1", "trigger1", {})

        self.assertEqual(lora_calls, ["lora", "lora"])
        self.assertEqual(other_calls, ["checkpoint"])

    async def test_fanout_empty_items_runs_downstream_zero_times(self):
        registry = self._scanner_registry([])

        calls = []

        async def sink_execute(ctx):
            calls.append(ctx.node_id)
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="action.sink", kind="action", title="Sink", execute=sink_execute))

        automation = Automation(id="auto1", name="FanoutEmpty", graph=_graph(
            nodes=[_trigger_node(), {"id": "scanner", "type": "action.scanner", "config": {}},
                   {"id": "sink", "type": "action.sink", "config": {}}],
            edges=[
                {"id": "e1", "source": "trigger1", "source_handle": "out", "target": "scanner", "target_handle": "in"},
                {"id": "e2", "source": "scanner", "source_handle": "out", "target": "sink", "target_handle": "in"},
            ],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        run_id = await engine.run("auto1", "trigger1", {})

        self.assertEqual(calls, [])
        run_nodes = repo.list_run_nodes(run_id)
        self.assertEqual([n.node_id for n in run_nodes], ["scanner"])

    async def test_fanout_caps_items_and_records_truncation_on_the_scanner_run_node(self):
        items = [{"i": i} for i in range(5)]
        registry = self._scanner_registry(items)

        calls = []

        async def sink_execute(ctx):
            calls.append(ctx.upstream["scanner"]["i"])
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="action.sink", kind="action", title="Sink", execute=sink_execute))

        automation = Automation(id="auto1", name="FanoutCap", graph=_graph(
            nodes=[_trigger_node(), {"id": "scanner", "type": "action.scanner", "config": {"fan_out_limit": 2}},
                   {"id": "sink", "type": "action.sink", "config": {}}],
            edges=[
                {"id": "e1", "source": "trigger1", "source_handle": "out", "target": "scanner", "target_handle": "in"},
                {"id": "e2", "source": "scanner", "source_handle": "out", "target": "sink", "target_handle": "in"},
            ],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        run_id = await engine.run("auto1", "trigger1", {})

        self.assertEqual(len(calls), 2)

        scanner_row = next(n for n in repo.list_run_nodes(run_id) if n.node_id == "scanner")
        output = json.loads(scanner_row.output)
        self.assertTrue(output["_fanout_truncated"])
        self.assertEqual(output["_fanout_total_items"], 5)
        self.assertEqual(output["_fanout_limit"], 2)
        # The declared output contract (`emitted`) survives alongside the truncation note.
        self.assertEqual(output["emitted"], 5)

    async def test_single_payload_node_is_unaffected_by_fanout_support(self):
        """Regression: a node that never sets `items` (the overwhelming majority)
        walks exactly as before - `NodeResult.items` defaults to `None`."""
        registry = NodeTypeRegistry()

        async def action_execute(ctx):
            return NodeResult(output={"ok": True})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.plain", kind="action", title="Plain", execute=action_execute))

        automation = Automation(id="auto1", name="Plain", graph=_graph(
            nodes=[_trigger_node(), {"id": "n2", "type": "action.plain", "config": {}}],
            edges=[{"id": "e1", "source": "trigger1", "source_handle": "out", "target": "n2", "target_handle": "in"}],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        run_id = await engine.run("auto1", "trigger1", {})

        run_nodes = repo.list_run_nodes(run_id)
        self.assertEqual(len(run_nodes), 1)
        self.assertEqual(json.loads(run_nodes[0].output), {"ok": True})


class TestEnqueueTriggerCrossThread(unittest.IsolatedAsyncioTestCase):

    async def test_enqueue_trigger_from_worker_thread_runs_on_loop(self):
        registry = NodeTypeRegistry()

        executed = threading.Event()

        async def action_execute(ctx):
            executed.set()
            return NodeResult(output={})

        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))
        registry.register(NodeTypeSpec(key="action.mark", kind="action", title="Mark", execute=action_execute))

        automation = Automation(id="auto1", name="CrossThread", graph=_graph(
            nodes=[_trigger_node(), {"id": "n2", "type": "action.mark", "config": {}}],
            edges=[{"id": "e1", "source": "trigger1", "source_handle": "out", "target": "n2", "target_handle": "in"}],
        ))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)
        engine.set_loop(asyncio.get_running_loop())

        def call_from_thread():
            engine.enqueue_trigger("auto1", "trigger1", {"from": "worker-thread"})

        thread = threading.Thread(target=call_from_thread)
        thread.start()
        thread.join()

        # Give the run_coroutine_threadsafe-scheduled coroutine a chance to complete.
        for _ in range(50):
            if executed.is_set():
                break
            await asyncio.sleep(0.05)

        self.assertTrue(executed.is_set())

    async def test_enqueue_trigger_without_set_loop_does_not_raise(self):
        registry = NodeTypeRegistry()
        registry.register(NodeTypeSpec(key="trigger.manual", kind="trigger", title="Manual"))

        automation = Automation(id="auto1", name="NoLoop", graph=_graph(nodes=[_trigger_node()], edges=[]))
        repo = FakeAutomationRepository(automation)
        engine = AutomationEngine(repository=repo, registry=registry)

        # No set_loop() call - should log and return, not raise.
        engine.enqueue_trigger("auto1", "trigger1", {})


if __name__ == '__main__':
    unittest.main()
