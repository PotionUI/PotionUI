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
from typing import Any, ClassVar, Dict, List, Optional


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
    """Reconciled state of a provisioned resource."""
    state: str  # "running" | "stopped" | "missing" | "unreachable"


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
    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        """Create (or reuse) the underlying compute and start the Remote Native
        worker on it. Raises `ComputeProvisionerError` on failure."""

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
