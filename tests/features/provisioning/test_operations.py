"""`src.features.provisioning.operations` against a fake `ComputeProvisioner`,
a fake in-memory repository, a fake `BackendRegistry` and a fake admin hub -
no network, no DB.

Proves the seam this feature owns: provisioning fills an EXISTING
`native.remote` backend row (created ahead of time, unconfigured, the same
way any other backend is created) rather than minting a new one, and links it
on the `ProvisionedCompute` row it returns at once as `provisioning`; the
bring-up itself runs in a `ComputeProvisioningJobs` task that streams
progress onto the row and over the hub. Stopping disables that backend
without touching its connection details; starting again runs the
provisioner's `start()` the same way and re-enables the backend with the
(possibly new) details it returns; terminating clears the connection details
and disables it, but the row itself survives so it can be provisioned into
again later.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from src.features.backends.backend_config import NativeBackendConfig, NativeRemoteBackendConfig
from src.features.provisioning import operations
from src.features.provisioning.contracts import (
    ComputeFieldDescriptorV1,
    ComputeFieldOptionV1,
    ComputeProvisioner,
    ComputeProvisionerError,
    ComputeStatus,
    ProvisionProgress,
    ProvisionRequest,
    ProvisionResult,
)
from src.features.provisioning.operations import (
    BackendAlreadyProvisionedError,
    BackendNotFoundError,
    ComputeNotStartableError,
    ComputeProvisioningJobs,
    InvalidProvisionValuesError,
    NotARemoteBackendError,
    ProvisionedComputeNotFoundError,
    UnknownProviderError,
)
from src.features.provisioning.records import ProvisionedCompute
from src.features.provisioning.repository import PROGRESS_CAP

# Options for the dependent `network_volume_id` field, keyed by the
# `gpu_type_id` value it `depends_on` - proves describe_fields(values) really
# resolves against the submitted values, not a fixed list.
_NETWORK_VOLUME_OPTIONS_BY_GPU = {
    "fake-gpu": [ComputeFieldOptionV1(value="vol-fake-gpu-1", label="1x NVMe (fake-gpu)")],
}


class FakeProvisioner(ComputeProvisioner):
    provider_id = "fake"
    label = "Fake Provider"

    #: Stages `provision()` reports, in order, before returning.
    stages = ("preparing", "creating", "ready")
    #: Stages `start()` reports, in order, before returning.
    start_stages = ("starting", "waiting_worker", "ready")

    def __init__(self):
        self.provisioned = []
        self.started = []
        self.stopped = []
        self.terminated = []
        self.status_state = "running"
        self.status_detail = None
        self.ready = True
        self.start_ready = True
        # Deliberately different from provision()'s, so a test can tell that
        # a start re-wrote the backend's connection details.
        self.start_base_url = "https://fake-worker-restarted:8100"
        self.start_worker_token = "tok-restarted"

    async def describe_fields(self, values=None):
        values = values or {}
        volume_options = _NETWORK_VOLUME_OPTIONS_BY_GPU.get(values.get("gpu_type_id"), [])
        return [
            ComputeFieldDescriptorV1(
                key="gpu_type_id",
                label="GPU Type",
                type="select",
                required=True,
                default="fake-gpu",
                options=[ComputeFieldOptionV1(value="fake-gpu", label="Fake GPU", detail="24 GB VRAM")],
            ),
            ComputeFieldDescriptorV1(
                key="network_volume_id",
                label="Network Volume",
                type="select",
                required=False,
                depends_on=["gpu_type_id"],
                options=volume_options,
            ),
            ComputeFieldDescriptorV1(
                key="volume_size_gb",
                label="Volume Size (GB)",
                type="number",
                required=False,
                default=100,
            ),
        ]

    async def provision(self, request: ProvisionRequest, report) -> ProvisionResult:
        self.provisioned.append(request)
        for index, stage in enumerate(self.stages):
            await report(ProvisionProgress(stage=stage, message=f"{stage} {request.profile_name}", percent=index * 50))
        return ProvisionResult(
            handle=request.profile_name,
            base_url="https://fake-worker:8100",
            worker_token="tok-abc123",
            ready=self.ready,
            resource_ref="res-1",
        )

    async def start(self, handle: str, report) -> ProvisionResult:
        self.started.append(handle)
        for index, stage in enumerate(self.start_stages):
            await report(ProvisionProgress(stage=stage, message=f"{stage} {handle}", percent=index * 50))
        return ProvisionResult(
            handle=handle,
            base_url=self.start_base_url,
            worker_token=self.start_worker_token,
            ready=self.start_ready,
            resource_ref="res-1",
        )

    async def status(self, handle: str) -> ComputeStatus:
        return ComputeStatus(state=self.status_state, detail=self.status_detail)

    async def stop(self, handle: str) -> None:
        self.stopped.append(handle)

    async def terminate(self, handle: str) -> None:
        self.terminated.append(handle)


class FakeProvisionerRegistry:
    def __init__(self, provisioners):
        self._provisioners = {p.provider_id: p for p in provisioners}

    def get(self, provider_id):
        return self._provisioners.get(provider_id)

    def list_provisioners(self):
        return list(self._provisioners.values())


class FakeRepository:
    """In-memory stand-in for `ProvisionedComputeRepository` - same call
    shapes, no DB."""

    def __init__(self):
        self._rows = {}
        self._counter = 0

    def create(self, **kwargs):
        self._counter += 1
        row_id = f"row-{self._counter}"
        row = ProvisionedCompute(id=row_id, **kwargs)
        self._rows[row_id] = row
        return row

    def update_handle(self, row_id, handle, resource_ref=None):
        row = self._rows.get(row_id)
        if row is None:
            return False
        row.handle = handle
        row.resource_ref = resource_ref
        return True

    def append_progress(self, row_id, entry):
        row = self._rows.get(row_id)
        if row is None:
            return False
        row.progress = (row.progress + [entry])[-PROGRESS_CAP:]
        row.status_detail = entry.get("message")
        return True

    def clear_progress(self, row_id):
        row = self._rows.get(row_id)
        if row is None:
            return False
        row.progress = []
        return True

    def get_by_id(self, row_id):
        return self._rows.get(row_id)

    def get_by_backend_id(self, backend_id):
        for row in self._rows.values():
            if row.backend_id == backend_id:
                return row
        return None

    def list_all(self):
        return list(self._rows.values())

    def update_status(self, row_id, status, detail=None, checked_at=None):
        row = self._rows.get(row_id)
        if row is None:
            return False
        row.status = status
        row.status_detail = detail
        if checked_at is not None:
            row.status_checked_at = checked_at
        return True

    def delete(self, row_id):
        return self._rows.pop(row_id, None) is not None


class FakeBackendConfigStore:
    def __init__(self, backends):
        self._backends = backends

    def get_backend(self, backend_id):
        return self._backends.get(backend_id)


class FakeBackendRegistry:
    """Stands in for `BackendRegistry` - `provision_compute` writes connection
    details straight onto whatever config object is already in the store."""

    def __init__(self):
        self._backends = {}
        self.backend_config_store = FakeBackendConfigStore(self._backends)
        self.removed = []

    async def add_backend(self, backend_config):
        self._backends[backend_config.id] = backend_config

    async def update_backend(self, backend_id, backend_config):
        self._backends[backend_id] = backend_config

    async def remove_backend(self, backend_id):
        if backend_id not in self._backends:
            raise ValueError(f"Backend '{backend_id}' not found")
        del self._backends[backend_id]
        self.removed.append(backend_id)


class FakeHub:
    """Stands in for `admin_connection_hub` - records every broadcast."""

    def __init__(self):
        self.messages = []

    async def broadcast(self, message):
        self.messages.append(message)

    def rows(self):
        return [m["row"] for m in self.messages if m["type"] == "compute_status"]


class Collaborators:
    def __init__(self, provisioner=None):
        self.provisioner = provisioner or FakeProvisioner()
        self.registry = FakeProvisionerRegistry([self.provisioner])
        self.repository = FakeRepository()
        self.backend_registry = FakeBackendRegistry()
        self.hub = FakeHub()
        self.jobs = ComputeProvisioningJobs(self.registry, self.repository, self.backend_registry, self.hub)

    async def provision(self, **overrides):
        kwargs = dict(provider_id="fake", backend_id="remote-1", values={"gpu_type_id": "fake-gpu"})
        kwargs.update(overrides)
        return await operations.provision_compute(
            self.registry, self.repository, self.backend_registry, self.jobs, **kwargs
        )

    async def provision_and_wait(self, **overrides):
        row = await self.provision(**overrides)
        await self.jobs.wait(row.id)
        return self.repository.get_by_id(row.id)

    def backend(self, backend_id="remote-1"):
        return self.backend_registry.backend_config_store.get_backend(backend_id)


def _collaborators(provisioner=None):
    c = Collaborators(provisioner)
    return c.provisioner, c.registry, c.repository, c.backend_registry


async def _seed_remote_backend(backend_registry, *, backend_id="remote-1", name="RunPod A100"):
    """Add an unconfigured `native.remote` backend to `backend_registry`, the
    way the admin "create backend" form would - `provision_compute` expects
    its target to already exist."""
    config = NativeRemoteBackendConfig(id=backend_id, name=name, enabled=False)
    await backend_registry.add_backend(config)
    return config


async def test_provision_compute_returns_a_provisioning_row_at_once():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1", name="RunPod A100")

    row = await c.provision(profile_name="prof-1", created_by="user-1")

    assert row.status == "provisioning"
    assert row.handle == ""  # the provisioner has not answered yet
    assert row.backend_id == "remote-1"
    assert row.gpu_type_id == "fake-gpu"
    assert row.created_by == "user-1"
    assert c.jobs.is_running(row.id)
    backend = c.backend()
    assert backend.enabled is False  # nothing to route to until the job finishes
    assert backend.base_url == ""
    await c.jobs.wait(row.id)


async def test_provision_job_streams_progress_then_fills_and_enables_the_backend():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1", name="RunPod A100")

    row = await c.provision_and_wait(profile_name="prof-1")

    assert row.status == "running"
    assert row.handle == "prof-1"
    assert row.resource_ref == "res-1"
    assert [e["stage"] for e in row.progress] == ["preparing", "creating", "ready"]
    assert row.progress[-1]["message"] == "ready prof-1"
    assert row.progress[-1]["percent"] == 100
    assert all(e["at"] for e in row.progress)
    assert row.status_detail == "Worker is up"
    assert row.status_checked_at is not None

    backend = c.backend()
    assert backend.name == "RunPod A100"  # untouched - provisioning fills, doesn't rename
    assert backend.enabled is True
    assert backend.base_url == "https://fake-worker:8100"
    assert backend.worker_token == "tok-abc123"

    # One broadcast per reported step, then one for the outcome - each the
    # whole row, so the client never merges.
    statuses = [(r["status"], len(r["progress"])) for r in c.hub.rows()]
    assert statuses == [("provisioning", 1), ("provisioning", 2), ("provisioning", 3), ("running", 3)]
    assert all(r["backend_id"] == "remote-1" for r in c.hub.rows())
    assert not c.jobs.is_running(row.id)


async def test_provision_job_not_ready_lands_as_unreachable_but_still_connects_the_backend():
    c = Collaborators()
    c.provisioner.ready = False
    await _seed_remote_backend(c.backend_registry)

    row = await c.provision_and_wait()

    assert row.status == "unreachable"
    assert "handshake" in row.status_detail
    assert c.backend().enabled is True
    assert c.backend().base_url == "https://fake-worker:8100"


class _FailingProvisioner(FakeProvisioner):
    async def provision(self, request, report):
        await report(ProvisionProgress(stage="preparing", message="Resolving"))
        raise ComputeProvisionerError("RunPod API error 401: RunPod API key was rejected")


async def test_provision_job_failure_marks_the_row_failed_and_leaves_the_backend_disabled():
    c = Collaborators(_FailingProvisioner())
    await _seed_remote_backend(c.backend_registry)

    row = await c.provision_and_wait()

    assert row.status == "failed"
    assert row.status_detail == "RunPod API error 401: RunPod API key was rejected"
    assert row.handle == ""
    assert [e["stage"] for e in row.progress] == ["preparing"]
    backend = c.backend()
    assert backend.enabled is False
    assert backend.base_url == ""
    assert backend.worker_token == ""
    assert [r["status"] for r in c.hub.rows()] == ["provisioning", "failed"]


class _CrashingProvisioner(FakeProvisioner):
    async def provision(self, request, report):
        raise RuntimeError("boom")


async def test_provision_job_any_exception_is_a_failed_row_not_a_lost_task():
    c = Collaborators(_CrashingProvisioner())
    await _seed_remote_backend(c.backend_registry)

    row = await c.provision_and_wait()

    assert row.status == "failed"
    assert row.status_detail == "boom"


async def test_provision_compute_replaces_a_failed_row():
    c = Collaborators(_FailingProvisioner())
    await _seed_remote_backend(c.backend_registry)
    failed = await c.provision_and_wait()
    assert failed.status == "failed"

    c.registry._provisioners["fake"] = FakeProvisioner()
    row = await c.provision_and_wait()

    assert row.id != failed.id
    assert c.repository.get_by_id(failed.id) is None
    assert row.status == "running"
    assert row.progress[0]["stage"] == "preparing"  # a clean timeline, not the failed one's
    assert c.backend().enabled is True


async def test_provision_compute_while_still_provisioning_raises():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry)
    first = await c.provision()

    with pytest.raises(BackendAlreadyProvisionedError) as excinfo:
        await c.provision()

    assert "already being brought up" in str(excinfo.value)
    await c.jobs.wait(first.id)


async def test_provision_compute_profile_name_defaults_to_backend_name():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1", name="RunPod A100")

    row = await c.provision_and_wait()

    assert row.profile_name == "RunPod A100"
    assert c.provisioner.provisioned[0].profile_name == "RunPod A100"


async def test_provision_compute_unknown_provider_raises():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry)

    with pytest.raises(UnknownProviderError):
        await c.provision(provider_id="missing", values={})


async def test_provision_compute_unknown_backend_raises():
    c = Collaborators()

    with pytest.raises(BackendNotFoundError):
        await c.provision(backend_id="does-not-exist")


async def test_provision_compute_non_remote_backend_raises():
    c = Collaborators()
    await c.backend_registry.add_backend(NativeBackendConfig(id="native", name="Local Generation"))

    with pytest.raises(NotARemoteBackendError):
        await c.provision(backend_id="native")


async def test_provision_compute_already_provisioned_backend_raises():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1")
    await c.provision_and_wait()

    with pytest.raises(BackendAlreadyProvisionedError):
        await c.provision()


async def test_provision_compute_missing_required_field_raises():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry)

    with pytest.raises(InvalidProvisionValuesError):
        await c.provision(values={})


async def test_provision_compute_empty_string_required_field_raises():
    """An empty form input arrives as "", not None - required must reject it."""
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry)

    with pytest.raises(InvalidProvisionValuesError):
        await c.provision(values={"gpu_type_id": ""})


async def test_provision_compute_illegal_select_value_raises():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry)

    with pytest.raises(InvalidProvisionValuesError):
        await c.provision(values={"gpu_type_id": "not-a-real-gpu"})


async def test_provision_compute_non_numeric_number_raises():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry)

    with pytest.raises(InvalidProvisionValuesError):
        await c.provision(values={"gpu_type_id": "fake-gpu", "volume_size_gb": "not-a-number"})


async def test_provision_compute_validation_failure_creates_no_row():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry)

    with pytest.raises(InvalidProvisionValuesError):
        await c.provision(values={})

    assert c.repository.list_all() == []
    assert c.hub.messages == []


async def test_stop_compute_disables_the_backend_without_removing_it():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1")
    row = await c.provision_and_wait()
    c.hub.messages.clear()

    stopped_row = await operations.stop_compute(c.registry, c.repository, c.backend_registry, c.hub, row.id)

    assert stopped_row.status == "stopped"
    assert stopped_row.status_detail == "Stopped by operator"
    assert c.provisioner.stopped == ["RunPod A100"]
    backend = c.backend(row.backend_id)
    assert backend is not None
    assert backend.enabled is False
    assert backend.base_url == "https://fake-worker:8100"  # connection details survive a stop
    assert backend.worker_token == "tok-abc123"
    assert [r["status"] for r in c.hub.rows()] == ["stopped"]


async def test_terminate_compute_clears_connection_and_disables_but_row_survives():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1")
    row = await c.provision_and_wait()

    await operations.terminate_compute(c.registry, c.repository, c.backend_registry, c.jobs, row.id)

    assert c.provisioner.terminated == ["RunPod A100"]
    backend = c.backend("remote-1")
    assert backend is not None  # the backend row survives - it's the durable object
    assert backend.enabled is False
    assert backend.base_url == ""
    assert backend.worker_token == ""
    assert backend.is_configured() is False
    assert c.repository.get_by_id(row.id) is None

    # The same backend can be provisioned into again since its link was
    # removed along with the row.
    second = await c.provision_and_wait()
    assert second.backend_id == "remote-1"
    assert c.backend("remote-1").enabled is True


class _HangingProvisioner(FakeProvisioner):
    """Never finishes on its own - and cleans up when cancelled, as the
    contract asks a provisioner to."""

    def __init__(self):
        super().__init__()
        self.cleaned_up = []

    async def provision(self, request, report):
        await report(ProvisionProgress(stage="creating", message="Requesting pod"))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cleaned_up.append(request.profile_name)
            raise
        raise AssertionError("unreachable")


async def test_terminate_compute_while_provisioning_cancels_the_job():
    c = Collaborators(_HangingProvisioner())
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1")
    row = await c.provision()
    await asyncio.sleep(0)  # let the job reach its first report
    assert c.repository.get_by_id(row.id).progress[0]["stage"] == "creating"

    await operations.terminate_compute(c.registry, c.repository, c.backend_registry, c.jobs, row.id)

    assert not c.jobs.is_running(row.id)
    assert c.provisioner.cleaned_up == ["RunPod A100"]  # the provisioner tore its own half-built pod down
    assert c.provisioner.terminated == []  # no handle yet, so core had nothing to name
    assert c.repository.get_by_id(row.id) is None
    assert c.backend("remote-1").enabled is False


async def test_terminate_compute_failed_row_without_handle_skips_the_provider():
    c = Collaborators(_FailingProvisioner())
    await _seed_remote_backend(c.backend_registry)
    row = await c.provision_and_wait()

    await operations.terminate_compute(c.registry, c.repository, c.backend_registry, c.jobs, row.id)

    assert c.provisioner.terminated == []
    assert c.repository.get_by_id(row.id) is None


class _HangingStartProvisioner(FakeProvisioner):
    """`start()` never finishes on its own - for tests that need a row to sit
    in `starting` with a live job behind it."""

    async def start(self, handle, report):
        await report(ProvisionProgress(stage="starting", message="Resuming pod"))
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _FailingStartProvisioner(FakeProvisioner):
    async def start(self, handle, report):
        await report(ProvisionProgress(stage="starting", message="Resuming pod"))
        raise ComputeProvisionerError("RunPod API error 500: pod could not be resumed")


async def _stopped_row(c: Collaborators):
    """A row that was provisioned to `running`, then stopped by the operator -
    the state "Start" is offered from."""
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1")
    row = await c.provision_and_wait()
    await operations.stop_compute(c.registry, c.repository, c.backend_registry, c.hub, row.id)
    c.hub.messages.clear()
    row = c.repository.get_by_id(row.id)
    assert row.status == "stopped"
    assert c.backend().enabled is False
    return row


async def _start(c: Collaborators, row_id):
    return await operations.start_compute(
        c.registry, c.repository, c.backend_registry, c.hub, c.jobs, row_id
    )


async def _start_and_wait(c: Collaborators, row_id):
    await _start(c, row_id)
    await c.jobs.wait(row_id)
    return c.repository.get_by_id(row_id)


async def test_start_compute_returns_a_starting_row_with_a_fresh_timeline_at_once():
    c = Collaborators()
    row = await _stopped_row(c)
    assert row.progress != []  # the original bring-up's timeline is still there

    started = await _start(c, row.id)

    assert started.status == "starting"
    assert started.status_detail == "Starting"
    assert started.progress == []
    assert started.handle == "RunPod A100"  # the handle survives - it is what start() is given
    assert c.jobs.is_running(row.id)
    assert c.backend().enabled is False  # nothing to route to until the job finishes
    assert [r["status"] for r in c.hub.rows()] == ["starting"]
    await c.jobs.wait(row.id)


async def test_start_job_streams_progress_then_reconnects_and_enables_the_backend():
    c = Collaborators()
    row = await _stopped_row(c)

    fresh = await _start_and_wait(c, row.id)

    assert c.provisioner.started == ["RunPod A100"]
    assert fresh.status == "running"
    assert fresh.status_detail == "Worker is up"
    assert fresh.status_checked_at is not None
    assert [e["stage"] for e in fresh.progress] == ["starting", "waiting_worker", "ready"]

    backend = c.backend()
    assert backend.enabled is True  # an explicit operator start re-enables, unlike the monitor
    assert backend.base_url == "https://fake-worker-restarted:8100"  # re-read from start(), not kept
    assert backend.worker_token == "tok-restarted"

    statuses = [(r["status"], len(r["progress"])) for r in c.hub.rows()]
    assert statuses == [("starting", 0), ("starting", 1), ("starting", 2), ("starting", 3), ("running", 3)]
    assert not c.jobs.is_running(row.id)


async def test_start_job_not_ready_lands_as_unreachable_but_still_enables_the_backend():
    c = Collaborators()
    c.provisioner.start_ready = False
    row = await _stopped_row(c)

    fresh = await _start_and_wait(c, row.id)

    assert fresh.status == "unreachable"
    assert "handshake" in fresh.status_detail
    assert c.backend().enabled is True
    assert c.backend().base_url == "https://fake-worker-restarted:8100"


async def test_start_job_failure_marks_the_row_failed_and_leaves_the_backend_disabled():
    c = Collaborators(_FailingStartProvisioner())
    row = await _stopped_row(c)

    fresh = await _start_and_wait(c, row.id)

    assert fresh.status == "failed"
    assert fresh.status_detail == "RunPod API error 500: pod could not be resumed"
    assert fresh.handle == "RunPod A100"  # the resource still exists - terminate can name it
    assert [e["stage"] for e in fresh.progress] == ["starting"]
    backend = c.backend()
    assert backend.enabled is False
    assert backend.base_url == "https://fake-worker:8100"  # the old details survive, disabled
    assert [r["status"] for r in c.hub.rows()] == ["starting", "starting", "failed"]


@pytest.mark.parametrize("state", ["stopped", "unreachable", "unknown"])
async def test_start_compute_accepts_every_startable_state(state):
    c = Collaborators()
    row = await _stopped_row(c)
    c.repository.update_status(row.id, state, detail="from the monitor")

    fresh = await _start_and_wait(c, row.id)

    assert fresh.status == "running"
    assert c.backend().enabled is True


@pytest.mark.parametrize("state", ["provisioning", "starting", "running", "missing", "failed"])
async def test_start_compute_refuses_a_non_startable_state(state):
    c = Collaborators()
    row = await _stopped_row(c)
    c.repository.update_status(row.id, state)

    with pytest.raises(ComputeNotStartableError):
        await _start(c, row.id)

    assert c.provisioner.started == []
    assert c.repository.get_by_id(row.id).status == state
    assert c.hub.rows() == []


async def test_start_compute_refuses_a_row_without_a_handle():
    c = Collaborators()
    row = c.repository.create(provider_id="fake", handle="", profile_name="p", status="unknown")

    with pytest.raises(ComputeNotStartableError):
        await _start(c, row.id)


async def test_start_compute_unknown_row_raises():
    c = Collaborators()

    with pytest.raises(ProvisionedComputeNotFoundError):
        await _start(c, "nope")


async def test_refresh_status_leaves_a_starting_row_alone():
    c = Collaborators(_HangingStartProvisioner())
    row = await _stopped_row(c)
    await _start(c, row.id)
    await asyncio.sleep(0)
    c.provisioner.status_state = "stopped"

    refreshed = await operations.refresh_status(c.registry, c.repository, c.hub, row.id)

    assert refreshed.status == "starting"
    assert c.repository.get_by_id(row.id).status == "starting"
    await c.jobs.cancel(row.id)


async def test_terminate_compute_while_starting_cancels_the_job_then_tears_down():
    c = Collaborators(_HangingStartProvisioner())
    row = await _stopped_row(c)
    await _start(c, row.id)
    await asyncio.sleep(0)
    assert c.jobs.is_running(row.id)

    await operations.terminate_compute(c.registry, c.repository, c.backend_registry, c.jobs, row.id)

    assert not c.jobs.is_running(row.id)
    assert c.provisioner.terminated == ["RunPod A100"]
    assert c.repository.get_by_id(row.id) is None
    backend = c.backend()
    assert backend.enabled is False
    assert backend.base_url == ""


async def test_provision_compute_refuses_a_backend_whose_row_is_starting():
    c = Collaborators(_HangingStartProvisioner())
    row = await _stopped_row(c)
    await _start(c, row.id)
    await asyncio.sleep(0)

    with pytest.raises(BackendAlreadyProvisionedError, match="brought up"):
        await c.provision()

    await c.jobs.cancel(row.id)


async def test_terminate_compute_unknown_row_raises():
    c = Collaborators()

    with pytest.raises(ProvisionedComputeNotFoundError):
        await operations.terminate_compute(c.registry, c.repository, c.backend_registry, c.jobs, "missing-row")


async def test_describe_fields_delegates_to_the_provisioner():
    _, registry, _, _ = _collaborators()

    fields = await operations.describe_fields(registry, "fake")

    assert [f.key for f in fields] == ["gpu_type_id", "network_volume_id", "volume_size_gb"]


async def test_describe_fields_resolves_dependent_field_options_from_values():
    _, registry, _, _ = _collaborators()

    without_gpu = await operations.describe_fields(registry, "fake", {})
    with_gpu = await operations.describe_fields(registry, "fake", {"gpu_type_id": "fake-gpu"})

    volume_field_without = next(f for f in without_gpu if f.key == "network_volume_id")
    volume_field_with = next(f for f in with_gpu if f.key == "network_volume_id")

    assert volume_field_without.depends_on == ["gpu_type_id"]
    assert volume_field_without.options == []
    assert [o.value for o in volume_field_with.options] == ["vol-fake-gpu-1"]


async def test_provision_compute_validates_against_value_resolved_options():
    """A select's legal option set is resolved WITH the submitted values, not
    a fixed list - an option that only exists once a dependency is filled in
    must validate, not be rejected against the empty-values option set."""
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry)

    row = await c.provision_and_wait(values={"gpu_type_id": "fake-gpu", "network_volume_id": "vol-fake-gpu-1"})

    assert row.backend_id == "remote-1"


async def test_refresh_status_updates_the_row_from_the_provisioner_and_broadcasts():
    c = Collaborators()
    await _seed_remote_backend(c.backend_registry, backend_id="remote-1")
    row = await c.provision_and_wait()
    c.provisioner.status_state = "unreachable"
    c.provisioner.status_detail = "Pod pod-1 RUNNING but the worker handshake failed"
    c.hub.messages.clear()
    before = row.status_checked_at

    refreshed = await operations.refresh_status(c.registry, c.repository, c.hub, row.id)

    assert refreshed.status == "unreachable"
    assert refreshed.status_detail == "Pod pod-1 RUNNING but the worker handshake failed"
    assert refreshed.status_checked_at >= before
    assert [r["status"] for r in c.hub.rows()] == ["unreachable"]


async def test_refresh_status_leaves_a_provisioning_row_alone():
    c = Collaborators(_HangingProvisioner())
    await _seed_remote_backend(c.backend_registry)
    row = await c.provision()
    c.provisioner.status_state = "missing"

    refreshed = await operations.refresh_status(c.registry, c.repository, c.hub, row.id)

    assert refreshed.status == "provisioning"
    await c.jobs.cancel(row.id)


async def test_broadcast_failure_never_breaks_the_job():
    class DeadHub:
        async def broadcast(self, message):
            raise ConnectionError("socket gone")

    c = Collaborators()
    c.jobs = ComputeProvisioningJobs(c.registry, c.repository, c.backend_registry, DeadHub())
    await _seed_remote_backend(c.backend_registry)

    row = await c.provision_and_wait()

    assert row.status == "running"
    assert c.backend().enabled is True
