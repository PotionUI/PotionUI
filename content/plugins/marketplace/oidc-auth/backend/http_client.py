from __future__ import annotations

from typing import Optional

import httpx

REQUEST_TIMEOUT_SECONDS = 10.0

_transport_override: Optional[httpx.BaseTransport] = None


def set_transport_override(transport: Optional[httpx.BaseTransport]) -> None:
    global _transport_override
    _transport_override = transport


def new_client() -> httpx.AsyncClient:
    kwargs = {"timeout": REQUEST_TIMEOUT_SECONDS}
    if _transport_override is not None:
        kwargs["transport"] = _transport_override
    return httpx.AsyncClient(**kwargs)
