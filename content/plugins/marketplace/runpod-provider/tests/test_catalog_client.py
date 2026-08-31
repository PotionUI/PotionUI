"""`backend.catalog_client` against a fake RunPod GraphQL endpoint
(`httpx.MockTransport` - no real network, no `runpod` SDK)."""

import httpx
import pytest

from backend import catalog_client
from backend.catalog_client import (
    DEFAULT_GRAPHQL_URL,
    RunPodCatalogError,
    fetch_catalog,
    get_catalog,
)

CATALOG_PAYLOAD = {
    "data": {
        "myself": {
            "dataCenters": [
                {
                    "id": "US-TX-3",
                    "name": "Texas",
                    "location": "United States",
                    "gpuAvailability": [
                        {
                            "stockStatus": "High",
                            "gpuTypeId": "NVIDIA GeForce RTX 4090",
                            "gpuTypeDisplayName": "RTX 4090",
                        },
                        {
                            "stockStatus": "None",
                            "gpuTypeId": "NVIDIA H100 80GB HBM3",
                            "gpuTypeDisplayName": "H100 80GB",
                        },
                    ],
                },
                {
                    "id": "EU-RO-1",
                    "name": "Bucharest",
                    "location": "Romania",
                    "gpuAvailability": [
                        {
                            "stockStatus": "Medium",
                            "gpuTypeId": "NVIDIA GeForce RTX 4090",
                            "gpuTypeDisplayName": "RTX 4090",
                        },
                    ],
                },
            ]
        },
        "gpuTypes": [
            {"id": "NVIDIA GeForce RTX 4090", "displayName": "RTX 4090", "memoryInGb": 24},
            {"id": "NVIDIA H100 80GB HBM3", "displayName": "H100 80GB", "memoryInGb": 80},
        ],
    }
}


@pytest.fixture(autouse=True)
def _reset_cache():
    catalog_client.reset_cache()
    yield
    catalog_client.reset_cache()
    catalog_client._clock = __import__("time").monotonic


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_fetch_catalog_sends_bearer_auth_and_a_single_post():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=CATALOG_PAYLOAD)

    result = await fetch_catalog("super-secret-key", transport=_transport(handler))

    assert len(calls) == 1
    assert calls[0].url == httpx.URL(DEFAULT_GRAPHQL_URL)
    assert calls[0].headers.get("authorization") == "Bearer super-secret-key"
    assert len(result) == 2


async def test_fetch_catalog_joins_memory_in_gb_from_the_gputypes_query():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CATALOG_PAYLOAD)

    result = await fetch_catalog("key", transport=_transport(handler))

    tx = next(dc for dc in result if dc.id == "US-TX-3")
    rtx = next(g for g in tx.gpus if g.gpu_type_id == "NVIDIA GeForce RTX 4090")
    assert rtx.memory_gb == 24
    assert rtx.stock_status == "High"


async def test_fetch_catalog_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(RunPodCatalogError):
        await fetch_catalog("key", transport=_transport(handler))


async def test_fetch_catalog_raises_on_graphql_errors_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "not authorized"}]})

    with pytest.raises(RunPodCatalogError):
        await fetch_catalog("key", transport=_transport(handler))


async def test_fetch_catalog_raises_on_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RunPodCatalogError):
        await fetch_catalog("key", transport=_transport(handler))


async def test_get_catalog_caches_within_the_ttl_window():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=CATALOG_PAYLOAD)

    fake_now = [1000.0]
    catalog_client._clock = lambda: fake_now[0]

    await get_catalog("key", transport=_transport(handler))
    fake_now[0] += 10.0  # well inside the 60s window
    await get_catalog("key", transport=_transport(handler))

    assert len(calls) == 1


async def test_get_catalog_refetches_after_the_ttl_expires():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=CATALOG_PAYLOAD)

    fake_now = [1000.0]
    catalog_client._clock = lambda: fake_now[0]

    await get_catalog("key", transport=_transport(handler))
    fake_now[0] += 61.0  # past the 60s window
    await get_catalog("key", transport=_transport(handler))

    assert len(calls) == 2


async def test_get_catalog_keys_the_cache_by_api_key():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=CATALOG_PAYLOAD)

    fake_now = [1000.0]
    catalog_client._clock = lambda: fake_now[0]

    await get_catalog("key-one", transport=_transport(handler))
    await get_catalog("key-two", transport=_transport(handler))

    assert len(calls) == 2
