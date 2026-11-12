import asyncio
from unittest.mock import Mock

import pytest

from src.features.generation.routing.health import BackendHealthTracker, probes_remotely


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _backend(result=None, error=None, delay=0.0, backend_id="b"):
    backend = Mock(backend_id=backend_id)

    async def health_check():
        if delay:
            await asyncio.sleep(delay)
        if error is not None:
            raise error
        return result

    backend.health_check = health_check
    return backend


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["healthy", "available"])
async def test_a_healthy_or_available_answer_is_routable(status):
    tracker = BackendHealthTracker()

    assert await tracker.probe(_backend({"status": status})) is True
    assert tracker.is_healthy("b")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, reason", [
    ({"status": "offline", "error": "refused"}, "refused"),
    ({"status": "degraded", "reason": "CUDA is gone"}, "CUDA is gone"),
    ({"status": "error"}, "the backend reports 'error'"),
    ({"status": "inactive", "message": "disabled"}, "disabled"),
])
async def test_any_other_answer_makes_the_backend_unroutable_with_a_reason(answer, reason):
    tracker = BackendHealthTracker(failures_to_down=1)

    assert await tracker.probe(_backend(answer)) is False
    assert not tracker.is_healthy("b")
    assert tracker.reason("b") == reason


@pytest.mark.asyncio
async def test_a_probe_that_raises_or_times_out_is_a_failed_probe():
    tracker = BackendHealthTracker(probe_timeout_seconds=0.02, failures_to_down=1)

    assert await tracker.probe(_backend(error=RuntimeError("boom"))) is False
    assert "boom" in tracker.reason("b")
    assert await tracker.probe(_backend({"status": "healthy"}, delay=1)) is False
    assert "timed out" in tracker.reason("b")


def test_a_backend_nobody_has_probed_is_assumed_healthy():
    assert BackendHealthTracker().is_healthy("never-seen")


def test_listeners_hear_only_about_changes():
    tracker = BackendHealthTracker()
    heard = []
    tracker.add_listener(lambda backend_id, healthy: heard.append((backend_id, healthy)))

    tracker.record("b", True)
    tracker.record("b", False, "x")
    tracker.record("b", False, "y")
    tracker.record("b", True)

    assert heard == [("b", False), ("b", True)]


def test_a_failing_listener_does_not_break_recording():
    tracker = BackendHealthTracker()
    tracker.add_listener(Mock(side_effect=RuntimeError("bad listener")))

    tracker.record("b", False, "x")

    assert not tracker.is_healthy("b")


def test_consecutive_failures_count_up_and_reset_on_recovery():
    tracker = BackendHealthTracker()

    tracker.record("b", False, "x")
    tracker.record("b", False, "x")
    assert tracker.consecutive_failures("b") == 2
    tracker.record("b", True)

    assert tracker.consecutive_failures("b") == 0


def test_a_probe_is_due_when_never_probed_and_again_after_the_ttl():
    clock = Clock()
    tracker = BackendHealthTracker(clock=clock, ttl_seconds=30)

    assert tracker.needs_probe("b")
    tracker.record("b", True)
    assert not tracker.needs_probe("b")
    clock.now = 29
    assert not tracker.needs_probe("b")
    clock.now = 30
    assert tracker.needs_probe("b")


@pytest.mark.asyncio
async def test_a_probe_in_flight_is_not_started_twice():
    tracker = BackendHealthTracker()
    slow = _backend({"status": "healthy"}, delay=0.05)

    first = asyncio.ensure_future(tracker.probe(slow))
    await asyncio.sleep(0.01)

    assert not tracker.needs_probe("b")
    await first


def test_forgetting_a_backend_returns_it_to_unknown():
    tracker = BackendHealthTracker()
    tracker.record("b", False, "x")

    tracker.forget("b")

    assert tracker.is_healthy("b")


def test_only_backends_that_do_not_share_this_hosts_gpu_are_probed():
    local, remote, other = Mock(), Mock(), Mock()
    local.execution_device = "this_host_gpu"
    remote.execution_device = "remote"
    other.execution_device = "unestablished"

    assert [probes_remotely(b) for b in (local, remote, other)] == [False, True, True]


@pytest.mark.asyncio
async def test_one_failed_probe_does_not_take_a_backend_down_but_a_second_does():
    clock = Clock()
    tracker = BackendHealthTracker(clock=clock, failures_to_down=2, suspect_retry_seconds=5)
    heard = []
    tracker.add_listener(lambda backend_id, healthy: heard.append(healthy))
    flaky = _backend({"status": "offline", "error": "timeout"})

    assert await tracker.probe(flaky) is True
    assert tracker.is_healthy("b") and heard == []
    assert not tracker.needs_probe("b")
    clock.now = 5
    assert tracker.needs_probe("b")

    assert await tracker.probe(flaky) is False
    assert not tracker.is_healthy("b") and heard == [False]


@pytest.mark.asyncio
async def test_a_good_probe_between_two_failures_resets_the_count():
    tracker = BackendHealthTracker(failures_to_down=2)

    await tracker.probe(_backend({"status": "offline"}))
    await tracker.probe(_backend({"status": "healthy"}))
    await tracker.probe(_backend({"status": "offline"}))

    assert tracker.is_healthy("b")


@pytest.mark.asyncio
async def test_a_down_backend_recovers_on_the_first_good_probe():
    tracker = BackendHealthTracker(failures_to_down=2)
    tracker.record("b", False, "x")

    assert await tracker.probe(_backend({"status": "healthy"})) is True
    assert tracker.is_healthy("b")
