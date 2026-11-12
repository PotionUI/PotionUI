import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.features.backends.backend_registry import BackendRegistry
from src.features.generation.queue import GenerationQueue, QueuedGeneration
from src.features.generation.queue_dispatcher import QueueDispatcher
from src.features.generation.routing.contracts import RoutingRequest
from src.features.generation.routing.registry import BUILTIN_RULES
from src.features.generation.routing.router import GenerationRouter
from src.features.generation.status_tracker import GenerationState, GenerationStatusTracker


def _backend(backend_id, priority=0):
    backend = Mock(backend_id=backend_id)
    backend.name = backend_id
    backend.config.priority = priority
    return backend


def _registry(backends, default_id=None):
    registry = object.__new__(BackendRegistry)
    registry.get_backends_for_engine = Mock(return_value=backends)
    registry._backends_cache = {b.backend_id: b for b in backends}
    registry.backend_config_store = Mock()
    registry.backend_config_store.get_default_backend.return_value = Mock(id=default_id) if default_id else None
    return registry


def _request():
    return RoutingRequest(engine="native", preset=Mock(requirements=[]))


@pytest.mark.asyncio
async def test_the_only_backend_gets_every_job():
    only = _backend("only")
    router = GenerationRouter(list(BUILTIN_RULES), backend_registry=_registry([only]))

    chosen = [(await router.route(_request())).chosen for _ in range(3)]

    assert chosen == [only, only, only]


@pytest.mark.asyncio
async def test_without_a_load_source_the_default_backend_wins_then_priority():
    low, high = _backend("low", priority=1), _backend("high", priority=9)
    by_priority = GenerationRouter(list(BUILTIN_RULES), backend_registry=_registry([high, low]))
    by_default = GenerationRouter(list(BUILTIN_RULES), backend_registry=_registry([high, low], default_id="low"))

    assert (await by_priority.route(_request())).chosen is high
    assert (await by_default.route(_request())).chosen is low


def _queue():
    started = []

    async def dispatch(item):
        started.append(item.generation_id)

    return GenerationQueue(dispatch=dispatch), started


@pytest.mark.asyncio
async def test_one_backend_runs_one_job_and_the_rest_wait_in_arrival_order():
    queue, started = _queue()
    for n in range(4):
        await queue.enqueue(QueuedGeneration(generation_id=f"g{n}", backend_id="b", user_id="u"))

    assert started == ["g0"]
    assert [queue.position(f"g{n}") for n in range(4)] == [None, 0, 1, 2]
    assert [i.generation_id for i in queue.pending_items()] == ["g1", "g2", "g3"]


@pytest.mark.asyncio
async def test_cancelling_a_waiting_job_moves_the_ones_behind_it_up():
    queue, _ = _queue()
    for n in range(4):
        await queue.enqueue(QueuedGeneration(generation_id=f"g{n}", backend_id="b", user_id="u"))

    assert await queue.cancel("g1") is True

    assert [queue.position(f"g{n}") for n in (2, 3)] == [0, 1]


@pytest.mark.asyncio
async def test_releasing_the_slot_starts_the_next_job_and_renumbers():
    queue, started = _queue()
    for n in range(3):
        await queue.enqueue(QueuedGeneration(generation_id=f"g{n}", backend_id="b", user_id="u"))

    await queue.release("b", "g0")

    assert started == ["g0", "g1"]
    assert queue.position("g2") == 0


@pytest.mark.asyncio
async def test_a_backend_with_more_slots_runs_that_many_at_once():
    started = []

    async def dispatch(item):
        started.append(item.generation_id)

    queue = GenerationQueue(dispatch=dispatch, capacity_for=lambda _: 2)
    for n in range(4):
        await queue.enqueue(QueuedGeneration(generation_id=f"g{n}", backend_id="b"))

    assert started == ["g0", "g1"]
    assert queue.position("g2") == 0


def _dispatcher():
    tracker = GenerationStatusTracker()
    seen = []

    async def listener(generation_id, message):
        seen.append(message)

    async def dispatch(*_args):
        return None

    dispatcher = QueueDispatcher(status_tracker=tracker, dispatch=dispatch)
    dispatcher.set_queue_listener(listener)
    return dispatcher, tracker, seen


@pytest.mark.asyncio
async def test_the_queue_update_message_keeps_its_original_fields():
    dispatcher, tracker, seen = _dispatcher()
    for gid in ("g0", "g1"):
        tracker.create(id=gid, backend_id="b", user_id="u", tab_id="t")
        await dispatcher.enqueue(QueuedGeneration(
            generation_id=gid, backend_id="b", user_id="u", tab_id="t",
            payload={"request": None, "backend": None, "db_generation": None, "output_callback": None},
        ))

    await dispatcher.publish_positions()

    last = seen[-1]
    assert {k: last[k] for k in ("type", "generation_id", "tab_id", "status", "queue_position")} == {
        "type": "queue_update", "generation_id": "g1", "tab_id": "t", "status": "pending", "queue_position": 0,
    }


@pytest.mark.asyncio
async def test_the_user_queue_snapshot_keeps_its_original_fields():
    dispatcher, tracker, _ = _dispatcher()
    for gid in ("g0", "g1"):
        tracker.create(id=gid, backend_id="b", user_id="u", tab_id="t")
        await dispatcher.enqueue(QueuedGeneration(
            generation_id=gid, backend_id="b", user_id="u", tab_id="t",
            payload={"request": None, "backend": None, "db_generation": None, "output_callback": None},
        ))

    snapshot = dispatcher.get_queue_snapshot("u")

    assert set(snapshot) == {"pending", "running"}
    assert snapshot["pending"][0]["generation_id"] == "g1"
    assert snapshot["pending"][0]["backend_id"] == "b"
    assert snapshot["pending"][0]["queue_position"] == 0
    assert {"generation_id", "backend_id", "tab_id", "enqueued_at"} <= set(snapshot["pending"][0])
    assert [r["generation_id"] for r in snapshot["running"]] == ["g0"]
    assert tracker.get("g0").state == GenerationState.RUNNING
