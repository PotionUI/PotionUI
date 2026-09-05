"""
Tests that system monitoring never stalls the event loop.

Sampling is a synchronous probe (a 100ms CPU wait plus NVML reads under a lock)
and broadcasting fans out to every connected websocket, so both have to stay off
the loop thread and off each other's critical path.
"""
import asyncio
import threading
import time
from unittest.mock import Mock

import pytest

from src.features.system_monitor.coordinator import SystemMonitorCoordinator
from src.features.system_monitor.connection_hub import (
    MonitoringConnectionHub,
    SEND_TIMEOUT_SECONDS,
)


SLOW_PROBE_SECONDS = 0.3
MAX_ACCEPTABLE_LOOP_GAP = 0.1

SNAPSHOT = {
    "cpu": {"usage_percent": 45.5, "core_count": 8, "core_count_physical": 4},
    "ram": {"total_gb": 32.0, "available_gb": 16.0, "used_gb": 16.0, "usage_percent": 50.0},
    "vram": {"total_gb": 12.0, "available_gb": 8.0, "used_gb": 4.0, "free_gb": 8.0},
    "gpu": {"temperature_c": 55.0, "available": True},
}


class LoopTicker:
    """Ticks on the event loop and records the gap between consecutive ticks."""

    def __init__(self, interval: float = 0.02):
        self.interval = interval
        self.gaps: list = []
        self._stop = asyncio.Event()
        self._task = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        # The ticker has to be running before the measured work starts, or a
        # blocking call finishes before its first tick and records no gap.
        while len(self.gaps) < 2:
            await asyncio.sleep(self.interval)
        self.gaps.clear()

    async def _run(self) -> None:
        last = time.perf_counter()
        while not self._stop.is_set():
            await asyncio.sleep(self.interval)
            now = time.perf_counter()
            self.gaps.append(now - last)
            last = now

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    @property
    def max_gap(self) -> float:
        return max(self.gaps) if self.gaps else 0.0


def make_slow_monitor(probe_threads: list):
    """A SystemMonitor whose snapshot blocks synchronously, like the real probe."""
    monitor = Mock()

    def slow_snapshot():
        probe_threads.append(threading.current_thread().name)
        time.sleep(SLOW_PROBE_SECONDS)
        return SNAPSHOT

    monitor.get_system_snapshot.side_effect = slow_snapshot
    return monitor


@pytest.fixture
def probe_threads():
    return []


@pytest.fixture
def slow_coordinator(probe_threads):
    coordinator = SystemMonitorCoordinator(
        system_monitor=make_slow_monitor(probe_threads),
        gpu_monitor=None,
        plugin_registry=None,
    )
    yield coordinator
    coordinator._shutdown_sampling_executor()


class TestSamplingOffTheEventLoop:
    """Sampling must not block the loop, and must not pile up."""

    async def test_slow_sample_does_not_stall_the_event_loop(self, slow_coordinator, probe_threads):
        ticker = LoopTicker()
        await ticker.start()

        stats = await slow_coordinator.collect_system_stats()

        await ticker.stop()

        assert stats["cpu"]["usage_percent"] == 45.5
        assert ticker.max_gap < MAX_ACCEPTABLE_LOOP_GAP, (
            f"event loop stalled for {ticker.max_gap:.3f}s during a "
            f"{SLOW_PROBE_SECONDS}s sample"
        )
        assert probe_threads[0].startswith("system-monitor-probe")

    async def test_concurrent_requests_share_one_sample(self, slow_coordinator):
        first = asyncio.create_task(slow_coordinator.collect_system_stats())
        await asyncio.sleep(0.05)
        second = asyncio.create_task(slow_coordinator.collect_system_stats())

        first_stats, second_stats = await asyncio.gather(first, second)

        assert slow_coordinator.system_monitor.get_system_snapshot.call_count == 1
        assert first_stats is second_stats

    async def test_sequential_requests_sample_again(self, slow_coordinator):
        await slow_coordinator.collect_system_stats()
        await slow_coordinator.collect_system_stats()

        assert slow_coordinator.system_monitor.get_system_snapshot.call_count == 2

    async def test_initial_snapshot_on_connect_does_not_stall_the_loop(
        self, slow_coordinator, probe_threads
    ):
        received = []

        class Client:
            async def send_text(self, data):
                received.append(data)

        async def client_gone():
            raise RuntimeError("client disconnected")

        ticker = LoopTicker()
        await ticker.start()

        await slow_coordinator.handle_websocket_connection(
            Client(), "client-1", receive_callback=client_gone
        )

        await ticker.stop()

        assert received, "initial stats were never sent"
        assert "system_update" in received[0]
        assert ticker.max_gap < MAX_ACCEPTABLE_LOOP_GAP, (
            f"event loop stalled for {ticker.max_gap:.3f}s while sampling for a "
            f"connecting client"
        )
        assert probe_threads[0].startswith("system-monitor-probe")

        # Let the detached probe thread finish before the loop closes.
        await asyncio.sleep(SLOW_PROBE_SECONDS)

    async def test_cancelling_mid_sample_stops_cleanly(self, slow_coordinator):
        slow_coordinator.connection_hub.add_connection(Mock())

        await slow_coordinator.start_monitoring_task()
        await asyncio.sleep(0.05)
        assert slow_coordinator.system_monitor.get_system_snapshot.call_count == 1

        await slow_coordinator.stop_monitoring_task()

        assert slow_coordinator.monitoring_task is None
        # The executor outlives the stop while its probe is still parked, so a
        # caller arriving mid-cycle cannot start a second one.
        assert slow_coordinator._sampling_executor is not None

        deadline = time.perf_counter() + 2.0
        while slow_coordinator._sampling_executor is not None and time.perf_counter() < deadline:
            await asyncio.sleep(0.02)

        assert slow_coordinator._sampling_executor is None
        assert slow_coordinator._pending_sample is None
        assert slow_coordinator.system_monitor.get_system_snapshot.call_count == 1


class BlockingProbe:
    """A probe that parks in the worker thread until released, counting overlap."""

    def __init__(self):
        self.release = threading.Event()
        self.calls = 0
        self.max_concurrent = 0
        self._active = 0
        self._lock = threading.Lock()

    def get_system_snapshot(self):
        with self._lock:
            self.calls += 1
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        self.release.wait(timeout=10.0)
        with self._lock:
            self._active -= 1
        return SNAPSHOT


class TestSamplingLifecycle:
    """Single-flight has to survive stop/start, not just concurrent callers."""

    async def test_stop_start_cycles_never_overlap_probes(self):
        probe = BlockingProbe()
        coordinator = SystemMonitorCoordinator(
            system_monitor=probe, gpu_monitor=None, plugin_registry=None
        )
        waiters = []

        try:
            waiters.append(asyncio.create_task(coordinator.collect_system_stats()))
            deadline = time.perf_counter() + 2.0
            while probe.calls < 1 and time.perf_counter() < deadline:
                await asyncio.sleep(0.01)
            assert probe.calls == 1, "the first probe never started"

            # Disconnect and reconnect while the probe is still parked. The
            # executor cannot be replaced here or a second probe joins the first.
            for _ in range(3):
                await coordinator.stop_monitoring_task()
                waiters.append(asyncio.create_task(coordinator.collect_system_stats()))
                await asyncio.sleep(0.05)

            assert probe.calls == 1, (
                f"{probe.calls} probes started while one was still running"
            )
            assert probe.max_concurrent == 1
        finally:
            probe.release.set()

        results = await asyncio.gather(*waiters)

        assert probe.max_concurrent == 1
        assert len(results) == 4
        for stats in results:
            assert stats["cpu"]["usage_percent"] == 45.5

        coordinator._shutdown_sampling_executor()

    async def test_reconnect_during_a_parked_probe_does_not_start_a_second(self):
        probe = BlockingProbe()
        coordinator = SystemMonitorCoordinator(
            system_monitor=probe, gpu_monitor=None, plugin_registry=None
        )
        received = []

        class Client:
            async def send_text(self, data):
                received.append(data)

        async def client_gone():
            raise RuntimeError("client disconnected")

        try:
            first = asyncio.create_task(coordinator.collect_system_stats())
            deadline = time.perf_counter() + 2.0
            while probe.calls < 1 and time.perf_counter() < deadline:
                await asyncio.sleep(0.01)
            assert probe.calls == 1

            await coordinator.stop_monitoring_task()
            reconnect = asyncio.create_task(
                coordinator.handle_websocket_connection(
                    Client(), "client-1", receive_callback=client_gone
                )
            )
            await asyncio.sleep(0.05)

            assert probe.calls == 1, (
                f"a reconnecting client started probe #{probe.calls} alongside a "
                f"running one"
            )
        finally:
            probe.release.set()

        await first
        await reconnect

        assert probe.max_concurrent == 1
        assert received, "the reconnecting client never got its snapshot"

        coordinator._shutdown_sampling_executor()


class HealthyClient:
    def __init__(self):
        self.received = []
        self.times = []

    async def send_text(self, data: str) -> None:
        self.received.append(data)
        self.times.append(time.perf_counter())


class SlowClient:
    def __init__(self, delay: float):
        self.delay = delay
        self.received = []

    async def send_text(self, data: str) -> None:
        await asyncio.sleep(self.delay)
        self.received.append(data)


class FailingClient:
    async def send_text(self, data: str) -> None:
        raise RuntimeError("connection closed")


class TestBroadcastIsolation:
    """One bad client must not hold up the rest of the fan-out."""

    async def test_slow_client_does_not_delay_healthy_ones(self):
        hub = MonitoringConnectionHub()
        first, second = HealthyClient(), HealthyClient()
        slow = SlowClient(delay=5.0)
        for client in (first, slow, second):
            hub.add_connection(client)

        started = time.perf_counter()
        failed = await hub.broadcast({"cpu": {"usage_percent": 1.0}})
        elapsed = time.perf_counter() - started

        assert elapsed < SEND_TIMEOUT_SECONDS + 1.0, (
            f"broadcast waited {elapsed:.2f}s on a 5s client"
        )

        for client in (first, second):
            assert len(client.received) == 1
            assert client.times[0] - started < MAX_ACCEPTABLE_LOOP_GAP

        assert failed == [slow]
        assert hub.connection_count() == 2
        assert slow not in hub.active_connections

    async def test_failing_client_is_removed_and_others_still_receive(self):
        hub = MonitoringConnectionHub()
        healthy = HealthyClient()
        broken = FailingClient()
        hub.add_connection(broken)
        hub.add_connection(healthy)

        failed = await hub.broadcast({"cpu": {"usage_percent": 1.0}})

        assert failed == [broken]
        assert hub.active_connections == [healthy]
        assert len(healthy.received) == 1

    async def test_payload_is_encoded_once(self):
        hub = MonitoringConnectionHub()
        clients = [HealthyClient() for _ in range(3)]
        for client in clients:
            hub.add_connection(client)

        await hub.broadcast({"cpu": {"usage_percent": 1.0}})

        payloads = [client.received[0] for client in clients]
        assert all(payload is payloads[0] for payload in payloads)

    async def test_connection_removed_mid_broadcast_does_not_raise(self):
        hub = MonitoringConnectionHub()
        healthy = HealthyClient()
        leaving = SlowClient(delay=0.05)
        hub.add_connection(healthy)
        hub.add_connection(leaving)

        async def drop_it():
            await asyncio.sleep(0.01)
            hub.remove_connection(leaving)

        _, failed = await asyncio.gather(
            drop_it(),
            hub.broadcast({"cpu": {"usage_percent": 1.0}}),
        )

        assert failed == []
        assert hub.active_connections == [healthy]
