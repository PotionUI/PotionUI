
from unittest.mock import AsyncMock, MagicMock

from src.features.models.indexing_coordinator import ModelIndexingCoordinator

class FakeHookContext:
    def __init__(self):
        self.data = {}

class FakePluginRegistry:

    def execute_hook(self, hook, initial_data):
        return FakeHookContext(), False

def _coordinator(scanner_result=None, scanner_raises=None, reconciler=None, backend_registry=None):
    scanner = MagicMock()
    if scanner_raises is not None:
        scanner.index_models.side_effect = scanner_raises
    else:
        scanner.index_models.return_value = scanner_result or {"indexed": 1}

    return ModelIndexingCoordinator(
        model_repository=MagicMock(),
        plugin_registry=FakePluginRegistry(),
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

    def count_unindexed(self):
        return {"total": len(self._unindexed), "by_type": {}}

    def index_models(self):
        indexed = list(self._unindexed)
        self._unindexed = []
        self.index_models_calls += 1
        return {"indexed": len(indexed)}

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

def test_resume_interrupted_indexing_does_not_start_when_nothing_is_unindexed():
    scanner = FakeScanner([])
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    coordinator = _coordinator_with_scanner(scanner, reconciler)

    started = coordinator.resume_interrupted_indexing()

    assert started is False
    assert scanner.index_models_calls == 0
