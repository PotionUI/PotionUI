import httpx
import pytest

from backend import catalog_client
from backend.catalog_client import (
    DEFAULT_CATALOG_URL,
    RunPodCatalogError,
    fetch_catalog,
    get_catalog,
)

CATALOG_PAYLOAD = {
    "gpus": [
        {
            "id": "NVIDIA GeForce RTX 4090",
            "name": "RTX 4090",
            "pool": "ADA_24",
            "manufacturer": "NVIDIA",
            "memory": 24,
            "secure": True,
            "community": True,
            "price": {"secure": 0.44, "community": 0.31},
            "maxCount": {"secure": 8, "community": 4},
            "availability": "HIGH",
            "dataCenters": [
                {"id": "US-TX-3", "name": "US Texas 3", "availability": "HIGH"},
                {"id": "EU-RO-1", "name": "EU Romania 1", "availability": "MEDIUM"},
            ],
        },
        {
            "id": "NVIDIA H100 80GB HBM3",
            "name": "H100 80GB",
            "pool": None,
            "manufacturer": "NVIDIA",
            "memory": 80,
            "secure": True,
            "community": False,
            "price": {"secure": 2.69, "community": 0.0},
            "maxCount": {"secure": 8, "community": 0},
            "availability": "NONE",
            "dataCenters": [{"id": "US-TX-3", "name": "US Texas 3", "availability": "NONE"}],
        },
        {
            "id": "NVIDIA RTX A4500",
            "name": "RTX A4500",
            "pool": None,
            "manufacturer": "NVIDIA",
            "memory": 20,
            "secure": True,
            "community": False,
            "price": {"secure": 0.19, "community": 0.0},
            "maxCount": {"secure": 4, "community": 0},
            "availability": "NONE",
        },
    ]
}


@pytest.fixture(autouse=True)
def _reset_cache():
    catalog_client.reset_cache()
    yield
    catalog_client.reset_cache()
    catalog_client._clock = __import__("time").monotonic


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_fetch_catalog_sends_bearer_auth_and_a_single_get_to_the_v2_catalog():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=CATALOG_PAYLOAD)

    result = await fetch_catalog("super-secret-key", transport=_transport(handler))

    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert DEFAULT_CATALOG_URL == "https://api.runpod.io/v2/catalog/gpus"
    assert calls[0].url.copy_with(query=None) == httpx.URL(DEFAULT_CATALOG_URL)
    assert calls[0].headers.get("authorization") == "Bearer super-secret-key"
    assert "api_key" not in calls[0].url.params
    assert len(result) == 2


async def test_fetch_catalog_scopes_availability_to_secure_cloud_pods():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=CATALOG_PAYLOAD)

    await fetch_catalog("key", transport=_transport(handler))

    params = calls[0].url.params
    assert params["include"] == "AVAILABILITY"
    assert params["product"] == "POD"
    assert params["cloud"] == "SECURE"


async def test_fetch_catalog_inverts_gpus_into_data_centers_with_memory_and_stock():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CATALOG_PAYLOAD)

    result = await fetch_catalog("key", transport=_transport(handler))

    tx = next(dc for dc in result if dc.id == "US-TX-3")
    assert tx.name == "US Texas 3"
    rtx = next(g for g in tx.gpus if g.gpu_type_id == "NVIDIA GeForce RTX 4090")
    assert rtx.memory_gb == 24
    assert rtx.stock_status == "High"
    assert rtx.gpu_type_display_name == "RTX 4090"
    h100 = next(g for g in tx.gpus if g.gpu_type_id == "NVIDIA H100 80GB HBM3")
    assert h100.stock_status == "None"
    assert h100.memory_gb == 80

    ro = next(dc for dc in result if dc.id == "EU-RO-1")
    assert [g.stock_status for g in ro.gpus] == ["Medium"]


async def test_fetch_catalog_surfaces_the_error_detail_on_http_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"title": "Unauthorized", "status": 401, "detail": "missing bearer token"}
        )

    with pytest.raises(RunPodCatalogError, match="missing bearer token"):
        await fetch_catalog("key", transport=_transport(handler))


async def test_fetch_catalog_raises_on_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(RunPodCatalogError):
        await fetch_catalog("key", transport=_transport(handler))


async def test_fetch_catalog_raises_on_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RunPodCatalogError):
        await fetch_catalog("key", transport=_transport(handler))


async def test_fetch_catalog_rejects_a_catalog_with_no_data_centers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"gpus": []})

    with pytest.raises(RunPodCatalogError, match="no data centers"):
        await fetch_catalog("rejected-key", transport=_transport(handler))


async def test_get_catalog_caches_within_the_ttl_window():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=CATALOG_PAYLOAD)

    fake_now = [1000.0]
    catalog_client._clock = lambda: fake_now[0]

    await get_catalog("key", transport=_transport(handler))
    fake_now[0] += 10.0
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
    fake_now[0] += 61.0
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
