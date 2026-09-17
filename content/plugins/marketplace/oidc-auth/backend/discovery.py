from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from .http_client import new_client

DEFAULT_CACHE_TTL_SECONDS = 3600.0
MIN_CACHE_TTL_SECONDS = 60.0
MAX_CACHE_TTL_SECONDS = 86400.0
UNKNOWN_KID_REFETCH_MIN_INTERVAL_SECONDS = 30.0


class DiscoveryError(Exception):
    pass


@dataclass
class _CacheEntry:
    value: Dict[str, Any]
    expires_at: float


_discovery_cache: Dict[str, _CacheEntry] = {}
_jwks_cache: Dict[str, _CacheEntry] = {}
_jwks_last_refetch: Dict[str, float] = {}


def reset_cache() -> None:
    _discovery_cache.clear()
    _jwks_cache.clear()
    _jwks_last_refetch.clear()


def _cache_ttl_seconds(response: httpx.Response) -> float:
    header = response.headers.get("cache-control", "")
    ttl = DEFAULT_CACHE_TTL_SECONDS
    for directive in header.split(","):
        directive = directive.strip().lower()
        if directive.startswith("max-age="):
            try:
                ttl = float(directive.split("=", 1)[1])
            except ValueError:
                pass
            break
    return min(MAX_CACHE_TTL_SECONDS, max(MIN_CACHE_TTL_SECONDS, ttl))


async def fetch_discovery(issuer_url: str) -> Dict[str, Any]:
    key = issuer_url.rstrip("/")
    now = time.monotonic()
    cached = _discovery_cache.get(key)
    if cached is not None and cached.expires_at > now:
        return cached.value

    async with new_client() as client:
        response = await client.get(f"{key}/.well-known/openid-configuration")
    response.raise_for_status()
    document = response.json()

    reported_issuer = str(document.get("issuer", "")).rstrip("/")
    if reported_issuer != key:
        raise DiscoveryError(
            f"discovery document issuer {reported_issuer!r} does not match configured issuer {key!r}"
        )

    _discovery_cache[key] = _CacheEntry(value=document, expires_at=now + _cache_ttl_seconds(response))
    return document


async def _fetch_jwks(jwks_uri: str) -> Dict[str, Any]:
    async with new_client() as client:
        response = await client.get(jwks_uri)
    response.raise_for_status()
    document = response.json()
    now = time.monotonic()
    _jwks_cache[jwks_uri] = _CacheEntry(value=document, expires_at=now + _cache_ttl_seconds(response))
    _jwks_last_refetch[jwks_uri] = now
    return document


def _find_kid(document: Dict[str, Any], kid: str, alg: str) -> Optional[Dict[str, Any]]:
    for key in document.get("keys", []):
        if key.get("kid") != kid:
            continue
        use = key.get("use")
        if use is not None and use != "sig":
            continue
        key_alg = key.get("alg")
        if key_alg is not None and key_alg != alg:
            continue
        return key
    return None


async def get_jwk(jwks_uri: str, kid: str, alg: str) -> Optional[Dict[str, Any]]:
    now = time.monotonic()
    cached = _jwks_cache.get(jwks_uri)
    document = cached.value if cached is not None and cached.expires_at > now else await _fetch_jwks(jwks_uri)

    found = _find_kid(document, kid, alg)
    if found is not None:
        return found

    last_refetch = _jwks_last_refetch.get(jwks_uri, 0.0)
    if now - last_refetch < UNKNOWN_KID_REFETCH_MIN_INTERVAL_SECONDS:
        return None

    document = await _fetch_jwks(jwks_uri)
    return _find_kid(document, kid, alg)
