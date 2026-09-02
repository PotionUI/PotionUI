"""Mutations for provisioned compute: provision/refresh/stop/start/terminate.

Plain functions over a `ComputeProvisionerRegistry` + `ProvisionedComputeRepository`
+ `BackendRegistry` - no manager/service class. `provision_compute` fills in
an EXISTING `native.remote` backend row (created ahead of time, unconfigured)
rather than minting a new one - the backend is the durable, user-facing
object; provisioning just connects it.

The bring-up itself runs in the background: `provision_compute` returns a
`provisioning` row at once and `ComputeProvisioningJobs` drives the
provisioner's `provision()` in an asyncio task, streaming every reported
step onto the row and out over the admin WebSocket as `compute_status`.
`start_compute` does the same for a stopped resource through the
provisioner's `start()`, with the row in `starting`.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.features.backends.backend_config import NATIVE_REMOTE_DRIVER
from src.features.backends.backend_registry import BackendRegistry
from src.features.provisioning.contracts import (
    STATE_FAILED,
    STATE_PROVISIONING,
    STATE_RUNNING,
    STATE_STARTING,
    STATE_STOPPED,
    STATE_UNKNOWN,
    STATE_UNREACHABLE,
    ComputeFieldDescriptorV1,
    ComputeProvisioner,
    ProgressReporter,
    ProvisionProgress,
    ProvisionRequest,
    ProvisionResult,
)
from src.features.provisioning.records import ProvisionedCompute
from src.features.provisioning.registry import ComputeProvisionerRegistry
from src.features.provisioning.repository import ProvisionedComputeRepository

logger = logging.getLogger(__name__)

#: The one admin WebSocket message this feature emits. `row` is the whole
#: `ProvisionedCompute.to_dict()` every time - the client replaces, never merges.
COMPUTE_STATUS_MESSAGE_TYPE = "compute_status"

#: States `start_compute` accepts: the resource exists on the provider and
#: is not (as far as core knows) already up. `starting`/`provisioning` have a
#: job running; `running` needs nothing; `missing` has nothing to start;
#: `failed` never got a handle (or the monitor will move it to `stopped` /
#: `unreachable` on its next tick, at which point it becomes startable).
STARTABLE_STATES = frozenset({STATE_STOPPED, STATE_UNREACHABLE, STATE_UNKNOWN})

#: States with a background job driving the row - the monitor and on-demand
#: refresh leave these alone while the job runs.
BRING_UP_STATES = frozenset({STATE_PROVISIONING, STATE_STARTING})


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
    live `ProvisionedCompute` row (anything but `failed`) - re-provisioning
    must go through that row (stop/terminate it first), not create a second
    link onto the same backend."""


class ComputeNotStartableError(ValueError):
    """Raised when `start_compute` is asked to start a row that is not in one
    of `STARTABLE_STATES` (already running, still being brought up, gone on
    the provider, or never provisioned)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


async def broadcast_compute_status(hub, row: ProvisionedCompute) -> None:
    """Push the whole row to every admin socket. Never raises: a dead socket
    must not turn a finished bring-up or a status tick into a failure."""
    try:
        await hub.broadcast({"type": COMPUTE_STATUS_MESSAGE_TYPE, "row": row.to_dict()})
    except Exception as exc:
        logger.warning("Failed to broadcast %s for row %s: %s", COMPUTE_STATUS_MESSAGE_TYPE, row.id, exc)


async def disable_backend(backend_registry: BackendRegistry, backend_id: Optional[str]) -> bool:
    """Disable (never remove) a linked backend so it stops being selected for
    new generations - the rule a stopped/paused/vanished pod triggers, whether
    the operator stopped it here or in the provider's own console."""
    if not backend_id:
        return False
    backend = backend_registry.backend_config_store.get_backend(backend_id)
    if backend is None or not backend.enabled:
        return False
    backend.enabled = False
    await backend_registry.update_backend(backend_id, backend)
    return True


class ComputeProvisioningJobs:
    """Owns the background bring-up task per `ProvisionedCompute` row.

    One process singleton (built in the container). `start()` schedules the
    provisioner's `provision()`, `resume()` its `start()`; either task writes
    progress and the outcome onto the row and broadcasts each change.
    `cancel()` is what terminating a `provisioning`/`starting` row does - the
    provisioner sees `CancelledError` and cleans up its own half-built
    resource.
    """

    def __init__(
        self,
        registry: ComputeProvisionerRegistry,
        repository: ProvisionedComputeRepository,
        backend_registry: BackendRegistry,
        hub,
    ):
        self._registry = registry
        self._repository = repository
        self._backend_registry = backend_registry
        self._hub = hub
        self._tasks: Dict[str, asyncio.Task] = {}

    def is_running(self, row_id: str) -> bool:
        task = self._tasks.get(row_id)
        return task is not None and not task.done()

    def start(self, row_id: str, provisioner: ComputeProvisioner, request: ProvisionRequest) -> asyncio.Task:
        return self._launch(row_id, lambda report: provisioner.provision(request, report), "Provisioning")

    def resume(self, row_id: str, provisioner: ComputeProvisioner, handle: str) -> asyncio.Task:
        return self._launch(row_id, lambda report: provisioner.start(handle, report), "Starting")

    def _launch(
        self,
        row_id: str,
        run: Callable[[ProgressReporter], Awaitable[ProvisionResult]],
        verb: str,
    ) -> asyncio.Task:
        task = asyncio.create_task(self._run(row_id, run, verb))
        self._tasks[row_id] = task
        task.add_done_callback(lambda done: self._forget(row_id, done))
        return task

    def _forget(self, row_id: str, task: asyncio.Task) -> None:
        if self._tasks.get(row_id) is task:
            del self._tasks[row_id]

    async def cancel(self, row_id: str) -> bool:
        task = self._tasks.get(row_id)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Provisioning job for row %s raised while being cancelled: %s", row_id, exc)
        return True

    async def wait(self, row_id: str) -> None:
        """Block until the row's job has finished - for tests and shutdown."""
        task = self._tasks.get(row_id)
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise

    async def _report(self, row_id: str, progress: ProvisionProgress) -> None:
        entry = {
            "stage": progress.stage,
            "message": progress.message,
            "percent": progress.percent,
            "at": _now().isoformat(),
        }
        try:
            self._repository.append_progress(row_id, entry)
        except Exception as exc:
            logger.warning("Failed to record provisioning progress for row %s: %s", row_id, exc)
            return
        row = self._repository.get_by_id(row_id)
        if row is not None:
            await broadcast_compute_status(self._hub, row)

    async def _run(
        self,
        row_id: str,
        run: Callable[[ProgressReporter], Awaitable[ProvisionResult]],
        verb: str,
    ) -> None:
        async def report(progress: ProvisionProgress) -> None:
            await self._report(row_id, progress)

        try:
            result = await run(report)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("%s row %s failed: %s", verb, row_id, exc, exc_info=True)
            await self._finish(row_id, STATE_FAILED, str(exc) or type(exc).__name__)
            return

        await self._connect(row_id, result)

    async def _connect(self, row_id: str, result: ProvisionResult) -> None:
        """The shared tail of a bring-up: record the handle, write the
        connection details onto the linked backend and enable it, then land
        the row on `running`/`unreachable` by `result.ready`."""
        row = self._repository.get_by_id(row_id)
        if row is None:
            return
        self._repository.update_handle(row_id, result.handle, result.resource_ref)

        backend = self._backend_registry.backend_config_store.get_backend(row.backend_id) if row.backend_id else None
        if backend is None:
            await self._finish(
                row_id, STATE_FAILED,
                "The target backend was deleted while its compute was being brought up",
            )
            return

        backend.base_url = result.base_url
        backend.worker_token = result.worker_token
        backend.enabled = True
        await self._backend_registry.update_backend(row.backend_id, backend)

        if result.ready:
            await self._finish(row_id, STATE_RUNNING, "Worker is up", checked=True)
        else:
            await self._finish(
                row_id, STATE_UNREACHABLE,
                "Compute is up but the worker did not answer the handshake yet",
                checked=True,
            )

    async def _finish(self, row_id: str, status: str, detail: str, *, checked: bool = False) -> None:
        self._repository.update_status(row_id, status, detail=detail, checked_at=_now() if checked else None)
        row = self._repository.get_by_id(row_id)
        if row is not None:
            await broadcast_compute_status(self._hub, row)


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
    jobs: ComputeProvisioningJobs,
    *,
    provider_id: str,
    backend_id: str,
    values: Dict[str, Any],
    profile_name: Optional[str] = None,
    created_by: Optional[str] = None,
) -> ProvisionedCompute:
    """Start filling an existing `native.remote` backend (`backend_id`)
    through `provider_id`: validate `values` against the provider's own field
    descriptors - resolved WITH those same `values`, so a dependent field's
    options are checked against the right set - create the row as
    `provisioning`, link it to the backend, and hand the bring-up to `jobs`.
    Returns at once; the job writes the connection details onto the backend
    (enabling it) when the provisioner is done. `profile_name` defaults to
    the target backend's name.

    A backend whose row is `failed` may be provisioned again: the failed row
    is replaced (deleted, then recreated) so the timeline starts clean. Any
    other linked row - `provisioning` included - refuses.
    """
    provisioner = _get_provisioner(registry, provider_id)
    backend = _get_target_backend(backend_registry, backend_id)
    existing = repository.get_by_backend_id(backend_id)
    if existing is not None:
        if existing.status in BRING_UP_STATES:
            raise BackendAlreadyProvisionedError(
                f"Backend '{backend_id}' is already being brought up - wait for it to finish or terminate it"
            )
        if existing.status != STATE_FAILED:
            raise BackendAlreadyProvisionedError(
                f"Backend '{backend_id}' is already linked to provisioned compute - "
                "stop or terminate it before provisioning again"
            )

    descriptors = await provisioner.describe_fields(values)
    _validate_values(descriptors, values)

    if existing is not None:
        repository.delete(existing.id)

    resolved_profile_name = profile_name or backend.name
    row = repository.create(
        provider_id=provider_id,
        handle="",
        profile_name=resolved_profile_name,
        status=STATE_PROVISIONING,
        status_detail="Starting",
        backend_id=backend_id,
        gpu_type_id=values.get("gpu_type_id"),
        region=values.get("region"),
        created_by=created_by,
    )
    jobs.start(row.id, provisioner, ProvisionRequest(profile_name=resolved_profile_name, values=values))
    return row


async def refresh_status(
    registry: ComputeProvisionerRegistry,
    repository: ProvisionedComputeRepository,
    hub,
    row_id: str,
) -> ProvisionedCompute:
    """On-demand reconcile against the provider. A row still `provisioning`
    or `starting` (its job owns the row until it lands) or one that never got
    a handle has nothing to ask the provider about and comes back as is."""
    row = _get_row(repository, row_id)
    if row.status in BRING_UP_STATES or not row.handle:
        return row
    provisioner = _get_provisioner(registry, row.provider_id)
    status = await provisioner.status(row.handle)
    repository.update_status(row_id, status.state, detail=status.detail, checked_at=_now())
    row = _get_row(repository, row_id)
    await broadcast_compute_status(hub, row)
    return row


async def stop_compute(
    registry: ComputeProvisionerRegistry,
    repository: ProvisionedComputeRepository,
    backend_registry: BackendRegistry,
    hub,
    row_id: str,
) -> ProvisionedCompute:
    """Stop the underlying resource and disable (never remove) its linked
    backend row - a stopped pod's backend must stop being selected for new
    generations without losing its configuration."""
    row = _get_row(repository, row_id)
    provisioner = _get_provisioner(registry, row.provider_id)
    await provisioner.stop(row.handle)
    repository.update_status(row_id, STATE_STOPPED, detail="Stopped by operator", checked_at=_now())

    await disable_backend(backend_registry, row.backend_id)

    row = _get_row(repository, row_id)
    await broadcast_compute_status(hub, row)
    return row


async def start_compute(
    registry: ComputeProvisionerRegistry,
    repository: ProvisionedComputeRepository,
    backend_registry: BackendRegistry,
    hub,
    jobs: ComputeProvisioningJobs,
    row_id: str,
) -> ProvisionedCompute:
    """Bring a stopped resource back: the row goes `starting` with a fresh
    timeline, the provisioner's `start()` runs in the background through
    `jobs`, and on success the (possibly new) connection details are written
    onto the linked backend and it is ENABLED - an explicit operator start,
    unlike the monitor, which never re-enables on its own. On failure the row
    lands on `failed` with the message and the backend stays disabled.

    Only a row in `STARTABLE_STATES` qualifies; anything else raises
    `ComputeNotStartableError` (409 at the route).
    """
    row = _get_row(repository, row_id)
    provisioner = _get_provisioner(registry, row.provider_id)
    if row.status not in STARTABLE_STATES or not row.handle:
        raise ComputeNotStartableError(
            f"Provisioned compute '{row_id}' is '{row.status}' - only stopped, unreachable "
            "or unknown compute can be started"
        )

    repository.clear_progress(row_id)
    repository.update_status(row_id, STATE_STARTING, detail="Starting")
    row = _get_row(repository, row_id)
    await broadcast_compute_status(hub, row)
    jobs.resume(row.id, provisioner, row.handle)
    return row


async def terminate_compute(
    registry: ComputeProvisionerRegistry,
    repository: ProvisionedComputeRepository,
    backend_registry: BackendRegistry,
    jobs: ComputeProvisioningJobs,
    row_id: str,
) -> None:
    """Tear the resource down and delete the `ProvisionedCompute` row - the
    one irreversible step in this lifecycle. The linked backend row survives:
    it is the durable, user-facing object, so terminating only clears its
    connection details (base_url/worker_token) and disables it, returning it
    to "not configured" so the same row can be provisioned into again later.

    Terminating a row still `provisioning`/`starting` is how a bring-up is
    cancelled: the job is cancelled first (the provisioner cleans up what it
    created on `CancelledError`), and a row that never got a handle skips the
    provider's `terminate` - there is nothing it could name.
    """
    row = _get_row(repository, row_id)
    provisioner = _get_provisioner(registry, row.provider_id)
    await jobs.cancel(row_id)
    row = _get_row(repository, row_id)
    if row.handle:
        await provisioner.terminate(row.handle)

    if row.backend_id:
        backend = backend_registry.backend_config_store.get_backend(row.backend_id)
        if backend is not None:
            backend.base_url = ""
            backend.worker_token = ""
            backend.enabled = False
            await backend_registry.update_backend(row.backend_id, backend)

    repository.delete(row_id)
