"""
Automation execution engine.

Walks a validated automation graph starting from the node that triggered it,
persisting a run + per-node run rows as it goes, and reports live status via
an injectable `emit_ws` callback (the WS wiring itself lives in the
API-layer connection manager, added by a follow-up wiring pass - this engine
only calls the callback it's given).

Threading: triggers may fire from the asyncio event loop thread (e.g. a
schedule loop) or from a worker thread (e.g. the generation orchestrator
firing `generation.after_complete` from inside a sync hook chain, or
watchdog's own observer thread). `enqueue_trigger` is therefore a *sync*
method safe to call from any thread - mirrors
`NotificationConnectionHub.schedule_send`.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.features.automation.context import AutomationServices, NodeExecutionContext, RunContext
from src.features.automation.hooks import AUTOMATION_HOOKS
from src.platform.plugins.automation_nodes import NodeResult, NodeTypeRegistry, node_type_registry
from src.platform.plugins.hooks import HookContext
from src.features.automation.records import AutomationRun, AutomationRunNode

logger = logging.getLogger(__name__)

# Cycle backstop: graphs are also rejected at validation time (topo-sort), but
# this caps runaway execution defensively (e.g. a graph mutated after an
# in-flight run started).
MAX_NODE_VISITS = 500

# Hard cap on how many items a fan-out node (`NodeResult.items` set - see
# `src.platform.plugins.automation_nodes.NodeResult`) can push through its
# downstream subtree in one run, absent an override. Overridable per node
# instance via its own `fan_out_limit` config key - a generic engine-level
# safety valve, independent of any node-specific cap a node type exposes on
# itself (e.g. `action.scan_files`'s own `max_files`).
DEFAULT_MAX_FANOUT_ITEMS = 500

DEFAULT_NODE_TIMEOUT_S = 60.0
DEFAULT_MAX_CONCURRENT_RUNS = 5

EmitWs = Callable[[Dict[str, Any]], Awaitable[None]]


@dataclass
class _Edge:
    source: str
    source_handle: str
    target: str
    target_handle: str


@dataclass
class _FanoutAggregate:
    """
    Accrues one downstream node's outcomes across every item of a fan-out,
    backing its single `automation_run_nodes` row (see `_walk_fanout`) - the
    table gets no new columns; `executed`/`succeeded`/`failed` are folded
    into that row's existing `output` JSON column instead.
    """
    run_node_id: str
    executed: int = 0
    succeeded: int = 0
    failed: int = 0
    last_error: Optional[str] = None


class AutomationEngine:
    """Executes automation graphs. One instance shared by the whole app."""

    def __init__(
        self,
        repository,
        services: Optional[AutomationServices] = None,
        plugin_registry: Optional[Any] = None,
        registry: NodeTypeRegistry = node_type_registry,
        emit_ws: Optional[EmitWs] = None,
        max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
    ):
        self.repository = repository
        self.services = services or AutomationServices()
        self.plugin_registry = plugin_registry
        self.registry = registry
        self.emit_ws = emit_ws
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._semaphore = asyncio.Semaphore(max_concurrent_runs)

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the running event loop. Call once at app startup (lifespan())."""
        self._loop = loop

    # -- trigger entrypoint (thread-safe) -------------------------------------

    def enqueue_trigger(self, automation_id: str, trigger_node_id: str, event_payload: Dict[str, Any]) -> None:
        """
        Fire an automation run. Safe to call from any thread; never blocks or
        executes inline - always schedules the actual walk onto the event loop.
        """
        if self._loop is None:
            logger.error(
                f"[AUTOMATION_ENGINE] enqueue_trigger called before set_loop() - "
                f"dropping trigger for automation {automation_id}"
            )
            return

        coro = self._run_safely(automation_id, trigger_node_id, event_payload)

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            self._loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    def schedule_run(self, automation_id: str, trigger_node_id: str,
                     event_payload: Dict[str, Any]) -> Optional[asyncio.Future]:
        """
        Like `enqueue_trigger`, but returns a Future that resolves when the run
        finishes, so a caller can wait for it. The run is wrapped in
        `_run_safely`, so the Future never carries an exception - a failed run
        completes normally. Returns None if no loop is bound yet.
        """
        if self._loop is None:
            logger.error(
                f"[AUTOMATION_ENGINE] schedule_run called before set_loop() - "
                f"dropping trigger for automation {automation_id}"
            )
            return None

        coro = self._run_safely(automation_id, trigger_node_id, event_payload)

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            return self._loop.create_task(coro)
        return asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    async def _run_safely(self, automation_id: str, trigger_node_id: str, event_payload: Dict[str, Any]) -> None:
        """Wraps `run` so a bug in the walker never kills the enqueueing task/thread."""
        try:
            async with self._semaphore:
                await self.run(automation_id, trigger_node_id, event_payload)
        except Exception:
            logger.error(f"[AUTOMATION_ENGINE] Unhandled error running automation {automation_id}", exc_info=True)

    # -- run walker -------------------------------------------------------------

    async def run(self, automation_id: str, trigger_node_id: str, event_payload: Dict[str, Any]) -> Optional[str]:
        """Execute one run of `automation_id`, starting from `trigger_node_id`. Returns the run id."""
        automation = self.repository.get_by_id(automation_id)
        if automation is None:
            logger.error(f"[AUTOMATION_ENGINE] Automation not found: {automation_id}")
            return None

        graph = automation.graph or {}
        nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
        edges = self._parse_edges(graph.get("edges", []))

        trigger_node = nodes_by_id.get(trigger_node_id)
        trigger_type = trigger_node["type"] if trigger_node else None

        run = self.repository.create_run(AutomationRun(
            id="",
            automation_id=automation_id,
            trigger_node_id=trigger_node_id,
            trigger_type=trigger_type,
            status="running",
            event_payload=event_payload,
        ))

        self._fire_hook(AUTOMATION_HOOKS.before_run, {
            "automation_id": automation_id,
            "run_id": run.id,
            "trigger_node_id": trigger_node_id,
            "trigger_type": trigger_type,
            "event_payload": event_payload,
        })
        await self._emit(run_id=run.id, automation_id=automation_id, status="running")

        run_context = RunContext(
            automation_id=automation_id, run_id=run.id, event=event_payload, services=self.services,
        )
        run_context.upstream[trigger_node_id] = event_payload

        start = time.monotonic()
        status = "success"
        error: Optional[str] = None

        try:
            await self._walk(run_context, nodes_by_id, edges, trigger_node_id)
        except Exception as exc:
            status = "failed"
            error = str(exc)
            logger.error(f"[AUTOMATION_ENGINE] Run {run.id} for automation {automation_id} failed", exc_info=True)

        duration_ms = int((time.monotonic() - start) * 1000)
        self.repository.finish_run(run.id, status, error=error, duration_ms=duration_ms)
        self.repository.touch_last_run(automation_id, status)

        if status == "failed" and self.services.notification_manager is not None:
            try:
                self.services.notification_manager(
                    level="error", category="automation", source="automation_engine",
                    title=f"Automation '{automation.name}' failed",
                    message=error or "Unknown error", user_id=automation.user_id,
                )
            except Exception:
                logger.error("[AUTOMATION_ENGINE] Failed to send failure notification", exc_info=True)

        self._fire_hook(AUTOMATION_HOOKS.after_run, {
            "automation_id": automation_id, "run_id": run.id, "status": status,
            "duration_ms": duration_ms, "error": error,
        })
        await self._emit(run_id=run.id, automation_id=automation_id, status=status, error=error)

        return run.id

    async def _walk(self, run_context: RunContext, nodes_by_id: Dict[str, dict],
                     edges: List[_Edge], trigger_node_id: str) -> None:
        """DFS from the trigger node, following edges filtered by branch for conditions.

        A node reached through more than one converging edge (a valid,
        non-cyclic DAG shape - `AutomationRuntime._has_cycle`'s topo-sort
        allows reconvergence and only rejects true cycles) executes exactly
        once per run, not once per incoming edge (a diamond-shaped graph -
        e.g. two branches both feeding a single
        `action.send_notification` node - double-fired that node, producing
        two identical notifications for one trigger).
        This is tracked with a post-order `done` set, populated only once a
        node's own downstream subtree has fully finished via a "leave"
        marker pushed right after it executes. A true cycle can never reach
        that state - finishing node A requires re-entering A first - so the
        MAX_NODE_VISITS backstop below still trips exactly as before for a
        graph that slips past validation unvalidated (e.g. mutated after an
        in-flight run started).
        """
        visited_count = 0
        done: set = set()
        # Stack entries: ("enter", node_id, handle) to execute + queue
        # children, or ("leave", node_id) to mark a node's subtree finished.
        stack = [
            ("enter", target.target, target.target_handle)
            for target in edges if target.source == trigger_node_id
        ]

        while stack:
            frame = stack.pop()

            if frame[0] == "leave":
                done.add(frame[1])
                continue

            _, node_id, _incoming_handle = frame

            if node_id in done:
                # Reached via another converging edge - already ran this run.
                continue

            visited_count += 1
            if visited_count > MAX_NODE_VISITS:
                raise RuntimeError(f"Automation graph exceeded MAX_NODE_VISITS ({MAX_NODE_VISITS}) - possible cycle")

            node = nodes_by_id.get(node_id)
            if node is None:
                logger.warning(f"[AUTOMATION_ENGINE] Run {run_context.run_id}: dangling edge to unknown node {node_id}")
                continue

            result = await self._execute_node(run_context, node)

            stack.append(("leave", node_id))
            if result.items is not None:
                # Fan-out: this node's downstream subtree is walked once per
                # item (zero times for an empty list) by `_walk_fanout`
                # instead of being pushed onto this stack - see
                # `NodeResult.items`.
                await self._walk_fanout(run_context, nodes_by_id, edges, node_id, result.items)
                continue
            for edge in edges:
                if edge.source != node_id:
                    continue
                if result.branch is not None and edge.source_handle != result.branch:
                    continue
                stack.append(("enter", edge.target, edge.target_handle))

    async def _execute_node(self, run_context: RunContext, node: dict) -> NodeResult:
        """
        Run a single condition/action node and return its full `NodeResult`.
        `result.branch` is "out" for actions, "true"/"false" for plain
        conditions, or an arbitrary case label / "default" for dynamic-port
        nodes (`condition.switch`) - `_walk`'s edge filter below
        (`edge.source_handle != result.branch`) already treats it as an
        opaque string, no branch value is special-cased. `result.items`, when
        not `None`, routes this node's downstream subtree through
        `_walk_fanout` instead of the ordinary single-pass edge walk.
        """
        node_id = node["id"]
        node_type = node["type"]
        config = node.get("config", {}) or {}

        spec = self.registry.get(node_type)
        if spec is None:
            logger.error(f"[AUTOMATION_ENGINE] Unknown node type '{node_type}' (node {node_id}) - skipping")
            await self._create_and_finish_run_node(run_context, node_id, node_type, "skipped", error="Unknown node type")
            # branch=None takes every outgoing edge unfiltered - same as
            # before this method returned a full NodeResult.
            return NodeResult(output=None, branch=None)

        run_node = self.repository.create_run_node(AutomationRunNode(
            id="", run_id=run_context.run_id, node_id=node_id, node_type=node_type,
            input={"config": config}, status="running",
        ))
        await self._emit(run_id=run_context.run_id, automation_id=run_context.automation_id,
                          node_id=node_id, status="running")

        def set_status(status: str) -> None:
            self.repository.update_run_node(run_node.id, status=status)

        self._fire_hook(AUTOMATION_HOOKS.node_before_execute, {
            "automation_id": run_context.automation_id, "run_id": run_context.run_id,
            "node_id": node_id, "node_type": node_type, "config": config,
        })

        node_ctx = NodeExecutionContext(run=run_context, node_id=node_id, node_type=node_type,
                                         config=config, set_status=set_status)

        timeout_s = float(config.get("timeout_s") or DEFAULT_NODE_TIMEOUT_S)
        start = time.monotonic()

        try:
            if spec.execute is None:
                raise RuntimeError(f"Node type '{node_type}' has no execute() - triggers cannot appear mid-graph")
            result = await asyncio.wait_for(spec.execute(node_ctx), timeout=timeout_s)
        except asyncio.TimeoutError:
            self.repository.update_run_node(run_node.id, status="failed", error=f"Timed out after {timeout_s}s", finished=True)
            await self._emit(run_id=run_context.run_id, automation_id=run_context.automation_id,
                              node_id=node_id, status="failed", error="timeout")
            raise
        except Exception as exc:
            self.repository.update_run_node(run_node.id, status="failed", error=str(exc), finished=True)
            await self._emit(run_id=run_context.run_id, automation_id=run_context.automation_id,
                              node_id=node_id, status="failed", error=str(exc))
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        run_context.upstream[node_id] = result.output

        # `result.output` is the node's declared contract (asserted by
        # test_outputs_contract.py) and stays exactly what execute() returned
        # for `upstream[node_id]`/downstream templates. A fan-out cap
        # truncation is recorded only in the PERSISTED row's JSON, additively
        # - it never taints the declared output shape.
        persisted_output = result.output
        if result.items is not None:
            limit = int(config.get("fan_out_limit") or DEFAULT_MAX_FANOUT_ITEMS)
            total_items = len(result.items)
            if total_items > limit:
                logger.warning(
                    f"[AUTOMATION_ENGINE] Run {run_context.run_id}: node {node_id} ({node_type}) "
                    f"fan-out capped {total_items} items to {limit}"
                )
                result.items = result.items[:limit]
                persisted_output = {
                    **(result.output or {}),
                    "_fanout_truncated": True,
                    "_fanout_total_items": total_items,
                    "_fanout_limit": limit,
                }

        self.repository.update_run_node(run_node.id, status="success", output=_safe_json(persisted_output), finished=True)

        self._fire_hook(AUTOMATION_HOOKS.node_after_execute, {
            "automation_id": run_context.automation_id, "run_id": run_context.run_id,
            "node_id": node_id, "node_type": node_type, "output": result.output, "duration_ms": duration_ms,
        })
        await self._emit(run_id=run_context.run_id, automation_id=run_context.automation_id,
                          node_id=node_id, status="success")

        return result

    # -- fan-out walker ----------------------------------------------------------

    async def _walk_fanout(self, run_context: RunContext, nodes_by_id: Dict[str, dict],
                            edges: List[_Edge], scanner_node_id: str, items: List[Dict[str, Any]]) -> None:
        """
        Executes the downstream subtree of `scanner_node_id` once per item in
        `items` (already capped by `_execute_node`; zero items = zero runs).

        Each item's walk gets its own `RunContext` whose `upstream` dict is a
        SHALLOW COPY of the outer one with `upstream[scanner_node_id]`
        replaced by that item's payload - descendant outputs produced while
        walking one item are written into that copy only, so they're visible
        to nodes further downstream within THAT item but never leak back into
        `run_context.upstream` (the base context this method was called
        with, and every other item's copy). A node reached twice within one
        item's walk (a diamond inside the subtree) still executes once - the
        `done` set is reset per item, mirroring `_walk`'s own per-run dedup.

        Every downstream node gets exactly ONE `automation_run_nodes` row for
        the whole fan-out, not one per item: `_execute_fanout_node` creates
        it on first touch and folds each item's outcome into a
        `_FanoutAggregate`, finalized once all items are done. A failure at a
        node stops only THAT item's walk past that point (no exception
        propagates out of this method) - one bad item cannot fail the whole
        automation run; it still counts against the row's `failed` total.
        """
        aggregates: Dict[str, _FanoutAggregate] = {}
        fanout_edges = [edge for edge in edges if edge.source == scanner_node_id]

        for item in items:
            item_context = RunContext(
                automation_id=run_context.automation_id, run_id=run_context.run_id,
                event=run_context.event, services=run_context.services,
                upstream={**run_context.upstream, scanner_node_id: item},
            )

            done: set = set()
            stack = [("enter", edge.target, edge.target_handle) for edge in fanout_edges]
            visited_count = 0

            while stack:
                frame = stack.pop()
                if frame[0] == "leave":
                    done.add(frame[1])
                    continue

                _, node_id, _incoming_handle = frame
                if node_id in done:
                    continue

                visited_count += 1
                if visited_count > MAX_NODE_VISITS:
                    logger.error(
                        f"[AUTOMATION_ENGINE] Run {run_context.run_id}: fan-out item subtree under "
                        f"{scanner_node_id} exceeded MAX_NODE_VISITS - aborting this item's walk"
                    )
                    break

                node = nodes_by_id.get(node_id)
                if node is None:
                    continue

                result = await self._execute_fanout_node(item_context, node, aggregates)

                stack.append(("leave", node_id))
                if result is None:
                    # execute() raised for this item - already folded into
                    # the node's aggregate as a failure; don't walk its
                    # children for this item.
                    continue
                for edge in edges:
                    if edge.source != node_id:
                        continue
                    if result.branch is not None and edge.source_handle != result.branch:
                        continue
                    stack.append(("enter", edge.target, edge.target_handle))

        for node_id, aggregate in aggregates.items():
            await self._finish_fanout_aggregate(run_context, node_id, aggregate)

    async def _execute_fanout_node(self, item_context: RunContext, node: dict,
                                    aggregates: Dict[str, _FanoutAggregate]) -> Optional[NodeResult]:
        """One item's execution of one downstream node. Returns `None` on failure
        (folded into the node's aggregate) rather than raising, so `_walk_fanout`
        can move on to the next item."""
        node_id = node["id"]
        node_type = node["type"]
        config = node.get("config", {}) or {}

        spec = self.registry.get(node_type)
        if spec is None or spec.execute is None:
            logger.error(f"[AUTOMATION_ENGINE] fan-out: unknown or trigger node type '{node_type}' (node {node_id}) - skipping")
            return None

        aggregate = aggregates.get(node_id)
        if aggregate is None:
            run_node = self.repository.create_run_node(AutomationRunNode(
                id="", run_id=item_context.run_id, node_id=node_id, node_type=node_type,
                input={"config": config}, status="running",
            ))
            aggregate = _FanoutAggregate(run_node_id=run_node.id)
            aggregates[node_id] = aggregate
            await self._emit(run_id=item_context.run_id, automation_id=item_context.automation_id,
                              node_id=node_id, status="running")

        node_ctx = NodeExecutionContext(run=item_context, node_id=node_id, node_type=node_type, config=config)
        timeout_s = float(config.get("timeout_s") or DEFAULT_NODE_TIMEOUT_S)

        aggregate.executed += 1
        try:
            result = await asyncio.wait_for(spec.execute(node_ctx), timeout=timeout_s)
        except Exception as exc:
            aggregate.failed += 1
            aggregate.last_error = str(exc)
            logger.warning(f"[AUTOMATION_ENGINE] fan-out: node {node_id} ({node_type}) failed for one item: {exc}")
            return None

        aggregate.succeeded += 1
        item_context.upstream[node_id] = result.output
        return result

    async def _finish_fanout_aggregate(self, run_context: RunContext, node_id: str,
                                        aggregate: _FanoutAggregate) -> None:
        # Every item touched this node and none of them succeeded: only then
        # is the row "failed" - partial failure among a majority-success
        # fan-out still reads as "success" (the per-item counts in the row's
        # output are where the detail lives).
        status = "failed" if aggregate.executed > 0 and aggregate.succeeded == 0 else "success"
        error = aggregate.last_error if status == "failed" else None
        output = {"executed": aggregate.executed, "succeeded": aggregate.succeeded, "failed": aggregate.failed}

        self.repository.update_run_node(aggregate.run_node_id, status=status, output=_safe_json(output),
                                         error=error, finished=True)
        await self._emit(run_id=run_context.run_id, automation_id=run_context.automation_id,
                          node_id=node_id, status=status, error=error)

    async def _create_and_finish_run_node(self, run_context: RunContext, node_id: str, node_type: str,
                                           status: str, error: Optional[str] = None) -> None:
        run_node = self.repository.create_run_node(AutomationRunNode(
            id="", run_id=run_context.run_id, node_id=node_id, node_type=node_type, status="running",
        ))
        self.repository.update_run_node(run_node.id, status=status, error=error, finished=True)
        await self._emit(run_id=run_context.run_id, automation_id=run_context.automation_id,
                          node_id=node_id, status=status, error=error)

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _parse_edges(raw_edges: List[dict]) -> List[_Edge]:
        return [
            _Edge(
                source=e["source"],
                source_handle=e.get("source_handle", "out"),
                target=e["target"],
                target_handle=e.get("target_handle", "in"),
            )
            for e in raw_edges
        ]

    def _fire_hook(self, hook_name: str, data: Dict[str, Any]) -> None:
        if self.plugin_registry is None:
            return
        try:
            self.plugin_registry.execute_hook(hook_name, initial_data=data)
        except Exception:
            logger.error(f"[AUTOMATION_ENGINE] Error firing hook {hook_name}", exc_info=True)

    async def _emit(self, *, run_id: str, automation_id: str, status: str,
                     node_id: Optional[str] = None, error: Optional[str] = None) -> None:
        if self.emit_ws is None:
            return
        message = {
            "type": "automation_run_update",
            "run_id": run_id,
            "automation_id": automation_id,
            "status": status,
        }
        if node_id is not None:
            message["node_id"] = node_id
        if error is not None:
            message["error"] = error
        try:
            await self.emit_ws(message)
        except Exception:
            logger.error("[AUTOMATION_ENGINE] emit_ws callback raised", exc_info=True)


def _safe_json(value: Any) -> Optional[str]:
    """Best-effort JSON encoding of a node's output for run-node persistence."""
    import json
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return json.dumps(str(value))
