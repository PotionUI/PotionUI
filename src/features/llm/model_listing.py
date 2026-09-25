from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

DISCOVERY_TIMEOUT_SECONDS = 8.0


class DiscoveredModel(BaseModel):
    id: str
    label: Optional[str] = None
    size: Optional[int] = None
    modified_at: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class ModelListingError(Exception):
    pass


def normalize_base_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ModelListingError("Base URL must be an http:// or https:// address.")
    return url


async def fetch_json(provider: str, base_url: str, path: str, headers: Optional[Dict[str, str]] = None) -> Any:
    url = normalize_base_url(base_url)
    target = f"{provider} at {url}"
    try:
        async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.get(f"{url}{path}", headers=headers or {})
    except httpx.ConnectError:
        raise ModelListingError(f"Couldn't reach {target}: connection refused.")
    except httpx.TimeoutException:
        raise ModelListingError(
            f"Couldn't reach {target}: timed out after {DISCOVERY_TIMEOUT_SECONDS:g}s."
        )
    except httpx.HTTPError as exc:
        raise ModelListingError(f"Couldn't reach {target}: {type(exc).__name__}.")
    if response.status_code in (401, 403):
        raise ModelListingError(
            f"{provider} at {url} rejected the credentials (HTTP {response.status_code}). Check the API key."
        )
    if response.status_code == 404:
        raise ModelListingError(f"{provider} at {url} has no model list at {path} (HTTP 404). Check the base URL.")
    if response.status_code >= 400:
        raise ModelListingError(f"{provider} at {url} answered HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError:
        raise ModelListingError(f"{provider} at {url} returned a response that isn't JSON.")


def sorted_models(models: List[DiscoveredModel]) -> List[DiscoveredModel]:
    return sorted(models, key=lambda m: m.id.lower())
