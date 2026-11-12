import asyncio
import logging
from typing import Any, Dict, Optional

from src.features.cloud.contracts import CLOUD_ENGINE
from src.features.generation.error_classification import classification_for_code
from src.features.generation.failure import GenerationFailure
from src.features.generation.repository import generation_repo
from src.features.generation.routing.health import PROBE_TTL_SECONDS
from src.features.models.form_refs import resolve_form_model_refs

logger = logging.getLogger(__name__)

RECOVERY_INTERVAL_SECONDS = PROBE_TTL_SECONDS

QUEUED_BACKEND_GONE = "The server this job was waiting for was taken offline and no other server can run it."
RUNNING_BACKEND_GONE = "The server running this job was taken offline."


def _plain_failure(message: str) -> GenerationFailure:
    classification = classification_for_code("backend_unreachable")
    return GenerationFailure(
        error_code=classification.category,
        message=message,
        hints=tuple(classification.suggestions),
    )


class BackendBalancer:
    def __init__(self, orchestrator: Any, recovery_interval: float = RECOVERY_INTERVAL_SECONDS):
        self._orchestrator = orchestrator
        self._recovery_interval = recovery_interval
        self._tasks: set = set()
        self._watching: set = set()

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def on_health_changed(self, backend_id: str, healthy: bool) -> None:
        self._spawn(self._after_health_change(backend_id, healthy))

    def on_backend_departed(self, backend_id: str) -> None:
        orchestrator = self._orchestrator
        orchestrator.backend_health.forget(backend_id)
        self._spawn(self.rebalance(backend_id, gone=orchestrator.backend_registry.get_backend(backend_id) is None))

    async def _after_health_change(self, backend_id: str, healthy: bool) -> None:
        dispatcher = self._orchestrator._queue_dispatcher
        if healthy:
            await dispatcher.wake()
            await dispatcher.publish_positions()
            return
        await self.rebalance(backend_id, gone=False)
        self._watch_recovery(backend_id)

    def _watch_recovery(self, backend_id: str) -> None:
        if backend_id in self._watching:
            return
        self._watching.add(backend_id)
        self._spawn(self._recover(backend_id))

    async def _recover(self, backend_id: str) -> None:
        orchestrator = self._orchestrator
        try:
            while not orchestrator.backend_health.is_healthy(backend_id):
                await asyncio.sleep(self._recovery_interval)
                backend = orchestrator.backend_registry.get_backend(backend_id)
                if backend is None:
                    return
                await orchestrator.backend_health.probe(backend)
                if not orchestrator.backend_health.is_healthy(backend_id):
                    await self.rebalance(backend_id, gone=False)
        finally:
            self._watching.discard(backend_id)

    async def rebalance(self, backend_id: str, gone: bool) -> Dict[str, int]:
        orchestrator = self._orchestrator
        dispatcher = orchestrator._queue_dispatcher
        moved = failed = 0
        for item in dispatcher.queue.pending_for_backend(backend_id):
            if await self._move(item, backend_id):
                moved += 1
            elif gone:
                await self._fail_queued(item)
                failed += 1
        if gone:
            for generation_id in dispatcher.queue.running_generation_ids(backend_id):
                await self._fail_running(generation_id, backend_id)
                failed += 1
        await dispatcher.publish_positions()
        await dispatcher.wake()
        if moved or failed:
            logger.info(f"[BALANCER] backend {backend_id}: moved {moved} queued job(s), failed {failed}")
        return {"moved": moved, "failed": failed}

    async def _move(self, item, old_backend_id: str) -> bool:
        orchestrator = self._orchestrator
        router = orchestrator.router
        payload = item.payload if isinstance(item.payload, dict) else {}
        routing = payload.get("routing")
        if router is None or routing is None or routing.engine == CLOUD_ENGINE:
            return False
        try:
            decision = await router.route(routing, reserve=True)
        except Exception:
            return False
        target = decision.chosen
        try:
            if target.backend_id == old_backend_id:
                return False
            db_generation = payload["db_generation"]
            try:
                payload["request"].form_data = resolve_form_model_refs(
                    db_generation.form_data or {}, target, orchestrator.model_locator
                )
            except Exception:
                logger.info(f"[BALANCER] {item.generation_id} cannot run on {target.backend_id}", exc_info=True)
                return False
            payload["backend"] = target
            if await orchestrator._queue_dispatcher.queue.reassign(item.generation_id, target.backend_id) is None:
                return False
            orchestrator.status_tracker.set_backend(item.generation_id, target.backend_id)
            db_generation.backend_id = target.backend_id
            await asyncio.to_thread(generation_repo.update_backend, item.generation_id, target.backend_id)
            return True
        finally:
            if decision.reserved:
                router.release(target.backend_id)

    async def _fail_queued(self, item) -> None:
        orchestrator = self._orchestrator
        if await orchestrator._queue_dispatcher.cancel(item.generation_id):
            await orchestrator._record_failure(item.generation_id, _plain_failure(QUEUED_BACKEND_GONE))
            orchestrator._queue_dispatcher.prune_finished()

    async def _fail_running(self, generation_id: str, backend_id: str) -> None:
        orchestrator = self._orchestrator
        await orchestrator._record_failure(generation_id, _plain_failure(RUNNING_BACKEND_GONE))
        backend: Optional[Any] = orchestrator._run_backends.get(generation_id)
        if backend is not None:
            try:
                await backend.cancel_generation(generation_id)
            except Exception:
                logger.debug(f"[BALANCER] cancel of {generation_id} on a departed backend failed", exc_info=True)
        await orchestrator._queue_dispatcher.release(backend_id, generation_id)
        orchestrator._queue_dispatcher.prune_finished()
