"""The `ComputeProvisioner` contract: what a plugin implements to let core
provision rented GPU compute (see `docs/remote-native.md`'s "Core side" and
the `runpod-provider` plugin, the first implementation).

A provisioner owns exactly one thing: turning a `ProvisionRequest` into a
running Remote Native worker and reporting/tearing down its lifecycle. It
never touches a `native.remote` backend row - that is core's job
(`src.features.provisioning.operations`), driven by the `handle` a
provisioner hands back from `provision()` and expects unchanged in every
later `status`/`stop`/`terminate` call. What `handle` actually is is entirely
the provisioner's choice (a profile name, a pod id, ...); core only ever
stores and echoes it back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, List, Optional


class ComputeProvisionerError(RuntimeError):
    """Raised by a `ComputeProvisioner` when the underlying provider call fails
    (a rejected API key, a rate limit, a resource that no longer exists, ...)."""


@dataclass(frozen=True)
class ComputeGpuType:
    """One GPU type a provisioner can start a pod on."""
    id: str
    memory_gb: Optional[int] = None


@dataclass(frozen=True)
class ProvisionRequest:
    """What to provision. `profile_name` is the operator-chosen label a
    provisioner may use to identify/reuse its own resources (e.g. a network
    volume) across repeated provisions - it is not core's row id."""
    profile_name: str
    gpu_type_id: Optional[str] = None
    region: Optional[str] = None
    image_ref: Optional[str] = None
    volume_size_gb: Optional[int] = None
    worker_port: int = 8100
    container_disk_gb: int = 20


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

    Registered by class, not instance - `ComputeProvisionerRegistry` instantiates
    it once per process, the same way `BackendRegistry` instantiates a registered
    backend class.
    """

    #: Stable id this provisioner registers under (e.g. "runpod"). Used as the
    #: `provider_id` on every `ProvisionedCompute` row this provisioner creates.
    provider_id: ClassVar[str]

    #: Human-readable label for the admin UI. Falls back to `provider_id`.
    label: ClassVar[str] = ""

    @abstractmethod
    async def list_gpu_types(self) -> List[ComputeGpuType]:
        """GPU types this provider can start a pod on."""

    @abstractmethod
    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        """Create (or reuse) the underlying compute and start the Remote Native
        worker on it. Raises `ComputeProvisionerError` on failure."""

    @abstractmethod
    async def status(self, handle: str) -> ComputeStatus:
        """Reconcile the resource identified by `handle` against the provider."""

    @abstractmethod
    async def stop(self, handle: str) -> None:
        """Stop the resource without destroying it - it can be started again."""

    @abstractmethod
    async def terminate(self, handle: str) -> None:
        """Tear the resource down. Whether this also destroys persistent
        storage (a volume, a disk) is the provisioner's own policy - core
        only ever calls this once, when an operator asks to delete the
        `ProvisionedCompute` row entirely."""
