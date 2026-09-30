import json
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.features.cloud.contracts import CloudError
from src.features.cloud.routes import build_router
from src.features.models.availability import models_for_engine
from src.features.models.repository import model_repo
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.features.cloud.conftest import returning
from src.features.cloud.testing.fake import fake_specs

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"
BASE = "/api/cloud/backends/cloud-1/catalog"


def user(account_type):
    return User(id="u1", username="u", email="u@example.test", password_hash="h", account_type=account_type)


def client_as(container, account_type):
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: user(account_type)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def as_admin(container):
    return client_as(container, AccountType.ADMIN)


def as_user(container):
    return client_as(container, AccountType.USER)


ROUTES = [
    ("get", BASE, None),
    ("post", f"{BASE}/refresh", None),
    ("post", f"{BASE}/enable", {"slugs": ["x"]}),
    ("post", f"{BASE}/disable", {"slugs": ["x"]}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=[f"{m} {p.rsplit('/', 1)[-1]}" for m, p, _ in ROUTES])
async def test_a_regular_user_is_refused_every_catalog_endpoint(container, method, path, body):
    async with as_user(container) as client:
        response = await client.request(method, path, json=body)

    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=[f"{m} {p.rsplit('/', 1)[-1]}" for m, p, _ in ROUTES])
async def test_a_refused_request_never_reaches_the_catalog(cloud_env, method, path, body):
    catalog = Mock()
    container = Mock(cloud_catalog=catalog)

    async with as_user(container) as client:
        await client.request(method, path, json=body)

    assert catalog.method_calls == []


async def test_an_admin_lists_the_catalog_with_prices_and_the_provider_notice(refreshed, container):
    async with as_admin(container) as client:
        response = await client.get(BASE)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2 and data["counts"] == {"total": 2, "enabled": 0, "missing": 0}
    assert data["provider"]["data_notice"]
    by_id = {item["provider_model_id"]: item for item in data["items"]}
    assert by_id[IMAGE]["pricing"] == [{"unit": "image", "usd": "0.04", "applies_to": None}]
    assert by_id[VIDEO]["pricing"][0]["usd"] == "0.40"
    assert by_id[IMAGE]["tasks"] == ["img_edit", "txt2img"]
    assert by_id[IMAGE]["enabled"] is False and by_id[IMAGE]["available"] is True


async def test_the_admin_listing_applies_filters_from_the_query_string(refreshed, container):
    async with as_admin(container) as client:
        response = await client.get(BASE, params={"task": "txt2video", "output": "video", "enabled": "false", "search": "fake"})

    items = response.json()["data"]["items"]
    assert [item["provider_model_id"] for item in items] == [VIDEO]


async def test_an_unknown_filter_value_is_a_validation_error(refreshed, container):
    async with as_admin(container) as client:
        response = await client.get(BASE, params={"task": "juggling"})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "cloud_catalog_filter_invalid"


async def test_listing_an_unknown_backend_is_a_404(cloud_env, container):
    async with as_admin(container) as client:
        response = await client.get("/api/cloud/backends/nope/catalog")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "cloud_backend_not_found"


async def test_an_admin_refreshes_enables_and_disables_through_the_api(fake_backend, container):
    async with as_admin(container) as client:
        refresh = await client.post(f"{BASE}/refresh")
        slugs = [item["slug"] for item in (await client.get(BASE)).json()["data"]["items"]]
        enable = await client.post(f"{BASE}/enable", json={"slugs": slugs})
        listed = await client.get(BASE, params={"enabled": "true"})
        disable = await client.post(f"{BASE}/disable", json={"slugs": slugs[:1]})
        after = await client.get(BASE, params={"enabled": "true"})

    assert refresh.json()["data"]["accepted"] == 2
    assert sorted(enable.json()["data"]["changed"]) == sorted(slugs)
    assert len(listed.json()["data"]["items"]) == 2
    assert all(item["model_id"] for item in listed.json()["data"]["items"])
    assert disable.json()["data"]["changed"] == slugs[:1]
    assert len(after.json()["data"]["items"]) == 1


async def test_enabling_an_unknown_slug_is_a_404_and_changes_nothing(refreshed, container):
    async with as_admin(container) as client:
        response = await client.post(f"{BASE}/enable", json={"slugs": ["fake~nope"]})
        counts = (await client.get(BASE)).json()["data"]["counts"]

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "cloud_entry_not_found"
    assert counts["enabled"] == 0


async def test_an_empty_selection_is_rejected(refreshed, container):
    async with as_admin(container) as client:
        response = await client.post(f"{BASE}/enable", json={"slugs": []})

    assert response.status_code == 422


async def test_a_provider_failure_while_refreshing_is_a_502_with_a_safe_message(refreshed, container):
    async def failing():
        raise CloudError("auth", "The provider rejected the API key.", detail="HTTP 401 Bearer sk-secret")

    refreshed.backend().provider.discover = failing

    async with as_admin(container) as client:
        response = await client.post(f"{BASE}/refresh")

    assert response.status_code == 502
    body = json.dumps(response.json())
    assert "cloud_auth" in body and "rejected the API key" in body
    assert "sk-secret" not in body


async def test_an_unexpected_failure_while_refreshing_does_not_leak_its_text(refreshed, container):
    async def exploding():
        raise RuntimeError("/srv/internal/.secret/path")

    refreshed.backend().provider.discover = exploding

    async with as_admin(container) as client:
        response = await client.post(f"{BASE}/refresh")

    assert response.status_code == 500
    assert ".secret" not in json.dumps(response.json())


async def test_refreshing_a_disabled_backend_is_a_409(cloud_env, container):
    from src.features.cloud.testing.fake import FakeCloudConfig

    await cloud_env.registry.add_backend(FakeCloudConfig(id="cloud-1", name="Off", enabled=False))

    async with as_admin(container) as client:
        response = await client.post(f"{BASE}/refresh")

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "cloud_backend_inactive"


PRICE_WORDS = ("pricing", "price", "usd", "cost", "0.04", "0.40")


def assert_no_price(payload):
    text = json.dumps(payload, default=str).lower()
    for word in PRICE_WORDS:
        assert word not in text, word


async def test_the_picker_payload_for_users_carries_no_price(refreshed):
    await refreshed.catalog.set_enabled("cloud-1", [refreshed.slug_of(IMAGE)], True)

    for admin in (False, True):
        entries = models_for_engine("cloud", refreshed.registry, model_type="cloud", admin=admin)
        assert entries
        assert_no_price(entries)


async def test_the_model_rows_and_their_providers_carry_no_price(refreshed):
    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    model = model_repo.get_by_identity("cloud", slug)

    for admin in (False, True):
        assert_no_price(model.to_dict(include_providers=True, admin=admin))


async def test_the_model_library_listing_for_a_user_carries_no_price(refreshed):
    await refreshed.catalog.set_enabled("cloud-1", [refreshed.slug_of(IMAGE)], True)

    rows = model_repo.get_all(model_type="cloud")

    assert rows
    assert_no_price([row.to_dict(include_providers=True, admin=False) for row in rows])


async def test_the_backend_system_info_carries_no_price(refreshed):
    await refreshed.catalog.set_enabled("cloud-1", [refreshed.slug_of(IMAGE)], True)

    assert_no_price(await refreshed.backend().get_system_info())
    assert_no_price(await refreshed.backend().health_check())


async def test_the_engine_descriptor_carries_no_price(refreshed):
    assert_no_price(refreshed.registry.get_engine_descriptors())


async def test_enabling_a_model_the_provider_dropped_is_a_409_naming_the_entry(refreshed, container):
    slug = refreshed.slug_of(VIDEO)
    refreshed.backend().provider.discover = returning([spec for spec in fake_specs() if spec.provider_model_id != VIDEO])
    await refreshed.catalog.refresh("cloud-1")

    async with as_admin(container) as client:
        response = await client.post(f"{BASE}/enable", json={"slugs": [slug]})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "cloud_entry_unavailable"
    assert slug in response.json()["detail"]["message"]


async def test_enabling_on_an_inactive_backend_is_a_409(cloud_env, container):
    from src.features.cloud.testing.fake import FakeCloudConfig

    await cloud_env.registry.add_backend(FakeCloudConfig(id="cloud-1", name="Off", enabled=False))

    async with as_admin(container) as client:
        response = await client.post(f"{BASE}/enable", json={"slugs": ["fake~x"]})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "cloud_backend_inactive"


async def test_the_refresh_response_flags_an_empty_provider_answer(refreshed, container):
    refreshed.backend().provider.discover = returning([])

    async with as_admin(container) as client:
        response = await client.post(f"{BASE}/refresh")

    assert response.status_code == 200
    assert response.json()["data"]["empty"] is True
    assert "nothing was changed" in response.json()["data"]["message"]
