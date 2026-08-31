"""Mutations for provisioned compute: provision/refresh/stop/terminate.

Plain functions over a `ComputeProvisionerRegistry` + `ProvisionedComputeRepository`
+ `BackendRegistry` - no manager/service class. `provision_compute` is the one
seam that used to be manual (see `docs/remote-native.md` and the
`runpod-provider` plugin's former README): a successful provision now creates
and enables the `native.remote` backend row itself, through the same
`BackendRegistry.add_backend` an admin's "create backend" form would call, and
links it on the `ProvisionedCompute` row it returns.
"""

from typing import List, Optional

from src.features.backends.backend_config import NativeRemoteBackendConfig
from src.features.backends.backend_registry import BackendRegistry
from src.platform.util.ids import generate_ulid
from src.features.provisioning.contracts import (
    ComputeGpuType,
    ComputeProvisioner,
    ProvisionRequest,
)
from src.features.provisioning.records import ProvisionedCompute
from src.features.provisioning.registry import ComputeProvisionerRegistry
from src.features.provisioning.repository import ProvisionedComputeRepository


class UnknownProviderError(ValueError):
    """Raised when `provider_id` names no registered `ComputeProvisioner`."""


class ProvisionedComputeNotFoundError(ValueError):
    """Raised when a `ProvisionedCompute` row id does not exist."""


def _get_provisioner(registry: ComputeProvisionerRegistry, provider_id: str) -> ComputeProvisioner:
    provisioner = registry.get(provider_id)
    if provisioner is None:
        raise UnknownProviderError(f"Unknown compute provider '{provider_id}'")
    return provisioner


def _get_row(repository: ProvisionedComputeRepository, row_id: str) -> ProvisionedCompute:
    row = repository.get_by_id(row_id)
    if row is None:
        raise ProvisionedComputeNotFoundError(f"No provisioned compute with id '{row_id}'")
    return row


def list_providers(registry: ComputeProvisionerRegistry) -> List[ComputeProvisioner]:
    return registry.list_provisioners()


async def list_gpu_types(registry: ComputeProvisionerRegistry, provider_id: str) -> List[ComputeGpuType]:
    provisioner = _get_provisioner(registry, provider_id)
    return await provisioner.list_gpu_types()


async def provision_compute(
    registry: ComputeProvisionerRegistry,
    repository: ProvisionedComputeRepository,
    backend_registry: BackendRegistry,
    *,
    provider_id: str,
    request: ProvisionRequest,
    backend_name: Optional[str] = None,
    created_by: Optional[str] = None,
) -> ProvisionedCompute:
    """Provision through `provider_id`, then create+enable a `native.remote`
    backend row from the connection details it hands back, and link the two."""
    provisioner = _get_provisioner(registry, provider_id)
    result = await provisioner.provision(request)

    backend_id = f"remote-{generate_ulid()}"
    backend_config = NativeRemoteBackendConfig(
        id=backend_id,
        name=backend_name or f"{provisioner.label or provider_id}: {request.profile_name}",
        base_url=result.base_url,
        worker_token=result.worker_token,
        enabled=True,
    )
    await backend_registry.add_backend(backend_config)

    return repository.create(
        provider_id=provider_id,
        handle=result.handle,
        profile_name=request.profile_name,
        status="running" if result.ready else "unreachable",
        backend_id=backend_id,
        resource_ref=result.resource_ref,
        gpu_type_id=request.gpu_type_id,
        region=request.region,
        created_by=created_by,
    )


async def refresh_status(
    registry: ComputeProvisionerRegistry,
    repository: ProvisionedComputeRepository,
    row_id: str,
) -> ProvisionedCompute:
    row = _get_row(repository, row_id)
    provisioner = _get_provisioner(registry, row.provider_id)
    status = await provisioner.status(row.handle)
    repository.update_status(row_id, status.state)
    return _get_row(repository, row_id)


async def stop_compute(
    registry: ComputeProvisionerRegistry,
    repository: ProvisionedComputeRepository,
    backend_registry: BackendRegistry,
    row_id: str,
) -> ProvisionedCompute:
    """Stop the underlying resource and disable (never remove) its linked
    backend row - a stopped pod's backend must stop being selected for new
    generations without losing its configuration."""
    row = _get_row(repository, row_id)
    provisioner = _get_provisioner(registry, row.provider_id)
    await provisioner.stop(row.handle)
    repository.update_status(row_id, "stopped")

    if row.backend_id:
        backend = backend_registry.backend_config_store.get_backend(row.backend_id)
        if backend is not None:
            backend.enabled = False
            await backend_registry.update_backend(row.backend_id, backend)

    return _get_row(repository, row_id)


async def terminate_compute(
    registry: ComputeProvisionerRegistry,
    repository: ProvisionedComputeRepository,
    backend_registry: BackendRegistry,
    row_id: str,
) -> None:
    """Tear the resource down, remove its linked backend row, and delete the
    `ProvisionedCompute` row - the one irreversible step in this lifecycle."""
    row = _get_row(repository, row_id)
    provisioner = _get_provisioner(registry, row.provider_id)
    await provisioner.terminate(row.handle)

    if row.backend_id:
        try:
            await backend_registry.remove_backend(row.backend_id)
        except ValueError:
            pass  # Already removed by hand - not this call's problem.

    repository.delete(row_id)
