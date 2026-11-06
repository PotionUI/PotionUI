import threading

from src.features.organize.worker import HOOK_EVENTS, OrganizeWorker
from src.platform.plugins.hooks import HookChain


def test_subscribing_registers_one_handler_per_hook_and_never_runs_inline():
    seen = []
    worker = OrganizeWorker(lambda kind, payload: seen.append((kind, payload)))
    chain = HookChain()
    worker.subscribe(chain)

    chain.execute("generation.after_complete", initial_data={"generation_id": "g1", "status": "completed", "extra": 1})

    assert seen == []
    assert worker.pending() == 1
    worker.drain()
    assert seen == [("generation_completed", {"generation_id": "g1", "status": "completed"})]


def test_unsubscribe_removes_every_handler():
    worker = OrganizeWorker(lambda kind, payload: None)
    chain = HookChain()
    worker.subscribe(chain)
    worker.unsubscribe(chain)

    for hook_name in HOOK_EVENTS:
        chain.execute(hook_name, initial_data={})

    assert worker.pending() == 0


def test_a_full_queue_drops_events_instead_of_blocking():
    worker = OrganizeWorker(lambda kind, payload: None, maxsize=1)

    assert worker.enqueue("upload_created", {}) is True
    assert worker.enqueue("upload_created", {}) is False


def test_a_failing_handler_does_not_stop_the_queue():
    seen = []

    def handle(kind, payload):
        if payload.get("boom"):
            raise RuntimeError("boom")
        seen.append(kind)

    worker = OrganizeWorker(handle)
    worker.enqueue("upload_created", {"boom": True})
    worker.enqueue("model_added", {})

    assert worker.drain() == 2
    assert seen == ["model_added"]


def test_the_background_thread_handles_events_and_stops():
    done = threading.Event()
    pruned = threading.Event()
    worker = OrganizeWorker(lambda kind, payload: done.set(), prune=pruned.set)

    worker.start()
    worker.enqueue("model_added", {"model_id": "m"})

    assert done.wait(5)
    assert pruned.wait(5)
    worker.stop()
    assert worker._thread is None


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


def sweeping_worker(monkeypatch, busy=None, sweep=None):
    from src.features.organize import worker as worker_module

    clock = FakeClock()
    monkeypatch.setattr(worker_module.time, "monotonic", clock)
    calls = []
    built = OrganizeWorker(
        lambda kind, payload: None,
        sweep=sweep or (lambda: calls.append(clock.now)),
        busy=busy,
    )
    return built, clock, calls, worker_module.SWEEP_EVERY_S


def test_no_sweep_happens_before_the_interval(monkeypatch):
    worker, clock, calls, every = sweeping_worker(monkeypatch)

    clock.now += every - 1
    worker._maybe_sweep()

    assert calls == []


def test_exactly_one_sweep_happens_when_due_and_the_next_waits_a_full_interval(monkeypatch):
    worker, clock, calls, every = sweeping_worker(monkeypatch)

    clock.now += every
    worker._maybe_sweep()
    worker._maybe_sweep()
    clock.now += every - 1
    worker._maybe_sweep()
    clock.now += 1
    worker._maybe_sweep()

    assert len(calls) == 2


def test_no_sweep_while_a_generation_is_running_and_it_runs_right_after(monkeypatch):
    state = {"busy": True}
    worker, clock, calls, every = sweeping_worker(monkeypatch, busy=lambda: state["busy"])

    clock.now += every * 3
    worker._maybe_sweep()
    assert calls == []

    state["busy"] = False
    worker._maybe_sweep()

    assert len(calls) == 1


def test_a_failing_sweep_is_logged_and_the_next_one_still_runs(monkeypatch, caplog):
    attempts = []

    def sweep():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("provider exploded")

    worker, clock, _calls, every = sweeping_worker(monkeypatch, sweep=sweep)

    clock.now += every
    worker._maybe_sweep()
    clock.now += every
    worker._maybe_sweep()

    assert len(attempts) == 2
    assert "retry pass failed" in caplog.text


def test_the_loop_survives_a_failing_sweep_and_keeps_handling_events(monkeypatch):
    seen = threading.Event()
    clock = FakeClock()
    from src.features.organize import worker as worker_module

    monkeypatch.setattr(worker_module.time, "monotonic", clock)

    def boom():
        raise RuntimeError("provider exploded")

    worker = OrganizeWorker(lambda kind, payload: seen.set(), sweep=boom)
    clock.now += worker_module.SWEEP_EVERY_S
    worker.start()
    try:
        worker.enqueue("model_added", {})
        assert seen.wait(5)
    finally:
        worker.stop()
