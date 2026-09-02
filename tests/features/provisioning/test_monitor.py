"""`ComputeStatusMonitor.tick()` against the same fakes as `test_operations`:
the heartbeat that makes a pod paused in the provider's console show up here
- and stop being routed to - without anyone clicking refresh.
"""

import asyncio

import pytest

from src.features.provisioning.contracts import ComputeProvisionerError, ComputeStatus
from src.features.provisioning.monitor import (
    DEFAULT_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    ComputeStatusMonitor,
    resolve_interval,
)

from tests.features.provisioning.test_operations import (
    Collaborators,
    FakeProvisioner,
    ProvisionProgress,
    _seed_remote_backend,
)


def _monitor(c: Collaborators, **kwargs) -> ComputeStatusMonitor:
    return ComputeStatusMonitor(c.registry, c.repository, c.backend_registry, c.hub, c.jobs, **kwargs)


async def _running_row(c: Collaborators):
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1")
    row = await c.provision_and_wait()
    assert row.status == "running"
    assert c.backend().enabled is True
    c.hub.messages.clear()
    return row


async def test_running_to_stopped_disables_the_backend_and_broadcasts_once():
    c = Collaborators()
    row = await _running_row(c)
    c.provisioner.status_state = "stopped"
    c.provisioner.status_detail = "Pod pod-1 is EXITED (stopped)"

    await _monitor(c).tick()

    fresh = c.repository.get_by_id(row.id)
    assert fresh.status == "stopped"
    assert fresh.status_detail == "Pod pod-1 is EXITED (stopped)"
    assert fresh.status_checked_at is not None
    assert c.backend().enabled is False
    assert c.backend().base_url == "https://fake-worker:8100"  # disabled, not disconnected
    assert [r["status"] for r in c.hub.rows()] == ["stopped"]


@pytest.mark.parametrize("state", ["missing", "failed"])
async def test_missing_or_failed_disables_the_backend_too(state):
    c = Collaborators()
    await _running_row(c)
    c.provisioner.status_state = state

    await _monitor(c).tick()

    assert c.backend().enabled is False


async def test_unreachable_does_not_disable_the_backend():
    c = Collaborators()
    await _running_row(c)
    c.provisioner.status_state = "unreachable"

    await _monitor(c).tick()

    assert c.backend().enabled is True
    assert [r["status"] for r in c.hub.rows()] == ["unreachable"]


async def test_no_broadcast_and_no_backend_change_when_nothing_changed():
    c = Collaborators()
    row = await _running_row(c)
    c.provisioner.status_detail = "Worker is up"
    c.repository.update_status(row.id, "running", detail="Worker is up")

    await _monitor(c).tick()
    await _monitor(c).tick()

    assert c.hub.messages == []
    assert c.backend().enabled is True
    assert c.repository.get_by_id(row.id).status_checked_at is not None  # still stamped every tick


async def test_stopped_to_running_broadcasts_but_does_not_enable_the_backend():
    c = Collaborators()
    row = await _running_row(c)
    c.provisioner.status_state = "stopped"
    await _monitor(c).tick()
    assert c.backend().enabled is False
    c.hub.messages.clear()

    c.provisioner.status_state = "running"
    await _monitor(c).tick()

    assert c.repository.get_by_id(row.id).status == "running"
    assert c.backend().enabled is False  # operator intent - the UI offers "Enable backend"
    assert [r["status"] for r in c.hub.rows()] == ["running"]


class _BrokenProvisioner(FakeProvisioner):
    async def status(self, handle):
        raise ComputeProvisionerError("RunPod API error 429: rate limited")


async def test_provider_error_marks_unknown_with_the_message_and_the_loop_survives():
    c = Collaborators()
    row = await _running_row(c)
    c.registry._provisioners["fake"] = _BrokenProvisioner()

    monitor = _monitor(c)
    await monitor.tick()
    await monitor.tick()

    fresh = c.repository.get_by_id(row.id)
    assert fresh.status == "unknown"
    assert fresh.status_detail == "RunPod API error 429: rate limited"
    assert c.backend().enabled is True  # unknown is not a reason to stop routing
    assert [r["status"] for r in c.hub.rows()] == ["unknown"]


class _ExplodingProvisioner(FakeProvisioner):
    async def status(self, handle):
        raise RuntimeError("bug in the plugin")


async def test_unexpected_exception_marks_unknown_and_the_loop_survives():
    c = Collaborators()
    row = await _running_row(c)
    c.registry._provisioners["fake"] = _ExplodingProvisioner()

    await _monitor(c).tick()

    assert c.repository.get_by_id(row.id).status == "unknown"
    assert c.repository.get_by_id(row.id).status_detail == "bug in the plugin"


class _SlowProvisioner(FakeProvisioner):
    async def status(self, handle):
        await asyncio.sleep(10)
        return ComputeStatus(state="running")


async def test_status_call_timeout_marks_unknown():
    c = Collaborators()
    row = await _running_row(c)
    c.registry._provisioners["fake"] = _SlowProvisioner()

    await _monitor(c, call_timeout_seconds=0.01).tick()

    fresh = c.repository.get_by_id(row.id)
    assert fresh.status == "unknown"
    assert "timed out" in fresh.status_detail


class _HangingProvisioner(FakeProvisioner):
    async def provision(self, request, report):
        await report(ProvisionProgress(stage="creating", message="Requesting pod"))
        await asyncio.Event().wait()

    async def status(self, handle):
        raise AssertionError("status() must not be asked about a row still provisioning")


async def test_rows_with_a_running_job_are_skipped():
    c = Collaborators(_HangingProvisioner())
    await _seed_remote_backend(c.backend_registry)
    row = await c.provision()
    await asyncio.sleep(0)
    c.hub.messages.clear()

    await _monitor(c).tick()

    assert c.repository.get_by_id(row.id).status == "provisioning"
    assert c.hub.messages == []
    await c.jobs.cancel(row.id)


async def test_a_provisioning_row_with_no_job_behind_it_is_failed():
    """The process died mid-bring-up: nothing will ever finish that row."""
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry)
    row = c.repository.create(
        provider_id="fake", handle="", profile_name="orphan", status="provisioning", backend_id="remote-1",
    )

    await _monitor(c).tick()

    fresh = c.repository.get_by_id(row.id)
    assert fresh.status == "failed"
    assert "restart" in fresh.status_detail
    assert [r["status"] for r in c.hub.rows()] == ["failed"]


async def test_unregistered_provider_marks_unknown():
    c = Collaborators()
    row = await _running_row(c)
    c.registry._provisioners.clear()

    await _monitor(c).tick()

    fresh = c.repository.get_by_id(row.id)
    assert fresh.status == "unknown"
    assert "fake" in fresh.status_detail


async def test_start_and_stop_run_the_loop():
    c = Collaborators()
    row = await _running_row(c)
    c.provisioner.status_state = "stopped"
    monitor = _monitor(c, interval_seconds=60)

    monitor.start()
    await asyncio.sleep(0.05)
    await monitor.stop()

    assert c.repository.get_by_id(row.id).status == "stopped"
    assert c.backend().enabled is False


class _FakeSettings:
    def __init__(self, value):
        self.value = value

    def get_setting(self, key, default=None):
        return self.value if self.value is not None else default


def test_resolve_interval_reads_the_setting_and_clamps_to_the_floor():
    assert resolve_interval(None) == DEFAULT_INTERVAL_SECONDS
    assert resolve_interval(_FakeSettings(None)) == DEFAULT_INTERVAL_SECONDS
    assert resolve_interval(_FakeSettings(30)) == 30
    assert resolve_interval(_FakeSettings("45")) == 45
    assert resolve_interval(_FakeSettings(1)) == MIN_INTERVAL_SECONDS
    assert resolve_interval(_FakeSettings("garbage")) == DEFAULT_INTERVAL_SECONDS
