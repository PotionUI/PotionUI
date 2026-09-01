"""Plan / provision / reconcile / deprovision for one RunPod provisioning
profile.

Provisions infrastructure only; returns connection details
`{base_url, worker_token}` for the backend that wires up the worker. RunPod
is a hosting provider, not an engine - core owns backend rows and the worker
protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

import httpx

from .client import Pod, RunPodClient, RunPodNotFoundError
from .resources import RunPodResourceManager
from .worker_token import generate_worker_token

PLUGIN_ID = "runpod-provider"

#: `base_url, worker_token -> did the handshake succeed`. Injectable so a
#: test never makes a real network call; the default only ever runs against
#: a real, operator-provisioned pod.
ReadinessProbe = Callable[[str, str], Awaitable[bool]]

STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_MISSING = "missing"
STATUS_UNREACHABLE = "unreachable"

#: `ProvisioningProfile.network_volume` sentinels; any other value is an
#: existing volume's RunPod id.
NETWORK_VOLUME_CREATE = "__create__"
NETWORK_VOLUME_NONE = "__none__"


async def default_readiness_probe(base_url: str, worker_token: str) -> bool:
    """GET /v1/worker through the RunPod HTTP proxy - the same handshake
    route `docs/remote-native.md`'s route table documents."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{base_url}/v1/worker",
                headers={"Authorization": f"Bearer {worker_token}"},
            )
            return response.status_code == 200
    except httpx.HTTPError:
        return False


@dataclass(frozen=True)
class ProvisioningProfile:
    name: str
    gpu_type_id: str
    image_ref: str
    region: Optional[str] = None
    volume_size_gb: int = 100
    worker_port: int = 8100
    container_registry_auth_id: Optional[str] = None
    container_disk_gb: int = 20
    network_volume: str = NETWORK_VOLUME_CREATE
    allowed_cuda_versions: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ProvisionResult:
    pod_id: str
    volume_id: Optional[str]
    base_url: str
    worker_token: str
    ready: bool


@dataclass(frozen=True)
class DeprovisionResult:
    pod_stopped: bool
    pod_terminated: bool
    volume_deleted: bool


def _worker_token_key(profile_name: str) -> str:
    return f"worker_token:{profile_name}"


class RunPodProvisioningManager:
    def __init__(
        self,
        client: RunPodClient,
        resources: RunPodResourceManager,
        plugin_repository: Any,
        *,
        readiness_probe: Optional[ReadinessProbe] = None,
    ):
        self._client = client
        self._resources = resources
        self._repo = plugin_repository
        self._readiness_probe = readiness_probe or default_readiness_probe

    async def _create_or_reuse_volume(self, profile: ProvisioningProfile) -> str:
        """Reuses this profile's own recorded volume when there is one. A
        recorded volume can be deleted out-of-band (RunPod console); verify
        before reuse and recreate on 404."""
        existing = self._resources.get(profile.name, "network_volume")
        if existing is not None:
            try:
                volume = await self._client.get_network_volume(existing.runpod_id)
                return volume.id
            except RunPodNotFoundError:
                self._resources.delete(profile.name, "network_volume")

        volume = await self._client.create_network_volume(
            name=f"potionui-{profile.name}",
            size_gb=profile.volume_size_gb,
            data_center_id=profile.region or "",
        )
        self._resources.record(
            profile.name, "network_volume", volume.id, meta={"data_center_id": volume.data_center_id}
        )
        return volume.id

    async def provision(self, profile: ProvisioningProfile) -> ProvisionResult:
        if profile.network_volume == NETWORK_VOLUME_NONE:
            volume_id = None
        elif profile.network_volume == NETWORK_VOLUME_CREATE:
            volume_id = await self._create_or_reuse_volume(profile)
        else:
            # An admin-selected existing volume - the caller already
            # verified it exists and resolved `profile.region` from it.
            volume_id = profile.network_volume
            self._resources.record(
                profile.name, "network_volume", volume_id, meta={"data_center_id": profile.region}
            )

        worker_token = generate_worker_token()
        create_pod_kwargs: Dict[str, Any] = dict(
            name=f"potionui-{profile.name}",
            image_name=profile.image_ref,
            container_registry_auth_id=profile.container_registry_auth_id,
            gpu_type_ids=[profile.gpu_type_id],
            env={
                "POTIONUI_WORKER_TOKEN": worker_token,
                "POTIONUI_WORKER_HOST": "0.0.0.0",
                "POTIONUI_WORKER_PORT": str(profile.worker_port),
                "POTIONUI_WORKER_PROVIDER": "runpod",
            },
            ports=[f"{profile.worker_port}/http"],
            network_volume_id=volume_id,
            container_disk_in_gb=profile.container_disk_gb,
            data_center_ids=[profile.region] if profile.region else None,
            allowed_cuda_versions=list(profile.allowed_cuda_versions),
        )
        if volume_id:
            create_pod_kwargs["volume_mount_path"] = "/models"
        pod = await self._client.create_pod(**create_pod_kwargs)
        self._resources.record(
            profile.name, "pod", pod.id, meta={"worker_port": profile.worker_port}
        )
        self._repo.set_plugin_setting(
            PLUGIN_ID, _worker_token_key(profile.name), worker_token, is_secret=True
        )

        base_url = _proxy_url(pod.id, profile.worker_port)
        ready = await self._readiness_probe(base_url, worker_token)

        return ProvisionResult(
            pod_id=pod.id,
            volume_id=volume_id,
            base_url=base_url,
            worker_token=worker_token,
            ready=ready,
        )

    async def reconcile(self, profile_name: str) -> str:
        pod_record = self._resources.get(profile_name, "pod")
        if pod_record is None:
            return STATUS_MISSING

        try:
            pod = await self._client.get_pod(pod_record.runpod_id)
        except RunPodNotFoundError:
            return STATUS_MISSING

        if pod.desired_status == "TERMINATED":
            return STATUS_MISSING
        if pod.desired_status == "EXITED":
            return STATUS_STOPPED
        if pod.desired_status != "RUNNING":
            return STATUS_UNREACHABLE

        token = self._read_worker_token(profile_name)
        if token is None:
            return STATUS_UNREACHABLE

        worker_port = pod_record.meta.get("worker_port", 8100)
        base_url = _proxy_url(pod.id, worker_port)
        ready = await self._readiness_probe(base_url, token)
        return STATUS_RUNNING if ready else STATUS_UNREACHABLE

    async def deprovision(
        self,
        profile_name: str,
        *,
        terminate_pod: bool = False,
        delete_volume: bool = False,
    ) -> DeprovisionResult:
        """`terminate_pod` and `delete_volume` are separate, explicit
        choices - both default to False, so a bare call only ever stops the
        pod and never touches the volume. Destroying the volume requires the
        caller to opt in twice: once by asking to terminate (rather than
        merely stop) the pod, and again by asking to delete the volume."""
        pod_record = self._resources.get(profile_name, "pod")
        pod_stopped = False
        pod_terminated = False

        if pod_record is not None:
            # A pod already gone on RunPod's side (console deletion, reclaim)
            # counts as torn down - the point is the cleanup, not the call.
            if terminate_pod:
                try:
                    await self._client.terminate_pod(pod_record.runpod_id)
                except RunPodNotFoundError:
                    pass
                self._resources.delete(profile_name, "pod")
                self._repo.delete_plugin_setting(PLUGIN_ID, _worker_token_key(profile_name))
                pod_terminated = True
            else:
                try:
                    await self._client.stop_pod(pod_record.runpod_id)
                except RunPodNotFoundError:
                    self._resources.delete(profile_name, "pod")
                pod_stopped = True

        volume_deleted = False
        if delete_volume:
            volume_record = self._resources.get(profile_name, "network_volume")
            if volume_record is not None:
                try:
                    await self._client.delete_network_volume(volume_record.runpod_id)
                except RunPodNotFoundError:
                    pass
                self._resources.delete(profile_name, "network_volume")
                volume_deleted = True

        return DeprovisionResult(
            pod_stopped=pod_stopped,
            pod_terminated=pod_terminated,
            volume_deleted=volume_deleted,
        )

    def _read_worker_token(self, profile_name: str) -> Optional[str]:
        setting = self._repo.get_plugin_setting(PLUGIN_ID, _worker_token_key(profile_name))
        return setting.setting_value if setting else None


def _proxy_url(pod_id: str, port: int) -> str:
    """RunPod's HTTP proxy URL format for an exposed port
    (docs.runpod.io/pods/configuration/expose-ports)."""
    return f"https://{pod_id}-{port}.proxy.runpod.net"
