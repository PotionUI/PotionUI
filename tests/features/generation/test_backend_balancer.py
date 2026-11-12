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
        self.health = BackendHealthTracker()
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

        self.orchestrator = SimpleNamespace(
            _queue_dispatcher=self.dispatcher,
            router=self.router,
            status_tracker=self.tracker,
            model_locator=None,
            backend_registry=registry,
            backend_health=self.health,
            _run_backends={},
            _record_failure=record_failure,
        )
        self.balancer = BackendBalancer(self.orchestrator, recovery_interval=0.01)

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
    with patch("src.features.generation.backend_balancer.generation_repo") as repo, \
         patch("src.features.generation.backend_balancer.resolve_form_model_refs", side_effect=lambda form, backend, locator: {**form, "on": backend.backend_id}):
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
    world.orchestrator._run_backends["a0"] = world.backends["A"]
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
    with patch(
        "src.features.generation.backend_balancer.resolve_form_model_refs", side_effect=RuntimeError("model missing")
    ):
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
    with patch(
        "src.features.generation.backend_balancer.resolve_form_model_refs", side_effect=RuntimeError("model missing")
    ):
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
        if world.tracker.get("a1").backend_id == "B":
            break

    assert world.tracker.get("a1").backend_id == "B"
    assert world.tracker.get("a0").state == GenerationState.FAILED
