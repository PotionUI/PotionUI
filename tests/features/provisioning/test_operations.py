"""`src.features.provisioning.operations` against a fake `ComputeProvisioner`,
a fake in-memory repository, and a fake `BackendRegistry` - no network, no DB.

Proves the seam this feature owns: provisioning fills an EXISTING
`native.remote` backend row (created ahead of time, unconfigured, the same
way any other backend is created) rather than minting a new one, and links it
on the `ProvisionedCompute` row it returns. Stopping disables that backend
without touching its connection details; terminating clears the connection
details and disables it, but the row itself survives so it can be provisioned
into again later.
"""

import pytest

from src.features.backends.backend_config import NativeBackendConfig, NativeRemoteBackendConfig
from src.features.provisioning import operations
from src.features.provisioning.contracts import (
    ComputeFieldDescriptorV1,
    ComputeFieldOptionV1,
    ComputeProvisioner,
    ComputeStatus,
    ProvisionRequest,
    ProvisionResult,
)
from src.features.provisioning.operations import (
    BackendAlreadyProvisionedError,
    BackendNotFoundError,
    InvalidProvisionValuesError,
    NotARemoteBackendError,
    ProvisionedComputeNotFoundError,
    UnknownProviderError,
)
from src.features.provisioning.records import ProvisionedCompute

# Options for the dependent `network_volume_id` field, keyed by the
# `gpu_type_id` value it `depends_on` - proves describe_fields(values) really
# resolves against the submitted values, not a fixed list.
_NETWORK_VOLUME_OPTIONS_BY_GPU = {
    "fake-gpu": [ComputeFieldOptionV1(value="vol-fake-gpu-1", label="1x NVMe (fake-gpu)")],
}


class FakeProvisioner(ComputeProvisioner):
    provider_id = "fake"
    label = "Fake Provider"

    def __init__(self):
        self.provisioned = []
        self.stopped = []
        self.terminated = []
        self.status_state = "running"

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

    def get_by_backend_id(self, backend_id):
        for row in self._rows.values():
            if row.backend_id == backend_id:
                return row
        return None

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


def _collaborators(provisioner=None):
    provisioner = provisioner or FakeProvisioner()
    return provisioner, FakeProvisionerRegistry([provisioner]), FakeRepository(), FakeBackendRegistry()


async def _seed_remote_backend(backend_registry, *, backend_id="remote-1", name="RunPod A100"):
    """Add an unconfigured `native.remote` backend to `backend_registry`, the
    way the admin "create backend" form would - `provision_compute` expects
    its target to already exist."""
    config = NativeRemoteBackendConfig(id=backend_id, name=name, enabled=False)
    await backend_registry.add_backend(config)
    return config


async def test_provision_compute_fills_the_existing_backend_and_links_it():
    provisioner, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry, backend_id="remote-1", name="RunPod A100")

    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake",
        backend_id="remote-1",
        profile_name="prof-1",
        values={"gpu_type_id": "fake-gpu"},
        created_by="user-1",
    )

    assert row.handle == "prof-1"
    assert row.status == "running"
    assert row.backend_id == "remote-1"
    assert row.gpu_type_id == "fake-gpu"

    backend = backend_registry.backend_config_store.get_backend("remote-1")
    assert backend is not None
    assert backend.name == "RunPod A100"  # untouched - provisioning fills, doesn't rename
    assert backend.enabled is True
    assert backend.base_url == "https://fake-worker:8100"
    assert backend.worker_token == "tok-abc123"


async def test_provision_compute_profile_name_defaults_to_backend_name():
    provisioner, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry, backend_id="remote-1", name="RunPod A100")

    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", backend_id="remote-1", values={"gpu_type_id": "fake-gpu"},
    )

    assert row.profile_name == "RunPod A100"
    assert provisioner.provisioned[0].profile_name == "RunPod A100"


async def test_provision_compute_unknown_provider_raises():
    _, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry)

    with pytest.raises(UnknownProviderError):
        await operations.provision_compute(
            registry, repository, backend_registry,
            provider_id="missing", backend_id="remote-1", values={},
        )


async def test_provision_compute_unknown_backend_raises():
    _, registry, repository, backend_registry = _collaborators()

    with pytest.raises(BackendNotFoundError):
        await operations.provision_compute(
            registry, repository, backend_registry,
            provider_id="fake", backend_id="does-not-exist", values={"gpu_type_id": "fake-gpu"},
        )


async def test_provision_compute_non_remote_backend_raises():
    _, registry, repository, backend_registry = _collaborators()
    await backend_registry.add_backend(NativeBackendConfig(id="native", name="Local Generation"))

    with pytest.raises(NotARemoteBackendError):
        await operations.provision_compute(
            registry, repository, backend_registry,
            provider_id="fake", backend_id="native", values={"gpu_type_id": "fake-gpu"},
        )


async def test_provision_compute_already_provisioned_backend_raises():
    provisioner, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry, backend_id="remote-1")
    await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", backend_id="remote-1", values={"gpu_type_id": "fake-gpu"},
    )

    with pytest.raises(BackendAlreadyProvisionedError):
        await operations.provision_compute(
            registry, repository, backend_registry,
            provider_id="fake", backend_id="remote-1", values={"gpu_type_id": "fake-gpu"},
        )


async def test_provision_compute_missing_required_field_raises():
    _, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry)

    with pytest.raises(InvalidProvisionValuesError):
        await operations.provision_compute(
            registry, repository, backend_registry,
            provider_id="fake", backend_id="remote-1", values={},
        )


async def test_provision_compute_empty_string_required_field_raises():
    """An empty form input arrives as "", not None - required must reject it."""
    _, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry)

    with pytest.raises(InvalidProvisionValuesError):
        await operations.provision_compute(
            registry, repository, backend_registry,
            provider_id="fake", backend_id="remote-1", values={"gpu_type_id": ""},
        )


async def test_provision_compute_illegal_select_value_raises():
    _, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry)

    with pytest.raises(InvalidProvisionValuesError):
        await operations.provision_compute(
            registry, repository, backend_registry,
            provider_id="fake", backend_id="remote-1",
            values={"gpu_type_id": "not-a-real-gpu"},
        )


async def test_provision_compute_non_numeric_number_raises():
    _, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry)

    with pytest.raises(InvalidProvisionValuesError):
        await operations.provision_compute(
            registry, repository, backend_registry,
            provider_id="fake", backend_id="remote-1",
            values={"gpu_type_id": "fake-gpu", "volume_size_gb": "not-a-number"},
        )


async def test_stop_compute_disables_the_backend_without_removing_it():
    provisioner, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry, backend_id="remote-1")
    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", backend_id="remote-1", values={"gpu_type_id": "fake-gpu"},
    )

    stopped_row = await operations.stop_compute(registry, repository, backend_registry, row.id)

    assert stopped_row.status == "stopped"
    assert provisioner.stopped == ["RunPod A100"]
    backend = backend_registry.backend_config_store.get_backend(row.backend_id)
    assert backend is not None
    assert backend.enabled is False
    assert backend.base_url == "https://fake-worker:8100"  # connection details survive a stop
    assert backend.worker_token == "tok-abc123"


async def test_terminate_compute_clears_connection_and_disables_but_row_survives():
    provisioner, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry, backend_id="remote-1")
    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", backend_id="remote-1", values={"gpu_type_id": "fake-gpu"},
    )

    await operations.terminate_compute(registry, repository, backend_registry, row.id)

    assert provisioner.terminated == ["RunPod A100"]
    backend = backend_registry.backend_config_store.get_backend("remote-1")
    assert backend is not None  # the backend row survives - it's the durable object
    assert backend.enabled is False
    assert backend.base_url == ""
    assert backend.worker_token == ""
    assert backend.is_configured() is False
    assert repository.get_by_id(row.id) is None

    # The same backend can be provisioned into again since its link was
    # removed along with the row.
    second = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", backend_id="remote-1", values={"gpu_type_id": "fake-gpu"},
    )
    assert second.backend_id == "remote-1"
    assert backend_registry.backend_config_store.get_backend("remote-1").enabled is True


async def test_terminate_compute_unknown_row_raises():
    _, registry, repository, backend_registry = _collaborators()

    with pytest.raises(ProvisionedComputeNotFoundError):
        await operations.terminate_compute(registry, repository, backend_registry, "missing-row")


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
    _, registry, repository, backend_registry = _collaborators()
    await _seed_remote_backend(backend_registry)

    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", backend_id="remote-1",
        values={"gpu_type_id": "fake-gpu", "network_volume_id": "vol-fake-gpu-1"},
    )

    assert row.backend_id == "remote-1"


async def test_refresh_status_updates_the_row_from_the_provisioner():
    provisioner, registry, repository, backend_registry = _collaborators()
    provisioner.status_state = "unreachable"
    await _seed_remote_backend(backend_registry, backend_id="remote-1")
    row = await operations.provision_compute(
        registry, repository, backend_registry,
        provider_id="fake", backend_id="remote-1", values={"gpu_type_id": "fake-gpu"},
    )

    refreshed = await operations.refresh_status(registry, repository, row.id)

    assert refreshed.status == "unreachable"
