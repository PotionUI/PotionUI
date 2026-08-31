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

import logging
from typing import Any, Dict, List, Optional

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

from . import catalog_client
from .catalog_client import DataCenter as LiveDataCenter
from .catalog_client import GpuAvailability, RunPodCatalogError
from .client import RunPodAPIError, RunPodClient
from .datacenter_catalog import STATIC_DATACENTER_CATALOG
from .gpu_catalog import STATIC_GPU_CATALOG
from .provisioning import ProvisioningProfile, RunPodProvisioningManager
from .resources import RunPodResourceManager
from .settings import RunPodSettings, load_settings

logger = logging.getLogger(__name__)

#: Fixed defaults for the RunPod-only knobs that don't get an admin-facing
#: field - `worker_port` is the Remote Native worker's own listen port
#: (never anything else), `container_disk_gb` is scratch space unrelated to
#: the persistent network volume `volume_size_gb` describes.
_WORKER_PORT = 8100
_CONTAINER_DISK_GB = 20

#: `gpuAvailability.stockStatus` values that mean "don't offer this GPU" -
#: RunPod returns the GPU either way, distinguished only by this string.
_OUT_OF_STOCK = {"none", ""}


def _gpu_detail(gpu: GpuAvailability) -> Optional[str]:
    parts = []
    if gpu.memory_gb:
        parts.append(f"{gpu.memory_gb} GB")
    if gpu.stock_status:
        parts.append(f"{gpu.stock_status} stock")
    return " · ".join(parts) or None


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

    async def describe_fields(
        self, values: Optional[Dict[str, Any]] = None
    ) -> List[ComputeFieldDescriptorV1]:
        settings = load_settings(self._plugin_repository)
        values = values or {}
        live_data_centers = await self._live_data_centers(settings.api_key)

        return [
            self._data_center_field(settings, live_data_centers),
            self._gpu_type_field(settings, values, live_data_centers),
            ComputeFieldDescriptorV1(
                key="volume_size_gb",
                label="Volume Size (GB)",
                type="number",
                required=True,
                default=settings.volume_size_gb,
                help_text="Size of the persistent network volume models are cached on.",
            ),
        ]

    async def _live_data_centers(self, api_key: Optional[str]) -> Optional[List[LiveDataCenter]]:
        """RunPod's live GraphQL catalog, or `None` (logged, never raised) on
        a missing key or any catalog-fetch failure - callers fall back to the
        static catalogs on `None`, exactly like this plugin did before the
        live catalog existed."""
        if not api_key:
            return None
        try:
            return await catalog_client.get_catalog(api_key)
        except RunPodCatalogError as exc:
            logger.warning("RunPod GraphQL catalog unavailable, using the static catalog instead: %s", exc)
            return None

    def _data_center_field(
        self, settings: RunPodSettings, live_data_centers: Optional[List[LiveDataCenter]]
    ) -> ComputeFieldDescriptorV1:
        if live_data_centers is not None:
            options = [
                ComputeFieldOptionV1(value=dc.id, label=dc.id, detail=dc.location or dc.name)
                for dc in live_data_centers
            ]
            help_text = (
                "The network volume and pod are created in this data center. "
                "GPU availability varies by data center."
            )
        else:
            options = [
                ComputeFieldOptionV1(value=dc.id, label=dc.id, detail=dc.geography)
                for dc in STATIC_DATACENTER_CATALOG
            ]
            help_text = (
                "The network volume and pod are created in this data center. GPU availability varies "
                "by data center. Showing the static catalog - RunPod's live catalog is unavailable."
            )

        return ComputeFieldDescriptorV1(
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
            help_text=help_text,
            options=options,
        )

    def _gpu_type_field(
        self,
        settings: RunPodSettings,
        values: Dict[str, Any],
        live_data_centers: Optional[List[LiveDataCenter]],
    ) -> ComputeFieldDescriptorV1:
        if live_data_centers is None:
            # GraphQL unavailable - the static catalog isn't scoped to any data
            # center, so it can't be filtered; offer the full list unfiltered.
            return ComputeFieldDescriptorV1(
                key="gpu_type_id",
                label="GPU Type",
                type="select",
                required=True,
                default=settings.gpu_type_id,
                help_text=(
                    "The GPU RunPod starts the pod on. Showing the static catalog - RunPod's live "
                    "catalog is unavailable."
                ),
                depends_on=["data_center_id"],
                options=[
                    ComputeFieldOptionV1(
                        value=gpu.id,
                        label=gpu.id,
                        detail=f"{gpu.memory_gb} GB VRAM" if gpu.memory_gb else None,
                    )
                    for gpu in STATIC_GPU_CATALOG
                ],
            )

        chosen_dc = values.get("data_center_id")
        if not chosen_dc:
            return ComputeFieldDescriptorV1(
                key="gpu_type_id",
                label="GPU Type",
                type="select",
                required=True,
                default=settings.gpu_type_id,
                help_text="Choose a data center first.",
                depends_on=["data_center_id"],
                options=[],
            )

        data_center = next((dc for dc in live_data_centers if dc.id == chosen_dc), None)
        available = [
            gpu
            for gpu in (data_center.gpus if data_center else [])
            if (gpu.stock_status or "").strip().lower() not in _OUT_OF_STOCK
        ]

        return ComputeFieldDescriptorV1(
            key="gpu_type_id",
            label="GPU Type",
            type="select",
            required=True,
            default=settings.gpu_type_id,
            help_text=(
                f"GPUs available in {chosen_dc}."
                if available
                else f"No GPUs currently in stock in {chosen_dc}."
            ),
            depends_on=["data_center_id"],
            options=[
                ComputeFieldOptionV1(value=gpu.gpu_type_id, label=gpu.gpu_type_display_name, detail=_gpu_detail(gpu))
                for gpu in available
            ],
        )

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
