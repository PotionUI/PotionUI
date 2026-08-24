"""
Bridges backend hook events (`generation.after_complete`, `model_index.*`,
`tag.*`, ...) into automation triggers.

`HookChain.register` keeps exactly one handler per `(hook_name, plugin_id)`
pair - registering per-automation would mean each new automation silently
replaces the previous one's handler for the same hook. Instead we register
exactly one dispatcher per hook name, under a fixed `plugin_id`, and fan it
out to every subscribed `(automation_id, node_id)` ourselves.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from src.features.automation.triggers.base import TriggerSource
from src.platform.plugins.hooks import HOOK_BLOCKING_WAITS_KEY, HookChain, HookContext

logger = logging.getLogger(__name__)

DISPATCHER_PLUGIN_ID = "automation_engine"

DEFAULT_WAIT_TIMEOUT_S = 30.0


class HookEventBridge:
    """Owns the single dispatcher-per-hook-name registration against a `HookChain`."""

    def __init__(self, hook_chain: HookChain):
        self._hook_chain = hook_chain
        # hook_name -> {(automation_id, node_id): TriggerSource}
        self._subscribers: Dict[str, Dict[Tuple[str, str], TriggerSource]] = {}

    def subscribe(self, hook_name: str, trigger: TriggerSource) -> None:
        key = (trigger.automation_id, trigger.node_id)
        first_subscriber = hook_name not in self._subscribers or not self._subscribers[hook_name]

        self._subscribers.setdefault(hook_name, {})[key] = trigger

        if first_subscriber:
            self._hook_chain.register(hook_name, DISPATCHER_PLUGIN_ID, self._make_dispatcher(hook_name))
            logger.debug(f"[HOOK_BRIDGE] Registered dispatcher for '{hook_name}'")

    def unsubscribe(self, hook_name: str, automation_id: str, node_id: str) -> None:
        subscribers = self._subscribers.get(hook_name)
        if not subscribers:
            return

        subscribers.pop((automation_id, node_id), None)

        if not subscribers:
            del self._subscribers[hook_name]
            self._hook_chain.unregister(hook_name, DISPATCHER_PLUGIN_ID)
            logger.debug(f"[HOOK_BRIDGE] Unregistered dispatcher for '{hook_name}' (no subscribers left)")

    def _make_dispatcher(self, hook_name: str):
        def dispatcher(context: HookContext) -> HookContext:
            # Copy the payload per-subscriber; never mutate the chain's context,
            # and never execute an automation inline from here (plan risk #3).
            # A subscriber that opted into "wait for completion" hands back an
            # awaitable instead of firing-and-forgetting; those are collected
            # under HOOK_BLOCKING_WAITS_KEY for the async call site to await
            # (see platform.plugins.hooks.await_hook_blocking_waits).
            payload = dict(context.data)
            waits = []
            for trigger in list(self._subscribers.get(hook_name, {}).values()):
                try:
                    wait = trigger.dispatch(dict(payload))
                    if wait is not None:
                        waits.append(wait)
                except Exception:
                    logger.error(f"[HOOK_BRIDGE] Error dispatching '{hook_name}' to automation trigger", exc_info=True)
            if waits:
                existing = context.data.get(HOOK_BLOCKING_WAITS_KEY) or []
                context.data[HOOK_BLOCKING_WAITS_KEY] = existing + waits
            return context

        return dispatcher


class HookEventTrigger(TriggerSource):
    """`trigger.hook_event` - fires when a given backend hook name executes.

    With `wait_for_completion` set, the hook dispatch does not just enqueue and
    return: it awaits the triggered run (up to `wait_timeout_s`, then proceeds
    anyway) at any async call site that drains HOOK_BLOCKING_WAITS_KEY - today,
    generation.before_start. A failed or timed-out run never blocks or vetoes
    the hook's own operation.
    """

    def __init__(self, automation_id: str, node_id: str, config: Dict[str, Any],
                 enqueue, bridge: HookEventBridge,
                 schedule_run: Optional[Callable[..., Optional[Awaitable]]] = None):
        super().__init__(automation_id, node_id, config, enqueue)
        self._bridge = bridge
        self._schedule_run = schedule_run
        self._hook_name = config.get("hook_name", "")
        self._wait = bool(config.get("wait_for_completion", False))
        self._wait_timeout_s = float(config.get("wait_timeout_s", DEFAULT_WAIT_TIMEOUT_S) or DEFAULT_WAIT_TIMEOUT_S)

    def dispatch(self, payload: Dict[str, Any]) -> Optional[Awaitable]:
        """Enqueue the run. When waiting, return an awaitable the call site holds
        on until the run completes or the timeout elapses; otherwise fire-and-forget."""
        if not self._wait or self._schedule_run is None:
            self.fire(payload)
            return None
        return self._await_run(payload)

    async def _await_run(self, payload: Dict[str, Any]) -> None:
        future = self._schedule_run(self.automation_id, self.node_id, payload)
        if future is None:
            self.fire(payload)
            return
        try:
            # shield so the timeout stops US waiting without cancelling the run
            # itself - eviction should finish even once the generation proceeds.
            await asyncio.wait_for(asyncio.shield(future), timeout=self._wait_timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                f"[HOOK_BRIDGE] wait_for_completion timed out after {self._wait_timeout_s}s "
                f"for node {self.node_id} on '{self._hook_name}'; proceeding (run continues)"
            )
        except Exception:
            logger.error(f"[HOOK_BRIDGE] Error awaiting run for node {self.node_id}", exc_info=True)

    async def start(self) -> None:
        if not self._hook_name:
            logger.error(f"[HOOK_BRIDGE] trigger.hook_event node {self.node_id} missing 'hook_name' config")
            return
        self._bridge.subscribe(self._hook_name, self)

    async def stop(self) -> None:
        if not self._hook_name:
            return
        self._bridge.unsubscribe(self._hook_name, self.automation_id, self.node_id)
