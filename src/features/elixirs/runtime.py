import asyncio
import logging
from typing import Any, Optional

from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.follow import SessionFollows
from src.features.generation.hooks import GENERATION_HOOKS
from src.features.sessions.hooks import SESSION_HOOKS

logger = logging.getLogger(__name__)

SUBSCRIBER_ID = "elixirs_engine"
HOLD_REASON = "An elixir brew still needs files from this generation. Stop or finish that brew first."


def hook_data(context: Any) -> dict:
    data = getattr(context, "data", None)
    if isinstance(data, dict):
        return data
    return context if isinstance(context, dict) else {}


class ElixirRuntime:
    def __init__(self, coordinator: Coordinator, follows: SessionFollows) -> None:
        self.coordinator = coordinator
        self.follows = follows
        self._task: Optional[asyncio.Task] = None

    def hooks(self) -> dict:
        return {
            GENERATION_HOOKS.after_complete: self.on_generation_complete,
            GENERATION_HOOKS.before_delete: self.on_before_delete,
            GENERATION_HOOKS.before_bulk_delete: self.on_before_bulk_delete,
            SESSION_HOOKS.after_update: self.on_session_updated,
            SESSION_HOOKS.after_delete: self.on_session_deleted,
        }

    def subscribe(self, hook_chain: Any) -> None:
        for hook_name, handler in self.hooks().items():
            hook_chain.register(hook_name, SUBSCRIBER_ID, handler)

    def unsubscribe(self, hook_chain: Any) -> None:
        for hook_name in self.hooks():
            hook_chain.unregister(hook_name, SUBSCRIBER_ID)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
        if self.running:
            return False
        loop = loop or asyncio.get_running_loop()
        self.coordinator.attach(loop)
        self._task = loop.create_task(self.coordinator.run_forever())
        logger.info("[ELIXIRS] Brew coordinator started (boot %s)", self.coordinator.boot_id)
        return True

    async def stop(self) -> None:
        task, self._task = self._task, None
        tasks = [item for item in [task, *self.coordinator.writer_tasks()] if item is not None and not item.done()]
        for item in tasks:
            item.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def on_generation_complete(self, context):
        self.coordinator.on_generation_finished(hook_data(context).get("generation_id"))
        return context

    def on_before_delete(self, context):
        data = hook_data(context)
        if self.coordinator.store.generations_in_use([data.get("generation_id")]):
            data["blocked"] = True
            data["block_reason"] = HOLD_REASON
        return context

    def on_before_bulk_delete(self, context):
        data = hook_data(context)
        held = self.coordinator.store.generations_in_use(data.get("generation_ids") or [])
        if held:
            data["blocked"] = True
            data["block_reason"] = f"{HOLD_REASON} ({len(held)} of the selected generations)"
        return context

    def on_session_updated(self, context):
        data = hook_data(context)
        try:
            self.follows.session_written(data.get("user_id"), data.get("session_id"), data.get("version"))
        except Exception:
            logger.exception("[ELIXIRS] Could not check the elixirs that follow session %s", data.get("session_id"))
        return context

    def on_session_deleted(self, context):
        data = hook_data(context)
        try:
            self.follows.session_deleted(data.get("user_id"), data.get("session_id"))
        except Exception:
            logger.exception("[ELIXIRS] Could not mark the elixirs that follow deleted session %s", data.get("session_id"))
        return context
