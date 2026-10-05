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
