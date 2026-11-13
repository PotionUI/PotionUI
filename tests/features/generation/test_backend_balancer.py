import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.backends.backend_registry import BackendRegistry
from src.features.generation.backend_balancer import (
    QUEUED_BACKEND_GONE,
    RUNNING_BACKEND_GONE,
    BackendBalancer,
)
from src.features.generation.queue import QueuedGeneration
from src.features.generation.queue_dispatcher import QueueDispatcher
from src.features.generation.routing.contracts import RoutingRequest
from src.features.generation.routing.health import BackendHealthTracker
from src.features.generation.routing.load import BackendLoadLedger
from src.features.generation.routing.registry import BUILTIN_RULES
from src.features.generation.routing.router import GenerationRouter
from src.features.generation.status_tracker import GenerationState, GenerationStatusTracker


def _backend(backend_id):
    backend = Mock(backend_id=backend_id)
    backend.name = backend_id
    backend.config.priority = 1
    backend.config.effective_driver = "native"
    backend.execution_device = "this_host_gpu"
    backend.cancel_generation = AsyncMock(return_value=True)
    return backend


class World:
    def __init__(self, ids=("A", "B")):
        self.backends = {i: _backend(i) for i in ids}
        self.tracker = GenerationStatusTracker()
        self.health = BackendHealthTracker(failures_to_down=1)
        self.started = []
        self.failures = []

        registry = object.__new__(BackendRegistry)
        registry.get_backends_for_engine = Mock(side_effect=lambda engine: list(self.backends.values()))
        registry._backends_cache = self.backends
        registry.get_backend = Mock(side_effect=lambda bid: self.backends.get(bid))
        registry.backend_config_store = Mock()
        registry.backend_config_store.get_default_backend.return_value = None
        self.registry = registry

        async def dispatch(generation_id, request, backend, db_generation, output_callback):
            self.started.append((generation_id, backend.backend_id))

        self.dispatcher = QueueDispatcher(
            status_tracker=self.tracker, dispatch=dispatch,
            hold_reason=lambda b: None if self.health.is_healthy(b) else (self.health.reason(b) or "unhealthy"),
        )
        self.ledger = BackendLoadLedger(counts=self.dispatcher.queue.counts, capacity=lambda b: 1)
        self.router = GenerationRouter(list(BUILTIN_RULES), backend_registry=registry)
        self.router.bind_load(self.ledger)
        self.router.bind_health(self.health)

        async def record_failure(generation_id, failure):
            self.failures.append((generation_id, failure))
            await self.tracker.transition_async(generation_id, GenerationState.FAILED, failure)

        self.run_backends = {}
        self.balancer = BackendBalancer(
            dispatcher=self.dispatcher,
            get_router=lambda: self.router,
            status_tracker=self.tracker,
            health=self.health,
            get_backend=lambda bid: self.backends.get(bid),
            list_backends=lambda: dict(self.backends),
            fail_job=record_failure,
            run_backend_for=self.run_backends.get,
            resolve_form=lambda form, backend: {**form, "on": backend.backend_id},
            recovery_interval=0.01,
            sweep_interval=0.01,
        )

    async def submit(self, gid, backend_id):
        request = Mock(form_data={"x": 1})
        db = SimpleNamespace(form_data={"x": 1}, backend_id=backend_id)
        self.tracker.create(id=gid, backend_id=backend_id, user_id="u", tab_id="t")
        await self.dispatcher.enqueue(QueuedGeneration(
            generation_id=gid, backend_id=backend_id, user_id="u", tab_id="t",
            payload={
                "request": request, "backend": self.backends[backend_id], "db_generation": db,
                "output_callback": None,
                "routing": RoutingRequest(engine="native", preset=Mock(requirements=[], driver=None), form_data={"x": 1}),
            },
        ))
        return db


@pytest.fixture(autouse=True)
def _patches():
    with patch("src.features.generation.backend_balancer.generation_repo") as repo:
        yield repo


@pytest.mark.asyncio
async def test_queued_jobs_move_off_a_backend_that_went_down(_patches):
    world = World()
    await world.submit("a0", "A")
    dbs = [await world.submit(f"a{n}", "A") for n in (1, 2)]
    world.health.record("A", False, "connection refused")

    result = await world.balancer.rebalance("A", gone=False)

    assert result == {"moved": 2, "failed": 0}
    assert [world.tracker.get(g).backend_id for g in ("a1", "a2")] == ["B", "B"]
    assert [d.backend_id for d in dbs] == ["B", "B"]
    assert ("a1", "B") in world.started
    assert world.dispatcher.queue.position("a2") == 0
    _patches.update_backend.assert_any_call("a1", "B")


@pytest.mark.asyncio
async def test_a_moved_job_is_resolved_against_its_new_backend(_patches):
    world = World()
    await world.submit("a0", "A")
    await world.submit("a1", "A")
    item = world.dispatcher.queue.pending_for_backend("A")[0]
    world.health.record("A", False, "down")

    await world.balancer.rebalance("A", gone=False)

    assert item.payload["request"].form_data == {"x": 1, "on": "B"}
    assert item.payload["backend"] is world.backends["B"]


@pytest.mark.asyncio
async def test_moved_jobs_spread_over_the_remaining_backends():
    world = World(("A", "B", "C"))
    await world.submit("a0", "A")
    for n in range(1, 5):
        await world.submit(f"a{n}", "A")
    world.health.record("A", False, "down")

    await world.balancer.rebalance("A", gone=False)

    on = [world.tracker.get(f"a{n}").backend_id for n in range(1, 5)]
    assert sorted(on) == ["B", "B", "C", "C"]


@pytest.mark.asyncio
async def test_with_nowhere_to_go_a_down_backends_queue_waits_and_says_why():
    world = World(("A",))
    await world.submit("a0", "A")
    await world.submit("a1", "A")
    world.health.record("A", False, "connection refused")

    result = await world.balancer.rebalance("A", gone=False)
    info = world.dispatcher.queue_info("a1")

    assert result == {"moved": 0, "failed": 0}
    assert world.tracker.get("a1").state == GenerationState.PENDING
    assert info["waiting_reason"]
    assert info["admin_detail"]["waiting_detail"] == "connection refused"


@pytest.mark.asyncio
async def test_a_running_job_on_a_down_but_not_removed_backend_is_left_to_its_backend():
    world = World()
    await world.submit("a0", "A")
    world.health.record("A", False, "flaky probe")

    await world.balancer.rebalance("A", gone=False)

    assert world.tracker.get("a0").state == GenerationState.RUNNING
    assert world.failures == []


@pytest.mark.asyncio
async def test_removing_a_backend_fails_its_running_job_with_a_plain_reason_and_frees_the_slot():
    world = World()
    await world.submit("a0", "A")
    world.run_backends["a0"] = world.backends["A"]
    del world.backends["A"]

    result = await world.balancer.rebalance("A", gone=True)

    assert result["failed"] == 1
    assert world.tracker.get("a0").state == GenerationState.FAILED
    assert world.failures[0][1].message == RUNNING_BACKEND_GONE
    assert world.dispatcher.queue.running_generation_ids("A") == []


@pytest.mark.asyncio
async def test_removing_a_backend_moves_its_waiting_jobs_and_fails_its_running_one():
    world = World(("A", "B"))
    await world.submit("a0", "A")
    await world.submit("a1", "A")
    await world.submit("a2", "A")
    del world.backends["A"]

    result = await world.balancer.rebalance("A", gone=True)

    assert result == {"moved": 2, "failed": 1}
    assert world.tracker.get("a1").backend_id == "B"
    assert world.tracker.get("a0").state == GenerationState.FAILED


@pytest.mark.asyncio
async def test_a_queued_job_no_other_backend_can_run_fails_when_its_backend_is_removed():
    world = World(("A", "B"))
    await world.submit("a0", "A")
    await world.submit("a1", "A")
    del world.backends["A"]
    world.balancer._resolve_form = Mock(side_effect=RuntimeError("model missing"))
    result = await world.balancer.rebalance("A", gone=True)

    assert result["failed"] == 2
    assert world.tracker.get("a1").state == GenerationState.FAILED
    assert world.failures[0][1].message in (QUEUED_BACKEND_GONE, RUNNING_BACKEND_GONE)
    assert world.dispatcher.queue.position("a1") is None


@pytest.mark.asyncio
async def test_a_job_whose_model_is_missing_on_the_other_backend_stays_put_when_the_first_is_only_down():
    world = World()
    await world.submit("a0", "A")
    await world.submit("a1", "A")
    world.health.record("A", False, "down")
    world.balancer._resolve_form = Mock(side_effect=RuntimeError("model missing"))
    result = await world.balancer.rebalance("A", gone=False)

    assert result == {"moved": 0, "failed": 0}
    assert world.tracker.get("a1").backend_id == "A"


@pytest.mark.asyncio
async def test_a_job_is_not_moved_when_the_router_reserves_nothing_better():
    world = World(("A",))
    await world.submit("a0", "A")
    await world.submit("a1", "A")

    result = await world.balancer.rebalance("A", gone=False)

    assert result == {"moved": 0, "failed": 0}
    assert world.ledger.load("A").reserved == 0


@pytest.mark.asyncio
async def test_the_reservation_taken_while_moving_is_released():
    world = World()
    await world.submit("a0", "A")
    await world.submit("a1", "A")
    world.health.record("A", False, "down")

    await world.balancer.rebalance("A", gone=False)

    assert world.ledger.load("B").reserved == 0


@pytest.mark.asyncio
async def test_a_recovered_backend_starts_the_work_that_was_held_for_it():
    world = World(("A",))
    world.health.record("A", False, "down")
    await world.submit("a0", "A")
    assert world.started == []

    world.health.record("A", True)
    await world.balancer._after_health_change("A", True)

    assert world.started == [("a0", "A")]


@pytest.mark.asyncio
async def test_a_down_backend_is_probed_until_it_comes_back():
    world = World(("A",))
    probes = []

    async def health_check():
        probes.append(1)
        return {"status": "offline", "error": "refused"} if len(probes) < 3 else {"status": "healthy"}

    world.backends["A"].health_check = health_check
    world.health.add_listener(world.balancer.on_health_changed)
    await world.submit("a0", "A")
    world.health.record("A", False, "refused")

    for _ in range(100):
        await asyncio.sleep(0.01)
        if world.health.is_healthy("A") and world.started:
            break

    assert world.health.is_healthy("A")
    assert len(probes) == 3
    assert ("a0", "A") in world.started


@pytest.mark.asyncio
async def test_a_departing_backend_triggers_a_rebalance():
    world = World()
    await world.submit("a0", "A")
    await world.submit("a1", "A")
    del world.backends["A"]

    world.balancer.on_backend_departed("A")
    for _ in range(50):
        await asyncio.sleep(0.01)
        if world.tracker.get("a1").backend_id == "B" and world.tracker.get("a0").state == GenerationState.FAILED:
            break

    assert world.tracker.get("a1").backend_id == "B"
    assert world.tracker.get("a0").state == GenerationState.FAILED


@pytest.mark.asyncio
async def test_a_job_that_started_before_the_move_lands_is_left_untouched():
    world = World()
    await world.submit("a0", "A")
    db = await world.submit("a1", "A")
    item = world.dispatcher.queue.pending_for_backend("A")[0]
    request_before = item.payload["request"].form_data
    backend_before = item.payload["backend"]
    await world.dispatcher.queue.cancel("a1")

    moved = await world.balancer._move(item, "A")

    assert moved is False
    assert item.payload["request"].form_data == request_before
    assert item.payload["backend"] is backend_before
    assert db.backend_id == "A"
    assert world.ledger.load("B").reserved == 0


@pytest.mark.asyncio
async def test_the_payload_swap_happens_inside_the_queue_lock_with_the_reassignment():
    world = World()
    await world.submit("a0", "A")
    await world.submit("a1", "A")
    item = world.dispatcher.queue.pending_for_backend("A")[0]
    seen = []
    real = world.dispatcher.queue.reassign

    async def watching(gid, backend_id, apply=None):
        def traced():
            seen.append(("applied", item.backend_id, world.dispatcher.queue._lock.locked()))
            apply()
        return await real(gid, backend_id, traced)

    world.dispatcher.queue.reassign = watching
    world.health.record("A", False, "down")

    assert await world.balancer._move(item, "A") is True

    assert seen == [("applied", "B", True)]
    assert item.payload["backend"] is world.backends["B"]


@pytest.mark.asyncio
async def test_the_sweep_probes_remote_backends_that_have_work_and_skips_idle_and_local_ones():
    world = World(("A", "B", "C"))
    probed = []
    for bid, backend in world.backends.items():
        backend.execution_device = "remote"

        async def check(bid=bid):
            probed.append(bid)
            return {"status": "healthy"}

        backend.health_check = check
    world.backends["C"].execution_device = "this_host_gpu"
    await world.submit("a0", "A")
    await world.submit("c0", "C")

    assert await world.balancer.probe_busy_backends() == 1

    assert probed == ["A"]


@pytest.mark.asyncio
async def test_the_sweep_notices_a_backend_that_died_while_jobs_sat_queued_on_it():
    world = World(("A", "B"))
    world.health = BackendHealthTracker(failures_to_down=1)
    world.balancer._health = world.health
    world.health.add_listener(world.balancer.on_health_changed)
    world.router.bind_health(world.health)
    world.backends["A"].execution_device = "remote"

    async def dead():
        return {"status": "offline", "error": "refused"}

    world.backends["A"].health_check = dead
    await world.submit("a0", "A")
    await world.submit("a1", "A")

    await world.balancer.probe_busy_backends()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if world.tracker.get("a1").backend_id == "B":
            break

    assert not world.health.is_healthy("A")
    assert world.tracker.get("a1").backend_id == "B"


@pytest.mark.asyncio
async def test_the_sweep_loop_runs_until_stopped_and_never_overlaps_itself():
    world = World(("A",))
    running = []
    peak = []
    world.backends["A"].execution_device = "remote"

    async def slow():
        running.append(1)
        peak.append(len(running))
        await asyncio.sleep(0.05)
        running.pop()
        return {"status": "healthy"}

    world.backends["A"].health_check = slow
    world.health._ttl = 0
    await world.submit("a0", "A")

    world.balancer.start_probing()
    await asyncio.sleep(0.2)
    await world.balancer.stop_probing()

    assert peak and max(peak) == 1
    assert world.balancer._sweeper is None
    count = len(peak)
    await asyncio.sleep(0.05)
    assert len(peak) == count
