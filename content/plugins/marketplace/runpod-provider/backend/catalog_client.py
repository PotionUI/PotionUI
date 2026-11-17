from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from src.plugin_api.providers import client_ssl_context

from .client import DEFAULT_BASE_URL, _error_message

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_URL = f"{DEFAULT_BASE_URL}/catalog/gpus"
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Cloudflare 403s the default python-httpx User-Agent before auth runs.
USER_AGENT = "PotionUI (+https://github.com/PotionUI/PotionUI)"

CACHE_TTL_SECONDS = 60.0

_PARAMS = {"include": "AVAILABILITY", "product": "POD", "cloud": "SECURE"}


class RunPodCatalogError(Exception):
    pass


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
    url: str = DEFAULT_CATALOG_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: Optional[httpx.BaseTransport] = None,
) -> List[DataCenter]:
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport, verify=client_ssl_context()) as http:
            response = await http.get(
                url,
                params=_PARAMS,
                headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
            )
    except httpx.HTTPError as exc:
        raise RunPodCatalogError(f"connection error: {exc}") from exc

    if response.status_code >= 400:
        raise RunPodCatalogError(f"catalog HTTP error {response.status_code}: {_error_message(response)}")

    try:
        raw_gpus = (response.json() or {}).get("gpus") or []
    except ValueError as exc:
        raise RunPodCatalogError("catalog returned a non-JSON body") from exc

    by_data_center: Dict[str, Dict[str, Any]] = {}
    for gpu in raw_gpus:
        gpu_id = gpu.get("id")
        if not gpu_id:
            continue
        for dc in gpu.get("dataCenters") or []:
            if not dc.get("id"):
                continue
            entry = by_data_center.setdefault(dc["id"], {"name": dc.get("name") or dc["id"], "gpus": []})
            entry["gpus"].append(
                GpuAvailability(
                    gpu_type_id=gpu_id,
                    gpu_type_display_name=gpu.get("name") or gpu_id,
                    stock_status=(dc.get("availability") or "").title() or None,
                    memory_gb=gpu.get("memory"),
                )
            )

    if not by_data_center:
        raise RunPodCatalogError(
            "the catalog listed no data centers with GPU availability - the API key was "
            "not accepted by the catalog endpoint or the account has no visibility"
        )

    return [
        DataCenter(id=dc_id, name=entry["name"], location=None, gpus=entry["gpus"])
        for dc_id, entry in by_data_center.items()
    ]


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
    url: str = DEFAULT_CATALOG_URL,
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
