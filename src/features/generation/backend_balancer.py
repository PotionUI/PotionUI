import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from src.features.cloud.contracts import CLOUD_ENGINE
from src.features.generation.error_classification import classification_for_code
from src.features.generation.failure import GenerationFailure
from src.features.generation.repository import generation_repo
from src.features.generation.routing.health import PROBE_TTL_SECONDS, probes_remotely

logger = logging.getLogger(__name__)

RECOVERY_INTERVAL_SECONDS = PROBE_TTL_SECONDS
PROBE_SWEEP_SECONDS = PROBE_TTL_SECONDS

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
    def __init__(
        self,
        dispatcher: Any,
        get_router: Callable[[], Any],
        status_tracker: Any,
        health: Any,
        get_backend: Callable[[str], Any],
        list_backends: Callable[[], Dict[str, Any]],
        fail_job: Callable[[str, GenerationFailure], Awaitable[None]],
        run_backend_for: Callable[[str], Any],
        resolve_form: Callable[[Dict[str, Any], Any], Dict[str, Any]],
        recovery_interval: float = RECOVERY_INTERVAL_SECONDS,
        sweep_interval: float = PROBE_SWEEP_SECONDS,
    ):
        self._dispatcher = dispatcher
        self._get_router = get_router
        self._status_tracker = status_tracker
        self._health = health
        self._get_backend = get_backend
        self._list_backends = list_backends
        self._fail_job = fail_job
        self._run_backend_for = run_backend_for
        self._resolve_form = resolve_form
        self._recovery_interval = recovery_interval
        self._sweep_interval = sweep_interval
        self._tasks: set = set()
        self._watching: set = set()
        self._sweeper: Optional[asyncio.Task] = None

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start_probing(self) -> None:
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.ensure_future(self._sweep_forever())

    async def stop_probing(self) -> None:
        sweeper, self._sweeper = self._sweeper, None
        if sweeper is not None:
            sweeper.cancel()
            await asyncio.gather(sweeper, return_exceptions=True)

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_interval)
            try:
                await self.probe_busy_backends()
            except Exception:
                logger.exception("[BALANCER] probe sweep failed")

    async def probe_busy_backends(self) -> int:
        queue = self._dispatcher.queue
        due = [
            backend for backend_id, backend in self._list_backends().items()
            if probes_remotely(backend)
            and queue.counts(backend_id) != (0, 0)
            and self._health.needs_probe(backend_id)
        ]
        await asyncio.gather(*(self._health.probe(backend) for backend in due), return_exceptions=True)
        return len(due)

    def on_health_changed(self, backend_id: str, healthy: bool) -> None:
        self._spawn(self._after_health_change(backend_id, healthy))

    def on_backend_departed(self, backend_id: str) -> None:
        self._health.forget(backend_id)
        self._spawn(self.rebalance(backend_id, gone=self._get_backend(backend_id) is None))

    async def _after_health_change(self, backend_id: str, healthy: bool) -> None:
        if healthy:
            await self._dispatcher.wake()
            await self._dispatcher.publish_positions()
            return
        await self.rebalance(backend_id, gone=False)
        self._watch_recovery(backend_id)

    def _watch_recovery(self, backend_id: str) -> None:
        if backend_id in self._watching:
            return
        self._watching.add(backend_id)
        self._spawn(self._recover(backend_id))

    async def _recover(self, backend_id: str) -> None:
        try:
            while not self._health.is_healthy(backend_id):
                await asyncio.sleep(self._recovery_interval)
                backend = self._get_backend(backend_id)
                if backend is None:
                    return
                await self._health.probe(backend)
                if not self._health.is_healthy(backend_id):
                    await self.rebalance(backend_id, gone=False)
        finally:
            self._watching.discard(backend_id)

    async def rebalance(self, backend_id: str, gone: bool) -> Dict[str, int]:
        dispatcher = self._dispatcher
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
        router = self._get_router()
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
                form_data = self._resolve_form(db_generation.form_data or {}, target)
            except Exception:
                logger.info(f"[BALANCER] {item.generation_id} cannot run on {target.backend_id}", exc_info=True)
                return False

            def swap() -> None:
                payload["request"].form_data = form_data
                payload["backend"] = target

            if await self._dispatcher.queue.reassign(item.generation_id, target.backend_id, swap) is None:
                return False
            self._status_tracker.set_backend(item.generation_id, target.backend_id)
            db_generation.backend_id = target.backend_id
            await asyncio.to_thread(generation_repo.update_backend, item.generation_id, target.backend_id)
            return True
        finally:
            if decision.reserved:
                router.release(target.backend_id)

    async def _fail_queued(self, item) -> None:
        if await self._dispatcher.cancel(item.generation_id):
            await self._fail_job(item.generation_id, _plain_failure(QUEUED_BACKEND_GONE))
            self._dispatcher.prune_finished()

    async def _fail_running(self, generation_id: str, backend_id: str) -> None:
        await self._fail_job(generation_id, _plain_failure(RUNNING_BACKEND_GONE))
        backend = self._run_backend_for(generation_id)
        if backend is not None:
            try:
                await backend.cancel_generation(generation_id)
            except Exception:
                logger.debug(f"[BALANCER] cancel of {generation_id} on a departed backend failed", exc_info=True)
        await self._dispatcher.release(backend_id, generation_id)
        self._dispatcher.prune_finished()
