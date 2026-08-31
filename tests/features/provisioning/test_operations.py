"""`src.features.provisioning.operations` against a fake `ComputeProvisioner`,
a fake in-memory repository, and a fake `BackendRegistry` - no network, no DB.

Proves the one seam this feature adds over the plugin's former manual flow:
a successful provision creates and enables a `native.remote` backend row and
links it on the `ProvisionedCompute` row; stopping disables that backend
without removing it; terminating removes the backend and deletes the row.
"""

import pytest

from src.features.provisioning import operations
from src.features.provisioning.contracts import (
    ComputeGpuType,
    ComputeProvisioner,
    ComputeStatus,
    ProvisionRequest,
    ProvisionResult,
)
from src.features.provisioning.operations import (
    ProvisionedComputeNotFoundError,
    UnknownProviderError,
)
from src.features.provisioning.records import ProvisionedCompute


class FakeProvisioner(ComputeProvisioner):
    provider_id = "fake"
    label = "Fake Provider"

    def __init__(self):
        self.provisioned = []
        self.stopped = []
        self.terminated = []
        self.status_state = "running"

    async def list_gpu_types(self):
        return [ComputeGpuType(id="fake-gpu", memory_gb=24)]

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        self.provisioned.append(request)
        return ProvisionResult(
            handle=request.profile_name,
            base_url="https://fake-worker:8100",
            worker_token="tok-abc123",
            ready=True,
            resource_ref="res-1",
        )

    async def status(self, handle: str) -> ComputeStatus:
        return ComputeStatus(state=self.status_state)

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

    def get_by_id(self, row_id):
        return self._rows.get(row_id)

    def list_all(self):
        return list(self._rows.values())

    def update_status(self, row_id, status):
        row = self._rows.get(row_id)
        if row is None:
            return False
        row.status = status
        return True

    def delete(self, row_id):
        return self._rows.pop(row_id, None) is not None


class FakeBackendConfigStore:
    def __init__(self, backends):
        self._backends = backends

    def get_backend(self, backend_id):
        return self._backends.get(backend_id)


class FakeBackendRegistry:
    """Stands in for `BackendRegistry` - `provision_compute` builds a real
    `NativeRemoteBackendConfig` and hands it here unchanged."""

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


def _collaborators(provisioner=None):
    provisioner = provisioner or FakeProvisioner()
    return provisioner, FakeProvisionerRegistry([provisioner]), FakeRepository(), FakeBackendRegistry()


async def test_provision_compute_creates_and_enables_a_backend_and_links_it():
    provisioner, registry, repository, backend_registry = _collaborators()

    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake",
        request=ProvisionRequest(profile_name="prof-1", gpu_type_id="fake-gpu"),
        created_by="user-1",
    )

    assert row.handle == "prof-1"
    assert row.status == "running"
    assert row.backend_id is not None

    backend = backend_registry.backend_config_store.get_backend(row.backend_id)
    assert backend is not None
    assert backend.enabled is True
    assert backend.base_url == "https://fake-worker:8100"
    assert backend.worker_token == "tok-abc123"


async def test_provision_compute_unknown_provider_raises():
    _, registry, repository, backend_registry = _collaborators()

    with pytest.raises(UnknownProviderError):
        await operations.provision_compute(
            registry, repository, backend_registry,
            provider_id="missing", request=ProvisionRequest(profile_name="p"),
        )


async def test_stop_compute_disables_the_backend_without_removing_it():
    provisioner, registry, repository, backend_registry = _collaborators()
    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", request=ProvisionRequest(profile_name="prof-1"),
    )

    stopped_row = await operations.stop_compute(registry, repository, backend_registry, row.id)

    assert stopped_row.status == "stopped"
    assert provisioner.stopped == ["prof-1"]
    backend = backend_registry.backend_config_store.get_backend(row.backend_id)
    assert backend is not None
    assert backend.enabled is False


async def test_terminate_compute_removes_the_backend_and_deletes_the_row():
    provisioner, registry, repository, backend_registry = _collaborators()
    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", request=ProvisionRequest(profile_name="prof-1"),
    )

    await operations.terminate_compute(registry, repository, backend_registry, row.id)

    assert provisioner.terminated == ["prof-1"]
    assert backend_registry.backend_config_store.get_backend(row.backend_id) is None
    assert repository.get_by_id(row.id) is None


async def test_terminate_compute_unknown_row_raises():
    _, registry, repository, backend_registry = _collaborators()

    with pytest.raises(ProvisionedComputeNotFoundError):
        await operations.terminate_compute(registry, repository, backend_registry, "missing-row")


async def test_list_gpu_types_delegates_to_the_provisioner():
    _, registry, _, _ = _collaborators()

    gpu_types = await operations.list_gpu_types(registry, "fake")

    assert gpu_types == [ComputeGpuType(id="fake-gpu", memory_gb=24)]


async def test_refresh_status_updates_the_row_from_the_provisioner():
    provisioner, registry, repository, backend_registry = _collaborators()
    provisioner.status_state = "unreachable"
    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", request=ProvisionRequest(profile_name="prof-1"),
    )

    refreshed = await operations.refresh_status(registry, repository, row.id)

    assert refreshed.status == "unreachable"
