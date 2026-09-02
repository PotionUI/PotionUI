"""The `ComputeProvisioner` contract: what a plugin implements to let core
provision rented GPU compute.

A provisioner turns a `ProvisionRequest` into a running Remote Native worker
and reports/tears down its lifecycle. It never touches a `native.remote`
backend row - that's core's job (`src.features.provisioning.operations`),
driven by the `handle` a provisioner hands back from `provision()` and
expects unchanged in later `status`/`stop`/`terminate` calls. `handle` is
entirely the provisioner's choice; core only stores and echoes it back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, ClassVar, Dict, List, Optional

#: Every state a `ProvisionedCompute` row can be in. `provisioning` and `failed`
#: are core's own (the background bring-up job sets them); a provisioner's
#: `status()` reports the rest:
#:   running     - the provider says the resource is up AND the worker answers.
#:   stopped     - paused/exited, by the operator or by the provider.
#:   missing     - the provider no longer knows the handle.
#:   unreachable - the provider says running but the worker handshake fails.
#:   failed      - `provision()` raised; the row keeps the message as detail.
#:   unknown     - the provider could not be asked (an API error, a timeout).
STATE_PROVISIONING = "provisioning"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"
STATE_MISSING = "missing"
STATE_UNREACHABLE = "unreachable"
STATE_FAILED = "failed"
STATE_UNKNOWN = "unknown"
COMPUTE_STATES = (
    STATE_PROVISIONING, STATE_RUNNING, STATE_STOPPED, STATE_MISSING,
    STATE_UNREACHABLE, STATE_FAILED, STATE_UNKNOWN,
)

#: Conventional `ProvisionProgress.stage` values, in the order a bring-up
#: usually passes through them. A provisioner may report any string; these
#: are the ones the admin UI has labels for.
STAGE_PREPARING = "preparing"
STAGE_CREATING = "creating"
STAGE_STARTING = "starting"
STAGE_WAITING_WORKER = "waiting_worker"
STAGE_READY = "ready"


class ComputeProvisionerError(RuntimeError):
    """Raised by a `ComputeProvisioner` when the underlying provider call fails
    (a rejected API key, a rate limit, a resource that no longer exists, ...)."""


@dataclass(frozen=True)
class ComputeFieldOptionV1:
    """One choice in a `ComputeFieldDescriptorV1` of type "select"."""
    value: str
    label: str
    detail: Optional[str] = None


@dataclass(frozen=True)
class ComputeFieldDescriptorV1:
    """One field a `ComputeProvisioner.describe_fields()` reports, describing
    its own `provision()` inputs to the admin UI."""
    key: str
    label: str
    type: str  # "text" | "number" | "select"
    required: bool = False
    default: Optional[Any] = None
    help_text: Optional[str] = None
    options: Optional[List[ComputeFieldOptionV1]] = None
    # Keys of other fields this descriptor's options depend on (e.g. a
    # "gpu_type_id" select narrowed once "data_center" is picked). Advisory
    # for the admin UI only - actual resolution happens in
    # describe_fields(values).
    depends_on: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProvisionRequest:
    """What to provision. `profile_name` is the operator-chosen label a
    provisioner may use to identify/reuse its own resources (e.g. a network
    volume) across repeated provisions - it is not core's row id. `values`
    holds the provider's own fields (as described by `describe_fields()`),
    already validated against those descriptors by
    `src.features.provisioning.operations.provision_compute`."""
    profile_name: str
    values: Dict[str, Any]


@dataclass(frozen=True)
class ProvisionResult:
    """What a provisioner hands back after a successful `provision()`.

    `handle` is passed back unchanged to every later `status`/`stop`/
    `terminate` call - it is the provisioner's own identifier for the
    resource, not anything core invents. `base_url`/`worker_token` are what
    core needs to create the `native.remote` backend row.
    """
    handle: str
    base_url: str
    worker_token: str
    ready: bool
    resource_ref: Optional[str] = None


@dataclass(frozen=True)
class ComputeStatus:
    """Reconciled state of a provisioned resource. `state` is one of
    `COMPUTE_STATES`; `detail` is the provider-facing reason behind it
    ("pod EXITED", "handshake failed after 3 attempts"), shown verbatim to
    the admin."""
    state: str
    detail: Optional[str] = None


@dataclass(frozen=True)
class ProvisionProgress:
    """One step of a bring-up, reported by `provision()` through its
    `report` callable as soon as the provisioner observes it. `stage` is a
    free string (see the `STAGE_*` conventions); `message` is what the admin
    reads; `percent` is optional and only meaningful when the provisioner
    can estimate it."""
    stage: str
    message: str
    percent: Optional[int] = None


#: What core hands `provision()` to stream progress back. Awaiting it never
#: raises into the provisioner - core swallows its own persistence/broadcast
#: failures - so a provisioner calls it freely at every phase it can see.
ProgressReporter = Callable[[ProvisionProgress], Awaitable[None]]


class ComputeProvisioner(ABC):
    """Implemented by a plugin (manifest `hooks: backend: - hook: "compute.register"`)
    to provision rented GPU compute running the Remote Native worker.

    Registered by class, not instance - `ComputeProvisionerRegistry`
    instantiates it once per process.
    """

    #: Stable id this provisioner registers under (e.g. "runpod"). Used as the
    #: `provider_id` on every `ProvisionedCompute` row this provisioner creates.
    provider_id: ClassVar[str]

    #: Human-readable label for the admin UI. Falls back to `provider_id`.
    label: ClassVar[str] = ""

    #: Optional signup/referral notice the admin UI shows for this provider —
    #: a plugin may credit the project through the provider's referral program.
    #: Rendered as one understated line with an external link; both empty = no
    #: notice.
    signup_url: ClassVar[str] = ""
    signup_note: ClassVar[str] = ""

    @abstractmethod
    async def describe_fields(
        self, values: Optional[Dict[str, Any]] = None
    ) -> List[ComputeFieldDescriptorV1]:
        """Describe this provider's own `provision()` inputs for the admin UI
        (e.g. a `gpu_type` select sourced from this provider's own catalog, a
        `volume_size_gb` number). Core validates a provision request's
        `values` against these descriptors before calling `provision()` -
        the provisioner never sees an unvalidated value.

        `values` is whatever the form has been filled in with so far
        (possibly partial, possibly empty) - a provisioner whose descriptors
        declare `depends_on` uses it to resolve a dependent field's options
        (e.g. narrowing `gpu_type_id`'s choices once `data_center` is
        picked). A provisioner with no dependent fields ignores it."""

    @abstractmethod
    async def provision(self, request: ProvisionRequest, report: ProgressReporter) -> ProvisionResult:
        """Create (or reuse) the underlying compute and start the Remote Native
        worker on it. Raises `ComputeProvisionerError` on failure.

        Runs as a background task: the admin request that started it has
        long returned, so `report` is the only channel back to the operator -
        call it at every phase the provisioner can observe (see `STAGE_*`),
        including each poll while waiting on the provider.

        Cancellation: an operator terminating the row mid-bring-up cancels
        the task, so `asyncio.CancelledError` may surface from any await in
        here. A provisioner must tear down whatever it has already created
        before re-raising - core cannot, since no `handle` exists yet."""

    @abstractmethod
    async def status(self, handle: str) -> ComputeStatus:
        """Reconcile the resource identified by `handle` against the provider."""

    @abstractmethod
    async def stop(self, handle: str) -> None:
        """Stop the resource without destroying it - it can be started again.
        A resource that no longer exists on the provider is not an error."""

    @abstractmethod
    async def terminate(self, handle: str) -> None:
        """Tear the resource down. Whether this also destroys persistent
        storage (a volume, a disk) is the provisioner's own policy - core
        only ever calls this once, when an operator asks to delete the
        `ProvisionedCompute` row entirely. Idempotent: a resource already
        gone on the provider counts as terminated, never as a failure -
        raising here would strand the row with no way to clean it up."""
