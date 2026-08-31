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
    ComputeFieldDescriptorV1,
    ComputeFieldOptionV1,
    ComputeProvisioner,
    ComputeProvisionerError,
    ComputeStatus,
    ProvisionRequest,
    ProvisionResult,
)

from .client import RunPodAPIError, RunPodClient
from .datacenter_catalog import STATIC_DATACENTER_CATALOG
from .gpu_catalog import STATIC_GPU_CATALOG
from .provisioning import ProvisioningProfile, RunPodProvisioningManager
from .resources import RunPodResourceManager
from .settings import load_settings

#: Fixed defaults for the RunPod-only knobs that don't get an admin-facing
#: field - `worker_port` is the Remote Native worker's own listen port
#: (never anything else), `container_disk_gb` is scratch space unrelated to
#: the persistent network volume `volume_size_gb` describes.
_WORKER_PORT = 8100
_CONTAINER_DISK_GB = 20


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

    async def describe_fields(self) -> List[ComputeFieldDescriptorV1]:
        settings = load_settings(self._plugin_repository)
        return [
            ComputeFieldDescriptorV1(
                key="gpu_type_id",
                label="GPU Type",
                type="select",
                required=True,
                default=settings.gpu_type_id,
                help_text="The GPU RunPod starts the pod on.",
                options=[
                    ComputeFieldOptionV1(
                        value=gpu.id,
                        label=gpu.id,
                        detail=f"{gpu.memory_gb} GB VRAM" if gpu.memory_gb else None,
                    )
                    for gpu in STATIC_GPU_CATALOG
                ],
            ),
            ComputeFieldDescriptorV1(
                key="volume_size_gb",
                label="Volume Size (GB)",
                type="number",
                required=True,
                default=settings.volume_size_gb,
                help_text="Size of the persistent network volume models are cached on.",
            ),
            ComputeFieldDescriptorV1(
                key="data_center_id",
                label="Data Center",
                type="select",
                required=True,
                # No fallback to a type-appropriate empty here: `dataCenterId` is a
                # required RunPod field, and defaulting to "" would just recreate the
                # bug this fixes (an empty string satisfies core's "value is present"
                # check, so the actual data-center dropdown must never quietly submit
                # empty - forcing an explicit choice when the optional `region`
                # setting isn't set is the point).
                default=settings.region or None,
                help_text="The network volume and pod are created in this data center. GPU availability varies by data center.",
                options=[
                    ComputeFieldOptionV1(value=dc.id, label=dc.id, detail=dc.geography)
                    for dc in STATIC_DATACENTER_CATALOG
                ],
            ),
        ]

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        settings = self._require_api_key()

        image_ref = settings.worker_image
        if not image_ref:
            raise ComputeProvisionerError(
                "No worker image configured - set 'worker_image' in the RunPod "
                "Provider plugin settings"
            )

        # `data_center_id` is required on RunPod's own `POST /networkvolumes` -
        # core's own required-field validation already rejects a missing key, but
        # not an empty string (a value only needs to be non-None to satisfy
        # "present"), and a caller that bypasses the admin form's select entirely
        # could still omit the key outright. Belt and braces beyond core: fall
        # back to the optional `region` setting for an omitted key, then refuse
        # to provision at all rather than send RunPod the empty string that
        # produced "dataCenterId of required type String! was not provided".
        data_center_id = request.values.get("data_center_id") or settings.region
        if not data_center_id:
            raise ComputeProvisionerError(
                "'data_center_id' is required - choose a data center, or set "
                "'region' in the RunPod Provider plugin settings"
            )

        profile = ProvisioningProfile(
            name=request.profile_name,
            gpu_type_id=request.values.get("gpu_type_id") or settings.gpu_type_id,
            image_ref=image_ref,
            region=data_center_id,
            volume_size_gb=request.values.get("volume_size_gb") or settings.volume_size_gb,
            worker_port=_WORKER_PORT,
            container_disk_gb=_CONTAINER_DISK_GB,
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
