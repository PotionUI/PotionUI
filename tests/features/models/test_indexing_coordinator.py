import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.models.exceptions import ModelIndexingException
from src.features.models.indexing_coordinator import ModelIndexingCoordinator


class FakeHookContext:
    def __init__(self, data=None):
        self.data = data or {}


class FakePluginRegistry:

    def __init__(self, blocked=False, block_reason="Indexing blocked by plugin"):
        self.blocked = blocked
        self.block_reason = block_reason

    def execute_hook(self, hook, initial_data):
        if self.blocked:
            return FakeHookContext({"blocked": True, "block_reason": self.block_reason}), True
        return FakeHookContext(), False


def _coordinator(scanner_result=None, scanner_raises=None, reconciler=None, backend_registry=None, plugin_registry=None):
    scanner = MagicMock()
    if scanner_raises is not None:
        scanner.index_models.side_effect = scanner_raises
    else:
        scanner.index_models.return_value = scanner_result or {"indexed": 1}

    return ModelIndexingCoordinator(
        model_repository=MagicMock(),
        plugin_registry=plugin_registry or FakePluginRegistry(),
        scanner=scanner,
        backend_registry=backend_registry,
        native_availability_reconciler=reconciler,
    )

def test_run_indexing_reconciles_native_availability_after_a_successful_scan():
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    backend_registry = MagicMock()
    coordinator = _coordinator(reconciler=reconciler, backend_registry=backend_registry)

    coordinator.run_indexing()

    reconciler.reconcile.assert_awaited_once_with(backend_registry)

def test_run_indexing_skips_reconcile_when_the_scan_fails():
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    coordinator = _coordinator(scanner_raises=OSError("disk unavailable"), reconciler=reconciler)

    coordinator.run_indexing()

    reconciler.reconcile.assert_not_awaited()

def test_reconcile_failure_does_not_raise():
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock(side_effect=RuntimeError("boom"))
    coordinator = _coordinator(reconciler=reconciler)

    coordinator.run_indexing()

    reconciler.reconcile.assert_awaited_once()

class FakeScanner:
    def __init__(self, unindexed_files):
        self._unindexed = list(unindexed_files)
        self.index_models_calls = 0
        self._progress_cb = None

    def count_unindexed(self):
        return {"total": len(self._unindexed), "by_type": {}}

    def set_progress_callback(self, callback):
        self._progress_cb = callback

    def index_models(self, cancel_check=None):
        indexed = list(self._unindexed)
        self._unindexed = []
        self.index_models_calls += 1
        if self._progress_cb:
            self._progress_cb(len(indexed), len(indexed), "done")
        return {
            "indexed": len(indexed),
            "found_on_disk": len(indexed),
            "failed_files": [],
            "cancelled": False,
        }

def _coordinator_with_scanner(scanner, reconciler=None):
    return ModelIndexingCoordinator(
        model_repository=MagicMock(),
        plugin_registry=FakePluginRegistry(),
        scanner=scanner,
        backend_registry=None,
        native_availability_reconciler=reconciler,
    )

def test_resume_interrupted_indexing_starts_and_indexes_every_unindexed_file():
    scanner = FakeScanner(["a.safetensors", "b.safetensors"])
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    coordinator = _coordinator_with_scanner(scanner, reconciler)

    started = coordinator.resume_interrupted_indexing()

    assert started is True
    assert scanner.index_models_calls == 1
    assert coordinator.count_unindexed()["total"] == 0
    assert coordinator.status()["trigger"] == "startup"

def test_resume_interrupted_indexing_does_not_start_when_nothing_is_unindexed():
    scanner = FakeScanner([])
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    coordinator = _coordinator_with_scanner(scanner, reconciler)

    started = coordinator.resume_interrupted_indexing()

    assert started is False
    assert scanner.index_models_calls == 0



def test_second_start_indexing_call_joins_the_first_instead_of_restarting():
    coordinator = _coordinator()

    first = coordinator.start_indexing(trigger="manual")
    second = coordinator.start_indexing(trigger="manual")

    assert first["state"] == "scanning"
    assert second["state"] == "scanning"
    assert second["started_at"] == first["started_at"]

def test_status_never_walks_disk_for_unindexed_counts():
    coordinator = _coordinator()

    status = coordinator.status()

    assert "unindexed" not in status
    coordinator.scanner.count_unindexed.assert_not_called()

def test_veto_sets_blocked_state_visible_via_status():
    plugin_registry = FakePluginRegistry(blocked=True, block_reason="maintenance in progress")
    coordinator = ModelIndexingCoordinator(
        model_repository=MagicMock(), plugin_registry=plugin_registry, scanner=MagicMock(),
    )

    with pytest.raises(ModelIndexingException):
        coordinator.start_indexing()

    status = coordinator.status()
    assert status["state"] == "blocked"
    assert status["error"] == "maintenance in progress"

def test_run_indexing_sets_failed_state_with_error_message_on_crash():
    coordinator = _coordinator(scanner_raises=RuntimeError("disk exploded"))
    coordinator.start_indexing()

    coordinator.run_indexing()

    status = coordinator.status()
    assert status["state"] == "failed"
    assert "disk exploded" in status["error"]

def test_zero_files_on_disk_is_reported_explicitly():
    coordinator = _coordinator(scanner_result={
        "indexed": 0, "found_on_disk": 0, "failed_files": [], "cancelled": False, "total": 0,
    })
    coordinator.start_indexing()

    coordinator.run_indexing()

    status = coordinator.status()
    assert status["found_on_disk"] == 0
    assert status["state"] == "done"

def test_per_file_failures_from_the_scan_are_recorded_in_status():
    failed = [{"path": f"f{i}.safetensors", "error": "boom"} for i in range(3)]
    coordinator = _coordinator(scanner_result={
        "indexed": 1, "failed_files": failed, "found_on_disk": 4, "cancelled": False,
    })
    coordinator.start_indexing()

    coordinator.run_indexing()

    status = coordinator.status()
    assert status["failed_files_total"] == 3
    assert status["failed_files"] == failed

def test_progress_updates_processed_and_total_via_scanner_callback():
    class ProgressScanner:
        def __init__(self):
            self._cb = None

        def set_progress_callback(self, cb):
            self._cb = cb

        def index_models(self, cancel_check=None):
            self._cb(0, 0, "Scanning...")
            self._cb(0, 3, "Looking...")
            self._cb(1, 3, "Checked a")
            self._cb(2, 3, "Checked b")
            self._cb(3, 3, "Checked c")
            return {"indexed": 3, "found_on_disk": 3, "failed_files": [], "cancelled": False}

    coordinator = ModelIndexingCoordinator(
        model_repository=MagicMock(), plugin_registry=FakePluginRegistry(), scanner=ProgressScanner(),
    )
    coordinator.start_indexing()

    coordinator.run_indexing()

    status = coordinator.status()
    assert status["total"] == 3
    assert status["processed"] == 3
    assert status["state"] == "done"



def test_concurrent_run_indexing_calls_execute_the_scan_only_once():
    started = threading.Event()
    release = threading.Event()
    call_count = {"n": 0}

    class BlockingScanner:
        def set_progress_callback(self, cb):
            pass

        def index_models(self, cancel_check=None):
            call_count["n"] += 1
            started.set()
            release.wait(timeout=2)
            return {"indexed": 0, "found_on_disk": 0, "failed_files": [], "cancelled": False}

    coordinator = ModelIndexingCoordinator(
        model_repository=MagicMock(), plugin_registry=FakePluginRegistry(), scanner=BlockingScanner(),
    )
    coordinator.start_indexing()

    t1 = threading.Thread(target=coordinator.run_indexing)
    t1.start()
    assert started.wait(timeout=2)

    t2 = threading.Thread(target=coordinator.run_indexing)
    t2.start()
    t2.join(timeout=2)

    release.set()
    t1.join(timeout=2)

    assert call_count["n"] == 1
    assert coordinator.status()["state"] == "done"



def test_cancel_and_restart_returns_immediately_without_waiting_for_the_running_scan():
    started = threading.Event()
    release = threading.Event()

    class IgnoresCancelScanner:
        def __init__(self):
            self.calls = 0

        def set_progress_callback(self, cb):
            pass

        def index_models(self, cancel_check=None):
            self.calls += 1
            started.set()
            release.wait(timeout=2)
            return {"indexed": 0, "found_on_disk": 1, "failed_files": [], "cancelled": False}

    scanner = IgnoresCancelScanner()
    coordinator = ModelIndexingCoordinator(
        model_repository=MagicMock(), plugin_registry=FakePluginRegistry(), scanner=scanner,
    )
    coordinator.start_indexing(trigger="manual")
    run_thread = threading.Thread(target=coordinator.run_indexing)
    run_thread.start()
    assert started.wait(timeout=2)

    before = time.monotonic()
    result = coordinator.cancel_and_restart(trigger="location_change")
    elapsed = time.monotonic() - before

    assert elapsed < 1.0
    assert result["restart_pending"] is True
    assert result["state"] in ("scanning", "indexing")

    started.clear()
    release.set()
    assert started.wait(timeout=2)
    release.set()
    run_thread.join(timeout=2)

    assert scanner.calls == 2
    status = coordinator.status()
    assert status["trigger"] == "location_change"
    assert status["restart_pending"] is False

def test_cancel_and_restart_starts_normally_when_nothing_is_running():
    coordinator = _coordinator()

    result = coordinator.cancel_and_restart(trigger="location_change")

    assert result["state"] == "scanning"
    assert result["trigger"] == "location_change"
    assert result["restart_pending"] is False

def test_start_indexing_during_the_tail_window_does_not_get_dropped():
    hold = threading.Event()
    entered_reconcile = threading.Event()

    class SlowReconciler:
        async def reconcile(self, backend_registry):
            entered_reconcile.set()
            await asyncio.to_thread(hold.wait, 2)

    scanner = FakeScanner(["a.safetensors"])
    coordinator = ModelIndexingCoordinator(
        model_repository=MagicMock(),
        plugin_registry=FakePluginRegistry(),
        scanner=scanner,
        native_availability_reconciler=SlowReconciler(),
    )
    coordinator.start_indexing(trigger="manual")
    run_thread = threading.Thread(target=coordinator.run_indexing)
    run_thread.start()
    assert entered_reconcile.wait(timeout=2)

    second_status = coordinator.start_indexing(trigger="manual")
    assert second_status["state"] == "scanning"

    second_thread = threading.Thread(target=coordinator.run_indexing)
    second_thread.start()
    second_thread.join(timeout=2)

    hold.set()
    run_thread.join(timeout=2)

    status = coordinator.status()
    assert status["state"] == "done"
    assert status["restart_pending"] is False
    assert scanner.index_models_calls == 2
