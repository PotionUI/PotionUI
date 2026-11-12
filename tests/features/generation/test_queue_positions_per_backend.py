import pytest

from src.features.generation.queue import GenerationQueue, QueuedGeneration
from src.features.generation.queue_dispatcher import WAITING_FOR_BACKEND, QueueDispatcher
from src.features.generation.scheduling import FAIR, SchedulingPolicy
from src.features.generation.status_tracker import GenerationStatusTracker


def _queue(**kwargs):
    started = []

    async def dispatch(item):
        started.append(item.generation_id)

    return GenerationQueue(dispatch=dispatch, **kwargs), started


async def _fill(queue, spec):
    for gid, backend_id in spec:
        await queue.enqueue(QueuedGeneration(generation_id=gid, backend_id=backend_id, user_id="u"))


@pytest.mark.asyncio
async def test_a_job_counts_only_the_waiting_jobs_of_its_own_backend():
    queue, started = _queue()
    await _fill(queue, [("a0", "A"), ("b0", "B"), ("a1", "A"), ("b1", "B"), ("a2", "A"), ("a3", "A")])

    assert started == ["a0", "b0"]
    assert [queue.position(g) for g in ("a1", "a2", "a3")] == [0, 1, 2]
    assert queue.position("b1") == 0


@pytest.mark.asyncio
async def test_cancelling_on_one_backend_leaves_the_other_backends_positions_alone():
    queue, _ = _queue()
    await _fill(queue, [("a0", "A"), ("b0", "B"), ("a1", "A"), ("b1", "B"), ("b2", "B"), ("a2", "A")])

    await queue.cancel("a1")

    assert queue.position("a2") == 0
    assert [queue.position("b1"), queue.position("b2")] == [0, 1]


@pytest.mark.asyncio
async def test_clearing_a_tab_renumbers_each_backend_separately():
    queue, _ = _queue()
    for gid, backend_id, tab in [("a0", "A", "t"), ("b0", "B", "t"), ("a1", "A", "x"), ("b1", "B", "t"), ("a2", "A", "t"), ("b2", "B", "x")]:
        await queue.enqueue(QueuedGeneration(generation_id=gid, backend_id=backend_id, user_id="u", tab_id=tab))

    await queue.clear_tab("u", "t")

    assert queue.position("a1") == 0
    assert queue.position("b2") == 0


@pytest.mark.asyncio
async def test_pending_with_positions_reports_each_backends_depth():
    queue, _ = _queue()
    await _fill(queue, [("a0", "A"), ("b0", "B"), ("a1", "A"), ("a2", "A"), ("b1", "B")])

    placed = [(i.generation_id, index, depth) for i, index, depth in queue.pending_with_positions()]

    assert placed == [("a1", 0, 2), ("a2", 1, 2), ("b1", 0, 1)]


@pytest.mark.asyncio
async def test_releasing_one_backend_renumbers_only_that_backend():
    queue, started = _queue()
    await _fill(queue, [("a0", "A"), ("b0", "B"), ("a1", "A"), ("a2", "A"), ("b1", "B")])

    await queue.release("A", "a0")

    assert started[-1] == "a1"
    assert queue.position("a2") == 0
    assert queue.position("b1") == 0


@pytest.mark.asyncio
async def test_a_fair_backend_reports_the_order_it_will_actually_run_in():
    queue, _ = _queue(policy_for=lambda b: SchedulingPolicy(name=FAIR) if b == "A" else SchedulingPolicy())
    for gid, backend_id, user in [("a0", "A", "u1"), ("b0", "B", "u1"), ("a1", "A", "u1"), ("a2", "A", "u1"), ("a3", "A", "u2")]:
        await queue.enqueue(QueuedGeneration(generation_id=gid, backend_id=backend_id, user_id=user))

    assert queue.position("a3") < queue.position("a2")
    assert sorted(queue.position(g) for g in ("a1", "a2", "a3")) == [0, 1, 2]


@pytest.mark.asyncio
async def test_a_counts_view_gives_queued_and_running_per_backend():
    queue, _ = _queue()
    await _fill(queue, [("a0", "A"), ("a1", "A"), ("a2", "A"), ("b0", "B")])

    assert queue.counts("A") == (2, 1)
    assert queue.counts("B") == (0, 1)
    assert queue.counts("C") == (0, 0)


@pytest.mark.asyncio
async def test_reassigning_moves_a_waiting_job_to_the_new_backends_queue_in_arrival_order():
    queue, started = _queue()
    await _fill(queue, [("a0", "A"), ("a1", "A"), ("a2", "A"), ("b0", "B"), ("b1", "B")])

    assert await queue.reassign("a1", "B") == "A"

    assert queue.position("a1") == 0
    assert queue.position("a2") == 0
    assert queue.position("b1") == 1
    assert started == ["a0", "b0"]


@pytest.mark.asyncio
async def test_reassigning_a_job_that_is_not_waiting_does_nothing():
    queue, _ = _queue()
    await _fill(queue, [("a0", "A")])

    assert await queue.reassign("a0", "B") is None


@pytest.mark.asyncio
async def test_a_held_backend_keeps_its_work_waiting_while_others_run():
    held = {"A"}
    queue, started = _queue(is_dispatchable=lambda b: b not in held)
    await _fill(queue, [("a0", "A"), ("b0", "B"), ("a1", "A")])

    assert started == ["b0"]
    assert queue.position("a0") == 0

    held.clear()
    await queue.wake()

    assert started == ["b0", "a0"]


def _dispatcher(hold=None):
    tracker = GenerationStatusTracker()
    seen = []

    async def listener(generation_id, message):
        seen.append(message)

    async def dispatch(*_args):
        return None

    dispatcher = QueueDispatcher(status_tracker=tracker, dispatch=dispatch, hold_reason=hold)
    dispatcher.set_queue_listener(listener)
    return dispatcher, tracker, seen


class _Named:
    def __init__(self, name):
        self.name = name


async def _enqueue(dispatcher, tracker, gid, backend_id, user="u"):
    tracker.create(id=gid, backend_id=backend_id, user_id=user, tab_id="t")
    await dispatcher.enqueue(QueuedGeneration(
        generation_id=gid, backend_id=backend_id, user_id=user, tab_id="t",
        payload={"request": None, "backend": _Named(f"{backend_id}-name"), "db_generation": None, "output_callback": None},
    ))


@pytest.mark.asyncio
async def test_the_queue_update_carries_the_old_fields_and_the_new_ones():
    dispatcher, tracker, seen = _dispatcher()
    for gid, backend_id in [("a0", "A"), ("b0", "B"), ("a1", "A"), ("a2", "A")]:
        await _enqueue(dispatcher, tracker, gid, backend_id)

    seen.clear()
    await dispatcher.publish_positions()
    by_id = {m["generation_id"]: m for m in seen}

    assert by_id["a2"]["queue_position"] == 1
    assert by_id["a2"]["queue_depth"] == 2
    assert by_id["a2"]["waiting_reason"] is None
    assert by_id["a2"]["admin_detail"] == {"backend_id": "A", "backend_name": "A-name"}
    assert {"type", "generation_id", "tab_id", "status"} <= set(by_id["a2"])
    assert by_id["a2"]["status"] == "pending"


@pytest.mark.asyncio
async def test_a_job_waiting_on_a_down_backend_says_so_without_naming_the_server():
    down = set()
    dispatcher, tracker, seen = _dispatcher(hold=lambda b: "connection refused" if b in down else None)
    await _enqueue(dispatcher, tracker, "a0", "A")
    down.add("A")
    await _enqueue(dispatcher, tracker, "a1", "A")

    seen.clear()
    await dispatcher.publish_positions()
    message = seen[-1]

    assert message["waiting_reason"] == WAITING_FOR_BACKEND
    assert "connection refused" not in {k: v for k, v in message.items() if k != "admin_detail"}.values()
    assert message["admin_detail"]["waiting_detail"] == "connection refused"


@pytest.mark.asyncio
async def test_the_http_queue_info_matches_the_pushed_position():
    dispatcher, tracker, seen = _dispatcher()
    for gid, backend_id in [("a0", "A"), ("a1", "A"), ("b0", "B"), ("b1", "B"), ("b2", "B")]:
        await _enqueue(dispatcher, tracker, gid, backend_id)

    info = dispatcher.queue_info("b2")

    assert info["queue_position"] == 1
    assert info["queue_depth"] == 2
    assert info["admin_detail"]["backend_name"] == "B-name"
    assert dispatcher.queue_info("a0") is None


@pytest.mark.asyncio
async def test_the_user_snapshot_and_admin_snapshot_use_the_per_backend_position():
    dispatcher, tracker, _ = _dispatcher()
    for gid, backend_id in [("a0", "A"), ("b0", "B"), ("a1", "A"), ("b1", "B")]:
        await _enqueue(dispatcher, tracker, gid, backend_id)

    user_view = dispatcher.get_queue_snapshot("u")
    admin_view = dispatcher.get_all_queue_snapshot()

    assert [(p["generation_id"], p["queue_position"]) for p in user_view["pending"]] == [("a1", 0), ("b1", 0)]
    assert [(p["generation_id"], p["queue_position"], p["backend_name"]) for p in admin_view["pending"]] == [
        ("a1", 0, "A-name"), ("b1", 0, "B-name"),
    ]
    assert "backend_name" not in user_view["pending"][0]
