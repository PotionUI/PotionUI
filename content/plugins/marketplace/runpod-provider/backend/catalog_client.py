"""A minimal, read-only GraphQL client for RunPod's live GPU-by-data-center
catalog.

RunPod's REST API (v1) has no endpoint to discover data centers or GPU
availability - the same honest-absence situation `client.py` and
`gpu_catalog.py`/`datacenter_catalog.py` document (verified by enumerating
`https://rest.runpod.io/v1/openapi.json`). That discovery exists only on
RunPod's GraphQL API (`https://api.runpod.io/graphql`, public schema at
graphql-spec.runpod.io), which this module speaks for catalog reads only -
every mutation (create/stop/terminate a pod, create/delete a volume) still
goes through `RunPodClient`'s REST v1 calls; this module never writes
anything. Same Bearer API key as REST.

Two things confirmed against the public schema, not guessed: data centers
are not a top-level `Query` field - they hang off `myself.dataCenters` - and
`gpuTypes` (which carries `memoryInGb`, absent from `gpuAvailability`) is a
sibling root field, so both come back in a single POST.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_GRAPHQL_URL = "https://api.runpod.io/graphql"
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Provisioning UIs re-describe fields on every dependency change (a data
#: center pick re-renders the GPU field) - this bounds how often that turns
#: into a real GraphQL round trip without going stale for an entire session.
CACHE_TTL_SECONDS = 60.0

_QUERY = """
query PotionUiComputeCatalog {
  myself {
    dataCenters {
      id
      name
      location
      gpuAvailability {
        stockStatus
        gpuTypeId
        gpuTypeDisplayName
      }
    }
  }
  gpuTypes {
    id
    displayName
    memoryInGb
  }
}
"""


class RunPodCatalogError(Exception):
    """The GraphQL catalog call failed (network, HTTP, or a GraphQL-level
    `errors` payload) - callers fall back to the static catalogs on this."""


@dataclass(frozen=True)
class GpuAvailability:
    gpu_type_id: str
    gpu_type_display_name: str
    stock_status: Optional[str]
    memory_gb: Optional[int]


@dataclass(frozen=True)
class DataCenter:
    id: str
    name: str
    location: Optional[str]
    gpus: List[GpuAvailability]


async def fetch_catalog(
    api_key: str,
    *,
    url: str = DEFAULT_GRAPHQL_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: Optional[httpx.BaseTransport] = None,
) -> List[DataCenter]:
    """One read-only POST to RunPod's GraphQL API. Raises `RunPodCatalogError`
    on any network/HTTP/GraphQL-level failure - never returns a partial
    result silently."""
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as http:
            response = await http.post(
                url,
                json={"query": _QUERY},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise RunPodCatalogError(f"connection error: {exc}") from exc

    if response.status_code >= 400:
        raise RunPodCatalogError(f"GraphQL HTTP error {response.status_code}: {response.text}")

    payload = response.json()
    if payload.get("errors"):
        raise RunPodCatalogError(f"GraphQL errors: {payload['errors']}")

    data = payload.get("data") or {}
    myself = data.get("myself") or {}
    raw_data_centers = myself.get("dataCenters") or []
    raw_gpu_types = data.get("gpuTypes") or []

    memory_by_id: Dict[str, Optional[int]] = {
        gpu["id"]: gpu.get("memoryInGb") for gpu in raw_gpu_types if gpu.get("id")
    }

    data_centers: List[DataCenter] = []
    for dc in raw_data_centers:
        if not dc.get("id"):
            continue
        gpus = [
            GpuAvailability(
                gpu_type_id=gpu["gpuTypeId"],
                gpu_type_display_name=gpu.get("gpuTypeDisplayName") or gpu["gpuTypeId"],
                stock_status=gpu.get("stockStatus"),
                memory_gb=memory_by_id.get(gpu["gpuTypeId"]),
            )
            for gpu in (dc.get("gpuAvailability") or [])
            if gpu.get("gpuTypeId")
        ]
        data_centers.append(
            DataCenter(id=dc["id"], name=dc.get("name") or dc["id"], location=dc.get("location"), gpus=gpus)
        )

    return data_centers


@dataclass
class _CacheEntry:
    fetched_at: float
    data_centers: List[DataCenter]


#: Keyed by api_key so an admin rotating the key never serves a stale
#: catalog fetched under the old one. `_clock` is swappable so tests can
#: control expiry without sleeping.
_cache: Dict[str, _CacheEntry] = {}
_clock = time.monotonic


def reset_cache() -> None:
    """Test-only: drop every cached entry."""
    _cache.clear()


async def get_catalog(
    api_key: str,
    *,
    url: str = DEFAULT_GRAPHQL_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: Optional[httpx.BaseTransport] = None,
) -> List[DataCenter]:
    """`fetch_catalog`, deduped across calls within `CACHE_TTL_SECONDS`."""
    entry = _cache.get(api_key)
    now = _clock()
    if entry is not None and (now - entry.fetched_at) < CACHE_TTL_SECONDS:
        return entry.data_centers

    data_centers = await fetch_catalog(api_key, url=url, timeout=timeout, transport=transport)
    _cache[api_key] = _CacheEntry(fetched_at=now, data_centers=data_centers)
    return data_centers
