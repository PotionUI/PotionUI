from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from src.plugin_api.providers import client_ssl_context

from .gpu_catalog import STATIC_GPU_CATALOG, GpuType

DEFAULT_BASE_URL = "https://api.runpod.io/v2"
LEGACY_BASE_URLS = frozenset({"https://rest.runpod.io/v1"})
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RATE_LIMIT_RETRIES = 2
MAX_RATE_LIMIT_WAIT_SECONDS = 30.0
MAX_PAGES = 100
PAGE_SIZE = 1000
_RATE_LIMIT_WINDOW = re.compile(r";\s*r=(\d+)\s*;\s*t=(\d+)")


def normalize_base_url(url: Optional[str]) -> str:
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned or cleaned in LEGACY_BASE_URLS:
        return DEFAULT_BASE_URL
    return cleaned


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


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


class RunPodRateLimitError(RunPodAPIError):
    def __init__(self, status_code: int, message: str, retry_after: Optional[float] = None):
        super().__init__(status_code, message)
        self.retry_after = retry_after


class RunPodValidationError(RunPodAPIError):
    pass


class RunPodPlacementError(RunPodAPIError):
    pass


class RunPodFeatureUnavailable(Exception):
    pass


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
        runtime_ports = (data.get("runtime") or {}).get("ports") or []
        public_ports = [item for item in runtime_ports if item.get("public") is not None]
        network_mounts = (data.get("mounts") or {}).get("network") or []
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            image=data.get("image", ""),
            desired_status=data.get("status", ""),
            public_ip=next((item["ip"] for item in public_ports if item.get("ip")), None),
            port_mappings={str(item["private"]): item["public"] for item in public_ports if "private" in item},
            ports=data.get("ports") or [],
            cost_per_hr=data.get("cost"),
            network_volume_id=network_mounts[0].get("volumeId") if network_mounts else None,
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
            data_center_id=data.get("dataCenter", ""),
        )


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("title") or ""
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            detail = "; ".join(part for part in [detail, *map(str, errors)] if part)
        if detail:
            return detail
    return response.text[:500] or f"HTTP {response.status_code}"


def _retry_after_seconds(response: httpx.Response) -> Optional[float]:
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    exhausted = [
        int(reset) for remaining, reset in _RATE_LIMIT_WINDOW.findall(response.headers.get("ratelimit", ""))
        if int(remaining) == 0
    ]
    return float(max(exhausted)) if exhausted else None


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
        object.__setattr__(self, "base_url", normalize_base_url(self.base_url))
        object.__setattr__(
            self,
            "_http",
            httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
                verify=client_ssl_context(),
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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        retryable = method in ("GET", "DELETE")
        attempt = 0
        while True:
            try:
                response = await self._http.request(method, path, json=json, params=params)
            except httpx.HTTPError as exc:
                raise RunPodAPIError(0, f"connection error: {exc}") from exc

            if response.status_code != 429:
                break
            wait = _retry_after_seconds(response)
            if not retryable or attempt >= MAX_RATE_LIMIT_RETRIES or (wait or 0.0) > MAX_RATE_LIMIT_WAIT_SECONDS:
                hint = f" (retry after {wait:g}s)" if wait is not None else ""
                raise RunPodRateLimitError(429, f"rate limit reached: {_error_message(response)}{hint}", wait)
            await _sleep(wait if wait is not None else float(2 ** attempt))
            attempt += 1

        status = response.status_code
        if status in (401, 403):
            raise RunPodAuthError(status, f"RunPod API key was rejected: {_error_message(response)}")
        if status == 404:
            raise RunPodNotFoundError(status, f"{path} not found: {_error_message(response)}")
        if status == 422:
            raise RunPodValidationError(status, f"request rejected: {_error_message(response)}")
        if status >= 400:
            raise RunPodAPIError(status, _error_message(response))
        if status == 204 or not response.content:
            return None
        return response.json()

    async def _list(self, path: str, key: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        seen = set()
        for _ in range(MAX_PAGES):
            query = {**(params or {}), "limit": PAGE_SIZE}
            if cursor:
                query["cursor"] = cursor
            data = await self._request("GET", path, params=query)
            if isinstance(data, list):
                return items + data
            data = data or {}
            items.extend(data.get(key) or [])
            pagination = data.get("pagination") or {}
            cursor = pagination.get("nextCursor")
            if not pagination.get("hasNextPage") or not cursor or cursor in seen:
                return items
            seen.add(cursor)
        raise RunPodAPIError(0, f"{path} returned more than {MAX_PAGES} pages")

    # -- Authentication ----------------------------------------------------

    async def validate_api_key(self) -> bool:
        try:
            await self._request("GET", "/pods", params={"limit": 1})
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
            "/network-volumes",
            json={"name": name, "size": size_gb, "dataCenter": data_center_id},
        )
        return NetworkVolume.from_api(data)

    async def get_network_volume(self, volume_id: str) -> NetworkVolume:
        data = await self._request("GET", f"/network-volumes/{volume_id}")
        return NetworkVolume.from_api(data)

    async def list_network_volumes(self) -> List[NetworkVolume]:
        items = await self._list("/network-volumes", "networkVolumes")
        return [NetworkVolume.from_api(item) for item in items]

    async def delete_network_volume(self, volume_id: str) -> None:
        await self._request("DELETE", f"/network-volumes/{volume_id}")

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
            "image": image_name,
            "env": env,
            "ports": ports,
            "disk": container_disk_in_gb,
            "cloud": cloud_type,
        }
        if network_volume_id:
            payload["mounts"] = {"network": [{"volumeId": network_volume_id, "path": volume_mount_path}]}
        else:
            payload["mounts"] = {"persistent": {"size": max(volume_in_gb, 10), "path": volume_mount_path}}
        if data_center_ids:
            payload["dataCenterIds"] = data_center_ids
        if container_registry_auth_id:
            payload["registry"] = container_registry_auth_id
        gpu: Dict[str, Any] = {"count": 1}
        if allowed_cuda_versions:
            gpu["allowedCudaVersions"] = list(allowed_cuda_versions)

        last_error: Optional[RunPodAPIError] = None
        for gpu_type_id in gpu_type_ids:
            try:
                data = await self._request("POST", "/pods", json={**payload, "gpu": {**gpu, "id": gpu_type_id}})
                return Pod.from_api(data)
            except RunPodAuthError as exc:
                if exc.status_code != 403:
                    raise
                last_error = exc
            except RunPodAPIError as exc:
                if exc.status_code != 400:
                    raise
                last_error = RunPodPlacementError(400, exc.message)
        raise last_error or RunPodAPIError(0, "no GPU type given for the pod")

    async def list_pods(self) -> List[Pod]:
        items = await self._list("/pods", "pods")
        return [Pod.from_api(item) for item in items]

    async def get_pod(self, pod_id: str) -> Pod:
        data = await self._request("GET", f"/pods/{pod_id}")
        return Pod.from_api(data)

    async def _pod_action(self, pod_id: str, action: str, settled: tuple) -> Pod:
        try:
            data = await self._request("POST", f"/pods/{pod_id}/action", json={"action": action})
        except RunPodAPIError as exc:
            if exc.status_code != 409:
                raise
            pod = await self.get_pod(pod_id)
            if pod.desired_status in settled:
                return pod
            raise
        if data is None:
            return await self.get_pod(pod_id)
        return Pod.from_api(data)

    async def stop_pod(self, pod_id: str) -> Pod:
        return await self._pod_action(pod_id, "stop", ("EXITED",))

    async def start_pod(self, pod_id: str) -> Pod:
        """Resume a stopped (EXITED) pod. The pod keeps its id, so its proxy
        URL is unchanged."""
        return await self._pod_action(pod_id, "start", ("RUNNING", "STARTING", "PROVISIONING"))

    async def terminate_pod(self, pod_id: str) -> None:
        await self._request("DELETE", f"/pods/{pod_id}")

    async def get_pod_logs(self, pod_id: str) -> str:
        raise RunPodFeatureUnavailable(
            "This plugin does not fetch pod logs. View them on the RunPod "
            f"console instead: https://console.runpod.io/pods/{pod_id}"
        )
