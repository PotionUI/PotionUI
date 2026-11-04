import asyncio
import threading
from types import SimpleNamespace

from src.features.elixirs.runtime import SUBSCRIBER_ID, ElixirRuntime
from src.features.generation.hooks import GENERATION_HOOKS
from src.features.sessions.hooks import SESSION_HOOKS
from src.platform.plugins.hooks import HookChain


class Recorder:
    def __init__(self):
        self.woken = []
        self.attached = None
        self.runs = 0

    def on_generation_finished(self, generation_id):
        self.woken.append(generation_id)

    def attach(self, loop):
        self.attached = loop

    async def run_forever(self):
        self.runs += 1
        await asyncio.Event().wait()

    def writer_tasks(self):
        return []

    boot_id = "boot-x"


def test_the_runtime_subscribes_to_completion_delete_and_session_hooks():
    chain = HookChain()
    runtime = ElixirRuntime(Recorder(), follows=None)

    runtime.subscribe(chain)

    wired = {
        GENERATION_HOOKS.after_complete,
        GENERATION_HOOKS.before_delete,
        GENERATION_HOOKS.before_bulk_delete,
        SESSION_HOOKS.after_update,
        SESSION_HOOKS.after_delete,
    }
    for hook_name in wired:
        assert [owner for owner, _ in chain._handlers[hook_name]] == [SUBSCRIBER_ID]
    runtime.unsubscribe(chain)
    assert all(not chain._handlers.get(hook_name) for hook_name in wired)


def test_a_finished_generation_only_wakes_the_coordinator():
    recorder = Recorder()
    runtime = ElixirRuntime(recorder, follows=None)
    context = SimpleNamespace(data={"generation_id": "g7", "status": "completed"})

    assert runtime.on_generation_complete(context) is context
    runtime.on_generation_complete({"generation_id": "g8"})

    assert recorder.woken == ["g7", "g8"]


def test_start_runs_the_coordinator_on_the_app_loop_once_and_stop_ends_it():
    recorder = Recorder()
    runtime = ElixirRuntime(recorder, follows=None)

    async def scenario():
        loop = asyncio.get_running_loop()
        assert runtime.start() is True
        assert runtime.start() is False
        await asyncio.sleep(0)
        assert recorder.attached is loop and runtime.running
        await runtime.stop()
        assert not runtime.running
        await runtime.stop()
        assert runtime.start() is True
        await asyncio.sleep(0)
        await runtime.stop()

    asyncio.run(scenario())

    assert recorder.runs == 2


def test_stop_also_stops_prompt_writers_still_running():
    recorder = Recorder()
    writer_cancelled = []

    async def scenario():
        writer = asyncio.create_task(asyncio.Event().wait())
        recorder.writer_tasks = lambda: [writer]
        runtime = ElixirRuntime(recorder, follows=None)
        runtime.start()
        await asyncio.sleep(0)
        await runtime.stop()
        writer_cancelled.append(writer.cancelled())

    asyncio.run(scenario())

    assert writer_cancelled == [True]


def idle_coordinator(port):
    from src.features.elixirs.coordinator import Coordinator
    from src.features.elixirs.run_repository import RunStore

    return Coordinator(store=RunStore(), port=port, boot_id="b")


async def stop(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_a_finished_generation_wakes_the_coordinator_from_another_thread(scratch_db, port):
    coordinator = idle_coordinator(port)
    pumps = []

    async def scenario():
        started = asyncio.Event()
        woken = asyncio.Event()

        async def fake_pump():
            pumps.append(1)
            (started if len(pumps) == 1 else woken).set()

        coordinator.pump = fake_pump
        task = asyncio.create_task(coordinator.run_forever(poll_seconds=30))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            waker = threading.Thread(target=coordinator.on_generation_finished, args=("g1",))
            waker.start()
            waker.join()
            await asyncio.wait_for(woken.wait(), timeout=2)
        finally:
            await stop(task)

    asyncio.run(scenario())

    assert len(pumps) == 2


def test_without_a_wake_the_coordinator_still_polls(scratch_db, port):
    coordinator = idle_coordinator(port)
    pumps = []

    async def scenario():
        polled = asyncio.Event()

        async def fake_pump():
            pumps.append(1)
            if len(pumps) == 3:
                polled.set()

        coordinator.pump = fake_pump
        task = asyncio.create_task(coordinator.run_forever(poll_seconds=0.01))
        try:
            await asyncio.wait_for(polled.wait(), timeout=2)
        finally:
            await stop(task)

    asyncio.run(scenario())

    assert len(pumps) >= 3


def test_a_failing_pass_is_logged_and_the_loop_keeps_going(scratch_db, port, caplog):
    coordinator = idle_coordinator(port)
    pumps = []

    async def scenario():
        recovered = asyncio.Event()

        async def flaky_pump():
            pumps.append(1)
            if len(pumps) == 1:
                raise RuntimeError("database is locked")
            recovered.set()

        coordinator.pump = flaky_pump
        task = asyncio.create_task(coordinator.run_forever(poll_seconds=0.01))
        try:
            await asyncio.wait_for(recovered.wait(), timeout=2)
        finally:
            await stop(task)

    asyncio.run(scenario())

    assert len(pumps) >= 2
    assert "Elixir coordinator pass failed" in caplog.text
    assert "database is locked" in caplog.text


def test_recovery_is_retried_until_it_succeeds_once(scratch_db, port, caplog):
    coordinator = idle_coordinator(port)
    recoveries = []
    pumps = []

    async def scenario():
        settled = asyncio.Event()
        real_recover = coordinator.recover

        async def flaky_recover():
            recoveries.append(1)
            if len(recoveries) == 1:
                raise RuntimeError("database is locked")
            await real_recover()

        async def fake_pump():
            pumps.append(len(recoveries))
            if len(pumps) == 3:
                settled.set()

        coordinator.recover = flaky_recover
        coordinator.pump = fake_pump
        task = asyncio.create_task(coordinator.run_forever(poll_seconds=0.01))
        try:
            await asyncio.wait_for(settled.wait(), timeout=2)
        finally:
            await stop(task)

    asyncio.run(scenario())

    assert recoveries == [1, 1]
    assert pumps[:3] == [2, 2, 2]
    assert "Elixir coordinator pass failed" in caplog.text
