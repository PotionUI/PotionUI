"""Typed wrapper over RunPod's REST API (v1) - not the deprecated GraphQL
API and not the `runpod` SDK.

REST v1 has no endpoint to list GPU types with pricing or to fetch a Pod's
logs; both exist only on the deprecated GraphQL API. `list_gpu_types()`
returns a static catalog instead; `get_pod_logs()` raises
`RunPodFeatureUnavailable`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from .gpu_catalog import STATIC_GPU_CATALOG, GpuType

DEFAULT_BASE_URL = "https://rest.runpod.io/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0


class RunPodAPIError(Exception):
    """A RunPod REST call returned an error status."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"RunPod API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class RunPodAuthError(RunPodAPIError):
    """The configured API key was rejected (401/403)."""


class RunPodNotFoundError(RunPodAPIError):
    """The requested resource does not exist (404)."""


class RunPodFeatureUnavailable(Exception):
    """The operation has no REST v1 endpoint; only the deprecated GraphQL
    API exposes it."""


@dataclass(frozen=True)
class Pod:
    id: str
    name: str
    image: str
    desired_status: str  # "RUNNING" | "EXITED" | "TERMINATED"
    public_ip: Optional[str]
    port_mappings: Dict[str, int]
    ports: List[str]
    cost_per_hr: Optional[float]
    network_volume_id: Optional[str]

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Pod":
        network_volume = data.get("networkVolume") or {}
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            image=data.get("image", ""),
            desired_status=data.get("desiredStatus", ""),
            public_ip=data.get("publicIp"),
            port_mappings=data.get("portMappings") or {},
            ports=data.get("ports") or [],
            cost_per_hr=data.get("costPerHr"),
            network_volume_id=network_volume.get("id") or data.get("networkVolumeId"),
        )


@dataclass(frozen=True)
class NetworkVolume:
    id: str
    name: str
    size_gb: int
    data_center_id: str

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "NetworkVolume":
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            size_gb=data.get("size", 0),
            data_center_id=data.get("dataCenterId", ""),
        )


@dataclass(frozen=True)
class RunPodClient:
    """`field(init=False)` builds the httpx client from `api_key`/`base_url`/
    `timeout`; callers only ever pass those three."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    transport: Optional[httpx.BaseTransport] = None
    _http: httpx.AsyncClient = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_http",
            httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "PotionUI (+https://github.com/PotionUI/PotionUI)",
                },
            ),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "RunPodClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, *, json: Optional[Dict[str, Any]] = None) -> Any:
        try:
            response = await self._http.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise RunPodAPIError(0, f"connection error: {exc}") from exc

        if response.status_code in (401, 403):
            raise RunPodAuthError(response.status_code, "RunPod API key was rejected")
        if response.status_code == 404:
            raise RunPodNotFoundError(response.status_code, f"{path} not found")
        if response.status_code >= 400:
            raise RunPodAPIError(response.status_code, response.text)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # -- Authentication ----------------------------------------------------

    async def validate_api_key(self) -> bool:
        """REST v1 has no whoami/key-check endpoint. Returns `False` only
        on 401/403; other errors propagate."""
        try:
            await self._request("GET", "/pods")
            return True
        except RunPodAuthError:
            return False

    # -- GPU types -----------------------------------------------------------

    def list_gpu_types(self) -> List[GpuType]:
        """Static catalog, synchronous - never touches the network."""
        return list(STATIC_GPU_CATALOG)

    # -- Network volumes -----------------------------------------------------

    async def create_network_volume(self, *, name: str, size_gb: int, data_center_id: str) -> NetworkVolume:
        data = await self._request(
            "POST",
            "/networkvolumes",
            json={"name": name, "size": size_gb, "dataCenterId": data_center_id},
        )
        return NetworkVolume.from_api(data)

    async def get_network_volume(self, volume_id: str) -> NetworkVolume:
        data = await self._request("GET", f"/networkvolumes/{volume_id}")
        return NetworkVolume.from_api(data)

    async def list_network_volumes(self) -> List[NetworkVolume]:
        data = await self._request("GET", "/networkvolumes")
        return [NetworkVolume.from_api(item) for item in data or []]

    async def delete_network_volume(self, volume_id: str) -> None:
        await self._request("DELETE", f"/networkvolumes/{volume_id}")

    # -- Pods ------------------------------------------------------------------

    async def create_pod(
        self,
        *,
        name: str,
        image_name: str,
        gpu_type_ids: List[str],
        env: Dict[str, str],
        ports: List[str],
        container_disk_in_gb: int = 20,
        volume_in_gb: int = 20,
        network_volume_id: Optional[str] = None,
        volume_mount_path: str = "/workspace",
        cloud_type: str = "SECURE",
        data_center_ids: Optional[List[str]] = None,
        container_registry_auth_id: Optional[str] = None,
        allowed_cuda_versions: Optional[List[str]] = None,
    ) -> Pod:
        payload: Dict[str, Any] = {
            "name": name,
            "imageName": image_name,
            "gpuTypeIds": gpu_type_ids,
            "gpuCount": 1,
            "env": env,
            "ports": ports,
            "containerDiskInGb": container_disk_in_gb,
            # A network volume replaces the pod's local volume, so 0 here -
            # a nonzero volumeInGb on top of a network volume over-constrains
            # host selection.
            "volumeInGb": 0 if network_volume_id else volume_in_gb,
            "volumeMountPath": volume_mount_path,
            "cloudType": cloud_type,
        }
        if network_volume_id:
            payload["networkVolumeId"] = network_volume_id
        if data_center_ids:
            payload["dataCenterIds"] = data_center_ids
        if container_registry_auth_id:
            payload["containerRegistryAuthId"] = container_registry_auth_id
        # Omitted means "any CUDA version is acceptable" per PodCreateInput -
        # sending an empty list would allow nothing.
        if allowed_cuda_versions:
            payload["allowedCudaVersions"] = list(allowed_cuda_versions)

        data = await self._request("POST", "/pods", json=payload)
        return Pod.from_api(data)

    async def list_pods(self) -> List[Pod]:
        data = await self._request("GET", "/pods")
        return [Pod.from_api(item) for item in data or []]

    async def get_pod(self, pod_id: str) -> Pod:
        data = await self._request("GET", f"/pods/{pod_id}")
        return Pod.from_api(data)

    async def stop_pod(self, pod_id: str) -> Pod:
        data = await self._request("POST", f"/pods/{pod_id}/stop")
        return Pod.from_api(data)

    async def start_pod(self, pod_id: str) -> Pod:
        """Resume a stopped (EXITED) pod. The pod keeps its id, so its proxy
        URL is unchanged."""
        data = await self._request("POST", f"/pods/{pod_id}/start")
        return Pod.from_api(data)

    async def terminate_pod(self, pod_id: str) -> None:
        await self._request("DELETE", f"/pods/{pod_id}")

    async def get_pod_logs(self, pod_id: str) -> str:
        raise RunPodFeatureUnavailable(
            "RunPod's REST API (v1) has no pod-logs endpoint - only the "
            "deprecated GraphQL API exposes `podLogs`, which this plugin "
            "does not use. View logs on the RunPod console instead: "
            f"https://console.runpod.io/pods/{pod_id}"
        )
