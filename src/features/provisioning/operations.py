"""Mutations for provisioned compute: provision/refresh/stop/terminate.

Plain functions over a `ComputeProvisionerRegistry` + `ProvisionedComputeRepository`
+ `BackendRegistry` - no manager/service class. `provision_compute` fills in
an EXISTING `native.remote` backend row (created ahead of time, unconfigured)
rather than minting a new one - the backend is the durable, user-facing
object; provisioning just connects it.
"""

from typing import Any, Dict, List, Optional

from src.features.backends.backend_config import NATIVE_REMOTE_DRIVER
from src.features.backends.backend_registry import BackendRegistry
from src.features.provisioning.contracts import (
    ComputeFieldDescriptorV1,
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


class InvalidProvisionValuesError(ValueError):
    """Raised when a provision request's `values` fail validation against the
    provider's own `describe_fields()` descriptors - a missing required
    field, a non-numeric number, or a select value outside its options."""


class BackendNotFoundError(ValueError):
    """Raised when `provision_compute`'s target `backend_id` names no backend."""


class NotARemoteBackendError(ValueError):
    """Raised when `provision_compute`'s target backend is not a `native.remote`
    backend - provisioning only ever fills an existing remote-worker row."""


class BackendAlreadyProvisionedError(ValueError):
    """Raised when `provision_compute`'s target backend is already linked to a
    `ProvisionedCompute` row - re-provisioning must go through that row
    (stop/terminate it first), not create a second link onto the same backend."""


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


def _get_target_backend(backend_registry: BackendRegistry, backend_id: str):
    backend = backend_registry.backend_config_store.get_backend(backend_id)
    if backend is None:
        raise BackendNotFoundError(f"No backend with id '{backend_id}'")
    if backend.driver != NATIVE_REMOTE_DRIVER:
        raise NotARemoteBackendError(
            f"Backend '{backend_id}' is a '{backend.driver}' backend, not a "
            "native.remote backend - provisioning only fills an existing "
            "native.remote backend"
        )
    return backend


def _validate_values(descriptors: List[ComputeFieldDescriptorV1], values: Dict[str, Any]) -> None:
    for descriptor in descriptors:
        value = values.get(descriptor.key)
        # "" counts as absent: an empty form input arrives as an empty string,
        # not None, and a required field must reject both.
        if value is None or value == "":
            if descriptor.required:
                raise InvalidProvisionValuesError(f"'{descriptor.key}' is required")
            continue

        if descriptor.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidProvisionValuesError(f"'{descriptor.key}' must be a number")
        elif descriptor.type == "select":
            legal = {option.value for option in (descriptor.options or [])}
            if value not in legal:
                raise InvalidProvisionValuesError(
                    f"'{descriptor.key}' must be one of {sorted(legal)}"
                )


def list_providers(registry: ComputeProvisionerRegistry) -> List[ComputeProvisioner]:
    return registry.list_provisioners()


async def describe_fields(
    registry: ComputeProvisionerRegistry, provider_id: str, values: Optional[Dict[str, Any]] = None
) -> List[ComputeFieldDescriptorV1]:
    provisioner = _get_provisioner(registry, provider_id)
    return await provisioner.describe_fields(values)


async def provision_compute(
    registry: ComputeProvisionerRegistry,
    repository: ProvisionedComputeRepository,
    backend_registry: BackendRegistry,
    *,
    provider_id: str,
    backend_id: str,
    values: Dict[str, Any],
    profile_name: Optional[str] = None,
    created_by: Optional[str] = None,
) -> ProvisionedCompute:
    """Fill an existing `native.remote` backend (`backend_id`) through
    `provider_id`: validate `values` against the provider's own field
    descriptors - resolved WITH those same `values`, so a dependent field's
    options are checked against the right set - provision through it, then
    write the connection details it hands back onto the target backend row
    (enabling it) and link the two. `profile_name` defaults to the target
    backend's name.
    """
    provisioner = _get_provisioner(registry, provider_id)
    backend = _get_target_backend(backend_registry, backend_id)
    if repository.get_by_backend_id(backend_id) is not None:
        raise BackendAlreadyProvisionedError(
            f"Backend '{backend_id}' is already linked to provisioned compute - "
            "stop or terminate it before provisioning again"
        )

    descriptors = await provisioner.describe_fields(values)
    _validate_values(descriptors, values)

    resolved_profile_name = profile_name or backend.name
    result = await provisioner.provision(
        ProvisionRequest(profile_name=resolved_profile_name, values=values)
    )

    backend.base_url = result.base_url
    backend.worker_token = result.worker_token
    backend.enabled = True
    await backend_registry.update_backend(backend_id, backend)

    return repository.create(
        provider_id=provider_id,
        handle=result.handle,
        profile_name=resolved_profile_name,
        status="running" if result.ready else "unreachable",
        backend_id=backend_id,
        resource_ref=result.resource_ref,
        gpu_type_id=values.get("gpu_type_id"),
        region=values.get("region"),
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
    """Tear the resource down and delete the `ProvisionedCompute` row - the
    one irreversible step in this lifecycle. The linked backend row survives:
    it is the durable, user-facing object, so terminating only clears its
    connection details (base_url/worker_token) and disables it, returning it
    to "not configured" so the same row can be provisioned into again later.
    """
    row = _get_row(repository, row_id)
    provisioner = _get_provisioner(registry, row.provider_id)
    await provisioner.terminate(row.handle)

    if row.backend_id:
        backend = backend_registry.backend_config_store.get_backend(row.backend_id)
        if backend is not None:
            backend.base_url = ""
            backend.worker_token = ""
            backend.enabled = False
            await backend_registry.update_backend(row.backend_id, backend)

    repository.delete(row_id)
