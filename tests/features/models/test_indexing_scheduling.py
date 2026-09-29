import threading
from unittest.mock import AsyncMock, MagicMock

from src.features.models.indexing_coordinator import ModelIndexingCoordinator


class OpenPlugins:
    def execute_hook(self, hook, initial_data):
        return MagicMock(data={}), False


class ThreadSpawner:
    def __init__(self):
        self.threads = []

    def __call__(self, target):
        thread = threading.Thread(target=target, daemon=True)
        self.threads.append(thread)
        thread.start()

    def join(self):
        for thread in list(self.threads):
            thread.join(timeout=5)
            assert not thread.is_alive()


class CountingScanner:
    def __init__(self, hold_first_run=False):
        self.calls = 0
        self.first_run_entered = threading.Event()
        self.release_first_run = threading.Event()
        self.first_run_was_cancelled = False
        self._hold_first_run = hold_first_run
        self._lock = threading.Lock()
        self.resolver = MagicMock()
        self.resolver.online_root_ids.return_value = []
        self.resolver.roots.return_value = []

    def set_progress_callback(self, callback):
        return None

    def index_models(self, max_workers=4, cancel_check=None):
        with self._lock:
            self.calls += 1
            first = self.calls == 1
        if first and self._hold_first_run:
            self.first_run_entered.set()
            while not self.release_first_run.wait(timeout=0.005):
                if cancel_check is not None and cancel_check():
                    self.first_run_was_cancelled = True
                    return {"indexed": 0, "cancelled": True}
        return {"indexed": 0, "cancelled": False}


def build_coordinator(scanner, spawner):
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    return ModelIndexingCoordinator(
        model_repository=MagicMock(),
        plugin_registry=OpenPlugins(),
        scanner=scanner,
        native_availability_projector=reconciler,
        spawn=spawner,
    )


def test_cancel_and_restart_on_an_idle_coordinator_runs_the_scan_to_a_terminal_state():
    scanner = CountingScanner()
    spawner = ThreadSpawner()
    coordinator = build_coordinator(scanner, spawner)

    coordinator.cancel_and_restart(trigger="admin_reindex")
    spawner.join()

    status = coordinator.status()
    assert status["state"] == "done"
    assert status["trigger"] == "admin_reindex"
    assert scanner.calls == 1


def test_start_indexing_runs_the_scan_without_the_caller_scheduling_it():
    scanner = CountingScanner()
    spawner = ThreadSpawner()
    coordinator = build_coordinator(scanner, spawner)

    coordinator.start_indexing(trigger="manual")
    spawner.join()

    assert coordinator.status()["state"] == "done"
    assert scanner.calls == 1


def test_cancel_and_restart_while_running_cancels_the_scan_and_reruns_exactly_once():
    scanner = CountingScanner(hold_first_run=True)
    spawner = ThreadSpawner()
    coordinator = build_coordinator(scanner, spawner)
    coordinator.start_indexing(trigger="manual")
    assert scanner.first_run_entered.wait(timeout=5)

    coordinator.cancel_and_restart(trigger="roots_change")
    spawner.join()

    status = coordinator.status()
    assert scanner.first_run_was_cancelled is True
    assert scanner.calls == 2
    assert status["state"] == "done"
    assert status["trigger"] == "roots_change"


def test_start_indexing_while_a_scan_is_running_does_not_run_a_second_scan():
    scanner = CountingScanner(hold_first_run=True)
    spawner = ThreadSpawner()
    coordinator = build_coordinator(scanner, spawner)
    coordinator.start_indexing(trigger="manual")
    assert scanner.first_run_entered.wait(timeout=5)

    coordinator.start_indexing(trigger="manual")
    coordinator.start_indexing(trigger="manual")
    assert len(spawner.threads) == 1
    scanner.release_first_run.set()
    spawner.join()

    assert scanner.calls == 1
    assert coordinator.status()["state"] == "done"
