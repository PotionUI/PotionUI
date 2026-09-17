"""`NativeAvailabilityReconciler` re-indexes local native backends only,
never remote-native or comfyui, swallows per-backend failures, and
serialises concurrent calls against the same backend so they cannot race
inside `BackendModelIndexer.index_backend`'s own
`delete_for_backend(keep_model_ids=...)` step.
"""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from src.features.backends.backend_config import NATIVE_ENGINE, NATIVE_LOCAL_DRIVER, NATIVE_REMOTE_DRIVER
from src.features.models.native_availability_reconciler import NativeAvailabilityReconciler


@dataclass
class FakeConfig:
    driver: str


class FakeBackend:
    def __init__(self, backend_id, driver, listing_supported=True):
        self.backend_id = backend_id
        self.config = FakeConfig(driver=driver)
        self._listing_supported = listing_supported

    def supports_model_listing(self):
        return self._listing_supported


class FakeBackendRegistry:
    def __init__(self, backends):
        self._backends = backends

    def get_backends_for_engine(self, engine):
        assert engine == NATIVE_ENGINE
        return list(self._backends)


class FakeIndexResult:
    def __init__(self, created=0, matched=0, removed=0):
        self.created = created
        self.matched = matched
        self.removed = removed


class FakeIndexer:
    def __init__(self):
        self.calls = []
        self.results = {}
        self.raises = {}

    async def index_backend(self, backend):
        self.calls.append(backend.backend_id)
        if backend.backend_id in self.raises:
            raise self.raises[backend.backend_id]
        return self.results.get(backend.backend_id, FakeIndexResult())


def test_reconciles_only_local_native_backends():
    local = FakeBackend("local-1", NATIVE_LOCAL_DRIVER)
    remote = FakeBackend("remote-1", NATIVE_REMOTE_DRIVER)
    indexer = FakeIndexer()
    indexer.results["local-1"] = FakeIndexResult(created=1, matched=2, removed=0)
    reconciler = NativeAvailabilityReconciler(indexer=indexer)

    summary = asyncio.run(reconciler.reconcile(FakeBackendRegistry([local, remote])))

    assert indexer.calls == ["local-1"]
    assert summary.backend_ids == ["local-1"]
    assert summary.created == 1
    assert summary.matched == 2


def test_skips_backends_that_do_not_support_listing():
    local = FakeBackend("local-1", NATIVE_LOCAL_DRIVER, listing_supported=False)
    indexer = FakeIndexer()
    reconciler = NativeAvailabilityReconciler(indexer=indexer)

    summary = asyncio.run(reconciler.reconcile(FakeBackendRegistry([local])))

    assert indexer.calls == []
    assert summary.backend_ids == []


def test_per_backend_failure_is_swallowed_and_reported():
    ok = FakeBackend("local-ok", NATIVE_LOCAL_DRIVER)
    broken = FakeBackend("local-broken", NATIVE_LOCAL_DRIVER)
    indexer = FakeIndexer()
    indexer.raises["local-broken"] = RuntimeError("disk unavailable")
    reconciler = NativeAvailabilityReconciler(indexer=indexer)

    summary = asyncio.run(reconciler.reconcile(FakeBackendRegistry([ok, broken])))

    assert sorted(indexer.calls) == ["local-broken", "local-ok"]
    assert summary.backend_ids == ["local-ok"]
    assert summary.failed_backend_ids == ["local-broken"]


def test_no_backend_registry_is_a_no_op():
    indexer = FakeIndexer()
    reconciler = NativeAvailabilityReconciler(indexer=indexer)

    summary = asyncio.run(reconciler.reconcile(None))

    assert indexer.calls == []
    assert summary.backend_ids == []


def test_concurrent_calls_for_the_same_backend_are_serialised():
    """Two reconciles racing for the same backend must not overlap inside
    `index_backend` - overlapping runs could each compute a different
    `seen_model_ids` and delete rows the other just wrote."""
    local = FakeBackend("local-1", NATIVE_LOCAL_DRIVER)
    reconciler = NativeAvailabilityReconciler(indexer=FakeIndexer())

    in_flight = 0
    max_in_flight = 0
    lock_probe = asyncio.Lock()

    class TrackingIndexer:
        async def index_backend(self, backend):
            nonlocal in_flight, max_in_flight
            async with lock_probe:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.01)
            async with lock_probe:
                in_flight -= 1
            return FakeIndexResult()

    reconciler.indexer = TrackingIndexer()

    async def _run():
        registry = FakeBackendRegistry([local])
        await asyncio.gather(
            reconciler.reconcile(registry),
            reconciler.reconcile(registry),
        )

    asyncio.run(_run())

    assert max_in_flight == 1


def test_reconcile_works_from_separate_event_loops_and_threads():
    import asyncio
    import threading
    from unittest.mock import Mock

    from src.features.backends.backend_config import NATIVE_LOCAL_DRIVER
    from src.features.models.native_availability_reconciler import NativeAvailabilityReconciler

    in_flight = 0
    max_in_flight = 0
    guard = threading.Lock()

    class SlowIndexer:
        async def index_backend(self, backend):
            nonlocal in_flight, max_in_flight
            with guard:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            with guard:
                in_flight -= 1
            return Mock(created=0, matched=0, removed=0)

    backend = Mock()
    backend.backend_id = "native"
    backend.config.driver = NATIVE_LOCAL_DRIVER
    backend.supports_model_listing.return_value = True
    registry = Mock()
    registry.get_backends_for_engine.return_value = [backend]

    reconciler = NativeAvailabilityReconciler(indexer=SlowIndexer())
    summaries = []

    def run():
        summaries.append(asyncio.run(reconciler.reconcile(registry)))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(summaries) == 4
    assert all(s.backend_ids == ["native"] and not s.failed_backend_ids for s in summaries)
    assert max_in_flight == 1


def test_a_registry_failure_never_reaches_the_caller():
    import asyncio
    from unittest.mock import Mock

    from src.features.models.native_availability_reconciler import NativeAvailabilityReconciler

    registry = Mock()
    registry.get_backends_for_engine.side_effect = RuntimeError("registry down")

    summary = asyncio.run(NativeAvailabilityReconciler(indexer=Mock()).reconcile(registry))

    assert summary.backend_ids == []
