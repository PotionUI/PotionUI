"""Adapts `RunPodProvisioningManager` to core's `ComputeProvisioner` contract
(`src.plugin_api.compute`) - registered via the `compute.register` hook, see
`hooks/compute_hooks.py`.

`handle` in every `ComputeProvisioner` call is this plugin's `profile_name`:
`RunPodProvisioningManager` and `RunPodResourceManager` key every stored
resource by `profile_name`, not by RunPod's own pod id, so `profile_name` is
the one identifier that survives round-tripping through core's
`provisioned_compute` row and back.
"""

from __future__ import annotations

from typing import List

from src.plugin_api import PluginRepository
from src.plugin_api.compute import (
    ComputeGpuType,
    ComputeProvisioner,
    ComputeProvisionerError,
    ComputeStatus,
    ProvisionRequest,
    ProvisionResult,
)

from .client import RunPodAPIError, RunPodClient
from .gpu_catalog import STATIC_GPU_CATALOG
from .provisioning import ProvisioningProfile, RunPodProvisioningManager
from .resources import RunPodResourceManager
from .settings import load_settings


class RunpodComputeProvisioner(ComputeProvisioner):
    provider_id = "runpod"
    label = "RunPod"

    def __init__(self) -> None:
        self._plugin_repository = PluginRepository()
        self._resources = RunPodResourceManager()

    def _manager(self, client: RunPodClient) -> RunPodProvisioningManager:
        return RunPodProvisioningManager(client, self._resources, self._plugin_repository)

    def _require_api_key(self):
        settings = load_settings(self._plugin_repository)
        if not settings.api_key:
            raise ComputeProvisionerError("RunPod API key is not configured")
        return settings

    async def list_gpu_types(self) -> List[ComputeGpuType]:
        return [ComputeGpuType(id=gpu.id, memory_gb=gpu.memory_gb) for gpu in STATIC_GPU_CATALOG]

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        settings = self._require_api_key()

        image_ref = request.image_ref or settings.worker_image
        if not image_ref:
            raise ComputeProvisionerError(
                "No worker image configured - set 'worker_image' in the RunPod "
                "Provider plugin settings or pass image_ref"
            )

        profile = ProvisioningProfile(
            name=request.profile_name,
            gpu_type_id=request.gpu_type_id or settings.gpu_type_id,
            image_ref=image_ref,
            region=request.region or settings.region,
            volume_size_gb=request.volume_size_gb or settings.volume_size_gb,
            worker_port=request.worker_port,
            container_disk_gb=request.container_disk_gb,
        )

        client = RunPodClient(api_key=settings.api_key)
        try:
            result = await self._manager(client).provision(profile)
        except RunPodAPIError as exc:
            raise ComputeProvisionerError(str(exc)) from exc
        finally:
            await client.aclose()

        return ProvisionResult(
            handle=request.profile_name,
            base_url=result.base_url,
            worker_token=result.worker_token,
            ready=result.ready,
            resource_ref=result.pod_id,
        )

    async def status(self, handle: str) -> ComputeStatus:
        settings = self._require_api_key()
        client = RunPodClient(api_key=settings.api_key)
        try:
            state = await self._manager(client).reconcile(handle)
        except RunPodAPIError as exc:
            raise ComputeProvisionerError(str(exc)) from exc
        finally:
            await client.aclose()
        return ComputeStatus(state=state)

    async def stop(self, handle: str) -> None:
        await self._deprovision(handle, terminate_pod=False)

    async def terminate(self, handle: str) -> None:
        await self._deprovision(handle, terminate_pod=True)

    async def _deprovision(self, handle: str, *, terminate_pod: bool) -> None:
        settings = self._require_api_key()
        client = RunPodClient(api_key=settings.api_key)
        try:
            await self._manager(client).deprovision(handle, terminate_pod=terminate_pod)
        except RunPodAPIError as exc:
            raise ComputeProvisionerError(str(exc)) from exc
        finally:
            await client.aclose()
