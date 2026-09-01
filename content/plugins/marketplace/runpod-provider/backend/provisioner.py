"""Adapts `RunPodProvisioningManager` to core's `ComputeProvisioner` contract
(registered via the `compute.register` hook in `hooks/compute_hooks.py`).

`handle` is this plugin's `profile_name` - resources are keyed by it, not by
RunPod's own pod id.
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
from .client import NetworkVolume, RunPodAPIError, RunPodClient
from .provisioning import (
    NETWORK_VOLUME_CREATE,
    NETWORK_VOLUME_NONE,
    ProvisioningProfile,
    RunPodProvisioningManager,
)
from .resources import RunPodResourceManager
from .settings import RunPodSettings, load_settings

logger = logging.getLogger(__name__)

#: Fixed defaults with no admin-facing field. `container_disk_gb` is scratch
#: space, unrelated to the persistent network volume `volume_size_gb`.
_WORKER_PORT = 8100
_CONTAINER_DISK_GB = 20

#: `gpuAvailability.stockStatus` values that mean "don't offer this GPU" -
#: RunPod returns the GPU either way, distinguished only by this string.
_OUT_OF_STOCK = {"none", ""}

#: Ranks `stockStatus` for auto-picking a data center - unranked values (an
#: unrecognized string, missing entirely) sort last, not first.
_STOCK_RANK = {"high": 3, "medium": 2, "low": 1}

#: Substrings RunPod's own pod-create error text uses when a GPU/data-center
#: combo has no capacity right now - matched case-insensitively since the
#: exact wording isn't a documented contract.
_NO_CAPACITY_MARKERS = (
    "could not find any pods with required specifications",
    "no instances available",
)


def _with_capacity_hint(message: str) -> str:
    """Appends an actionable hint to a no-capacity-shaped RunPod error,
    leaving every other error message untouched."""
    lowered = message.lower()
    if any(marker in lowered for marker in _NO_CAPACITY_MARKERS):
        return (
            f"{message} - the chosen GPU appears out of stock in that data "
            "center on Secure Cloud right now. Try another GPU or data center."
        )
    return message


def _gpu_detail(gpu: GpuAvailability) -> Optional[str]:
    parts = []
    if gpu.memory_gb:
        parts.append(f"{gpu.memory_gb} GB")
    if gpu.stock_status:
        parts.append(f"{gpu.stock_status} stock")
    return " · ".join(parts) or None


def _gpu_union_detail(entries: List[GpuAvailability]) -> Optional[str]:
    """Detail for a `gpu_type_id` option spanning every data center that
    stocks it."""
    parts = []
    memory_gb = next((gpu.memory_gb for gpu in entries if gpu.memory_gb), None)
    if memory_gb:
        parts.append(f"{memory_gb} GB")
    count = len(entries)
    parts.append(f"available in {count} data center{'s' if count != 1 else ''}")
    best = _best_stock_status(entries)
    if best:
        parts.append(f"best: {best} stock")
    return " · ".join(parts) or None


def _best_stock_status(entries: List[GpuAvailability]) -> Optional[str]:
    ranked = [gpu for gpu in entries if (gpu.stock_status or "").strip().lower() in _STOCK_RANK]
    if not ranked:
        return None
    return max(ranked, key=lambda gpu: _STOCK_RANK[gpu.stock_status.strip().lower()]).stock_status


def _best_stocked_data_center(data_centers: List[LiveDataCenter], gpu_type_id: str) -> Optional[str]:
    """The data center to auto-pick for `gpu_type_id`: highest `stockStatus`
    first, then lowest data-center id for a deterministic tiebreak."""
    candidates = []
    for dc in data_centers:
        gpu = next((g for g in dc.gpus if g.gpu_type_id == gpu_type_id), None)
        if gpu is None:
            continue
        status = (gpu.stock_status or "").strip().lower()
        if status in _OUT_OF_STOCK:
            continue
        candidates.append((_STOCK_RANK.get(status, 0), dc.id))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (-pair[0], pair[1]))
    return candidates[0][1]


class RunpodComputeProvisioner(ComputeProvisioner):
    provider_id = "runpod"
    label = "RunPod"
    signup_url = "https://runpod.io/?ref=0y7tgfbp"
    signup_note = (
        "No RunPod account yet? Signing up through this link supports "
        "PotionUI's development at no extra cost to you."
    )

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

        # No live catalog, no form.
        if live_data_centers is None:
            raise ComputeProvisionerError(
                "RunPod's catalog is unreachable, so GPU availability can't be shown "
                f"({getattr(self, '_catalog_error', None) or 'unknown error'}). "
                "Check the API key in the RunPod Provider plugin settings and retry."
            )

        volumes = await self._account_volumes(settings.api_key)

        fields = [
            self._gpu_type_field(settings, live_data_centers),
            self._network_volume_field(volumes),
            self._data_center_field(settings, values, live_data_centers, volumes),
        ]
        if values.get("network_volume", NETWORK_VOLUME_CREATE) == NETWORK_VOLUME_CREATE:
            fields.append(
                ComputeFieldDescriptorV1(
                    key="volume_size_gb",
                    label="Volume Size (GB)",
                    type="number",
                    required=True,
                    default=settings.volume_size_gb,
                    help_text="Size of the persistent network volume models are cached on.",
                    depends_on=["network_volume"],
                )
            )
        return fields

    async def _account_volumes(self, api_key: str) -> List[NetworkVolume]:
        """The account's existing network volumes, or `[]` if listing fails -
        a degraded form (create/none only) rather than a hard refusal, since
        `describe_fields` already refuses loudly when the catalog itself is
        unreachable."""
        client = RunPodClient(api_key=api_key)
        try:
            return await client.list_network_volumes()
        except RunPodAPIError as exc:
            logger.warning("RunPod network volume listing unavailable: %s", exc)
            return []
        finally:
            await client.aclose()

    def _network_volume_field(self, volumes: List[NetworkVolume]) -> ComputeFieldDescriptorV1:
        options = [
            ComputeFieldOptionV1(value=NETWORK_VOLUME_CREATE, label="Create new volume"),
            ComputeFieldOptionV1(
                value=NETWORK_VOLUME_NONE,
                label="No persistent storage",
                detail="Models re-upload on every new pod.",
            ),
            *(
                ComputeFieldOptionV1(
                    value=volume.id,
                    label=volume.name,
                    detail=f"{volume.id} · {volume.size_gb} GB · {volume.data_center_id}",
                )
                for volume in volumes
            ),
        ]
        return ComputeFieldDescriptorV1(
            key="network_volume",
            label="Network Volume",
            type="select",
            required=True,
            default=NETWORK_VOLUME_CREATE,
            help_text="Persistent storage for cached models across pods.",
            options=options,
        )

    async def _live_data_centers(self, api_key: Optional[str]) -> Optional[List[LiveDataCenter]]:
        """The live catalog, or `None` with the reason on `self._catalog_error`."""
        self._catalog_error: Optional[str] = None
        if not api_key:
            self._catalog_error = "no RunPod API key configured"
            return None
        try:
            return await catalog_client.get_catalog(api_key)
        except RunPodCatalogError as exc:
            logger.warning("RunPod GraphQL catalog unavailable, using the static catalog instead: %s", exc)
            self._catalog_error = str(exc)
            return None

    async def _resolve_data_center_id(
        self,
        *,
        requested: Optional[str],
        gpu_type_id: str,
        pinned: Optional[str],
        settings: RunPodSettings,
    ) -> str:
        """Resolution order: an explicit choice wins, but must agree with
        `pinned` if given (the network volume being used, and the models on
        it, live in one data center for its lifetime). Absent an explicit
        choice, `pinned` wins; failing that, the live catalog auto-picks the
        best-stocked data center for `gpu_type_id`; failing that, the
        `region` plugin setting; failing that, a clean error naming the
        field."""
        if requested:
            if pinned and requested != pinned:
                raise ComputeProvisionerError(
                    f"This backend's network volume already lives in '{pinned}' - its models are "
                    "stored there. Choose that data center, or delete the existing volume before "
                    "provisioning in a different one."
                )
            return requested

        if pinned:
            return pinned

        live_data_centers = await self._live_data_centers(settings.api_key)
        if live_data_centers is not None:
            best = _best_stocked_data_center(live_data_centers, gpu_type_id)
            if best is not None:
                return best

        if settings.region:
            return settings.region

        raise ComputeProvisionerError(
            "'data_center_id' is required - RunPod's live catalog is unavailable, so a data "
            "center can't be picked automatically. Choose one explicitly, or set 'region' in "
            "the RunPod Provider plugin settings"
        )

    def _pinned_data_center(self, profile_name: str) -> Optional[str]:
        record = self._resources.get(profile_name, "network_volume")
        if record is None:
            return None
        return record.meta.get("data_center_id")

    def _gpu_type_field(
        self, settings: RunPodSettings, live_data_centers: List[LiveDataCenter]
    ) -> ComputeFieldDescriptorV1:
        """The primary field: a RunPod Pod can float with no data center
        pinned at all, so the GPU is what the admin actually chooses first -
        `data_center_id` narrows *from* it, not the other way around.
        Only ever called with a live catalog - `describe_fields` raises
        before this point when the catalog is unreachable."""
        # Union across every data center - a GPU only needs to be in stock
        # somewhere to be offered; `_data_center_field` narrows by data
        # center once one is picked.
        by_gpu: Dict[str, List[GpuAvailability]] = {}
        for dc in live_data_centers:
            for gpu in dc.gpus:
                if (gpu.stock_status or "").strip().lower() in _OUT_OF_STOCK:
                    continue
                by_gpu.setdefault(gpu.gpu_type_id, []).append(gpu)

        options = [
            ComputeFieldOptionV1(
                value=gpu_type_id,
                label=entries[0].gpu_type_display_name,
                detail=_gpu_union_detail(entries),
            )
            for gpu_type_id, entries in sorted(by_gpu.items(), key=lambda kv: kv[1][0].gpu_type_display_name)
        ]

        return ComputeFieldDescriptorV1(
            key="gpu_type_id",
            label="GPU Type",
            type="select",
            required=True,
            default=settings.gpu_type_id,
            help_text="The GPU RunPod starts the pod on.",
            options=options,
        )

    def _data_center_field(
        self,
        settings: RunPodSettings,
        values: Dict[str, Any],
        live_data_centers: List[LiveDataCenter],
        volumes: List[NetworkVolume],
    ) -> ComputeFieldDescriptorV1:
        """Only ever called with a live catalog - `describe_fields` raises
        before this point when the catalog is unreachable."""
        chosen_volume_id = values.get("network_volume")
        pinned_volume = None
        if chosen_volume_id and chosen_volume_id not in (NETWORK_VOLUME_CREATE, NETWORK_VOLUME_NONE):
            pinned_volume = next((v for v in volumes if v.id == chosen_volume_id), None)

        if pinned_volume is not None:
            return ComputeFieldDescriptorV1(
                key="data_center_id",
                label="Data Center",
                type="select",
                required=True,
                default=pinned_volume.data_center_id,
                help_text="Pinned by the selected volume.",
                depends_on=["gpu_type_id", "network_volume"],
                options=[ComputeFieldOptionV1(value=pinned_volume.data_center_id, label=pinned_volume.data_center_id)],
            )

        chosen_gpu = values.get("gpu_type_id")
        if not chosen_gpu:
            return ComputeFieldDescriptorV1(
                key="data_center_id",
                label="Data Center",
                type="select",
                required=False,
                default=None,
                help_text="Choose a GPU first.",
                depends_on=["gpu_type_id", "network_volume"],
                options=[],
            )

        scoped = []
        for dc in live_data_centers:
            gpu = next(
                (
                    g
                    for g in dc.gpus
                    if g.gpu_type_id == chosen_gpu and (g.stock_status or "").strip().lower() not in _OUT_OF_STOCK
                ),
                None,
            )
            if gpu is not None:
                scoped.append(ComputeFieldOptionV1(value=dc.id, label=dc.id, detail=_gpu_detail(gpu)))

        return ComputeFieldDescriptorV1(
            key="data_center_id",
            label="Data Center",
            type="select",
            required=False,
            default=None,
            help_text=(
                "Automatic picks the best-stocked data center for this GPU. The network volume "
                "(your models) is created there, and future pods for this backend stay there."
            ),
            depends_on=["gpu_type_id", "network_volume"],
            options=[
                ComputeFieldOptionV1(
                    value="", label="Automatic", detail="Picks the best-stocked data center for this GPU."
                ),
                *scoped,
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

        gpu_type_id = request.values.get("gpu_type_id") or settings.gpu_type_id
        if not gpu_type_id:
            raise ComputeProvisionerError("'gpu_type_id' is required - choose a GPU")

        network_volume = request.values.get("network_volume") or NETWORK_VOLUME_CREATE

        # A brand-new client only if an explicit existing volume needs
        # verifying up front - the create/none paths never touch RunPod
        # until data_center_id has resolved cleanly.
        client: Optional[RunPodClient] = None
        try:
            if network_volume == NETWORK_VOLUME_NONE:
                pinned = None
            elif network_volume == NETWORK_VOLUME_CREATE:
                pinned = self._pinned_data_center(request.profile_name)
            else:
                client = RunPodClient(api_key=settings.api_key)
                existing_volume = await client.get_network_volume(network_volume)
                pinned = existing_volume.data_center_id

            data_center_id = await self._resolve_data_center_id(
                requested=request.values.get("data_center_id") or None,
                gpu_type_id=gpu_type_id,
                pinned=pinned,
                settings=settings,
            )

            profile = ProvisioningProfile(
                name=request.profile_name,
                gpu_type_id=gpu_type_id,
                image_ref=image_ref,
                region=data_center_id,
                volume_size_gb=request.values.get("volume_size_gb") or settings.volume_size_gb,
                worker_port=_WORKER_PORT,
                container_disk_gb=_CONTAINER_DISK_GB,
                container_registry_auth_id=settings.container_registry_auth_id,
                allowed_cuda_versions=settings.allowed_cuda_versions,
                network_volume=network_volume,
            )

            if client is None:
                client = RunPodClient(api_key=settings.api_key)
            result = await self._manager(client).provision(profile)
        except RunPodAPIError as exc:
            raise ComputeProvisionerError(_with_capacity_hint(str(exc))) from exc
        finally:
            if client is not None:
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
