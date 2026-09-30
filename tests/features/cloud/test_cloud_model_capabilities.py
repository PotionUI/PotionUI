import json
from dataclasses import replace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.features.cloud.routes import build_router
from src.features.cloud.testing.fake import fake_specs
from src.features.models.repository import model_repo
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.features.cloud.conftest import returning

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"


def user(account_type, user_id="u1"):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.test", password_hash="h", account_type=account_type)


def client_as(container, account, user_id="u1"):
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: user(account, user_id)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def add_user(db, user_id="u1"):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (user_id, user_id, f"{user_id}@example.test"),
        )


async def enabled_model(env, provider_model_id):
    slug = env.slug_of(provider_model_id)
    await env.catalog.set_enabled("cloud-1", [slug], True)
    return env.model_row(slug).id


def url(model_id):
    return f"/api/cloud/models/{model_id}/capabilities"


async def test_an_admin_gets_the_params_and_inputs_of_an_enabled_model(refreshed, container):
    model_id = await enabled_model(refreshed, IMAGE)

    async with client_as(container, AccountType.ADMIN) as client:
        response = await client.get(url(model_id))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["model_id"] == model_id and data["label"] == "Fake Image" and data["driver"] == "cloud.fake"
    assert data["tasks"] == ["txt2img", "img_edit"]
    assert data["outputs"] == ["image"]
    assert data["max_outputs_per_job"] == 4
    by_name = {param["name"]: param for param in data["params"]}
    assert by_name["aspect_ratio"]["kind"] == "enum" and by_name["aspect_ratio"]["values"] == ["1:1", "16:9"]
    assert by_name["aspect_ratio"]["default"] == "1:1"
    assert (by_name["quality"]["minimum"], by_name["quality"]["maximum"], by_name["quality"]["integer"]) == (1, 10, True)
    assert by_name["background"]["kind"] == "boolean"
    assert by_name["x.style"]["extra"] is True and by_name["aspect_ratio"]["extra"] is False
    (reference,) = data["inputs"]
    assert (reference["role"], reference["modality"], reference["max_items"], reference["tasks"]) == (
        "reference", "image", 4, ["img_edit"],
    )


async def test_the_response_lists_what_applies_to_each_task(refreshed, container):
    scoped = replace(fake_specs()[0], provider_model_id="fake/scoped-1", label="Scoped")
    params = tuple(
        replace(param, tasks=frozenset({"img_edit"})) if param.name == "quality" else param
        for param in scoped.params
    )
    refreshed.backend().provider.discover = returning([replace(scoped, params=params)])
    await refreshed.catalog.refresh("cloud-1")
    model_id = await enabled_model(refreshed, "fake/scoped-1")

    async with client_as(container, AccountType.ADMIN) as client:
        data = (await client.get(url(model_id))).json()["data"]

    assert "quality" not in data["by_task"]["txt2img"]["params"]
    assert "quality" in data["by_task"]["img_edit"]["params"]
    assert data["by_task"]["txt2img"]["inputs"] == []
    assert data["by_task"]["img_edit"]["inputs"] == ["reference"]


async def test_changing_the_model_changes_the_capabilities(refreshed, container):
    image_id = await enabled_model(refreshed, IMAGE)
    video_id = await enabled_model(refreshed, VIDEO)

    async with client_as(container, AccountType.ADMIN) as client:
        image = (await client.get(url(image_id))).json()["data"]
        video = (await client.get(url(video_id))).json()["data"]

    assert {p["name"] for p in image["params"]} != {p["name"] for p in video["params"]}
    assert "duration_s" in {p["name"] for p in video["params"]} and video["outputs"] == ["video"]
    assert video["inputs"][0]["role"] == "first_frame"


async def test_the_capabilities_carry_no_price(refreshed, container):
    model_id = await enabled_model(refreshed, IMAGE)

    async with client_as(container, AccountType.ADMIN) as client:
        text = json.dumps((await client.get(url(model_id))).json()).lower()

    for word in ("pricing", "price", "usd", "cost", "0.04"):
        assert word not in text


async def test_a_user_without_access_to_the_model_gets_a_404(refreshed, container, mock_db):
    add_user(mock_db)
    model_id = await enabled_model(refreshed, IMAGE)

    async with client_as(container, AccountType.USER) as client:
        response = await client.get(url(model_id))

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "model_not_found"


async def test_a_user_assigned_the_model_can_read_its_capabilities(refreshed, container, mock_db):
    add_user(mock_db)
    model_id = await enabled_model(refreshed, IMAGE)
    with mock_db.get_cursor() as cursor:
        cursor.execute("INSERT INTO user_models (id, user_id, model_id) VALUES ('um1', 'u1', ?)", (model_id,))

    async with client_as(container, AccountType.USER) as client:
        response = await client.get(url(model_id))

    assert response.status_code == 200


async def test_a_disabled_model_has_no_capabilities(refreshed, container):
    model_id = await enabled_model(refreshed, IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [refreshed.slug_of(IMAGE)], False)

    async with client_as(container, AccountType.ADMIN) as client:
        response = await client.get(url(model_id))

    assert response.status_code == 404


async def test_a_model_the_provider_dropped_has_no_capabilities(refreshed, container):
    model_id = await enabled_model(refreshed, IMAGE)
    refreshed.backend().provider.discover = returning([spec for spec in fake_specs() if spec.provider_model_id != IMAGE])
    await refreshed.catalog.refresh("cloud-1")

    async with client_as(container, AccountType.ADMIN) as client:
        response = await client.get(url(model_id))

    assert response.status_code == 404


async def test_a_depot_model_and_an_unknown_id_are_404(refreshed, container):
    from src.features.models.records import Model

    lora = model_repo.create(Model(filename="a.safetensors", model_type="lora"))

    async with client_as(container, AccountType.ADMIN) as client:
        depot = await client.get(url(lora.id))
        unknown = await client.get(url("nope"))

    assert depot.status_code == 404 and unknown.status_code == 404


async def test_the_driver_query_narrows_to_that_providers_backends(refreshed, container):
    model_id = await enabled_model(refreshed, IMAGE)

    async with client_as(container, AccountType.ADMIN) as client:
        right = await client.get(url(model_id), params={"driver": "cloud.fake"})
        wrong = await client.get(url(model_id), params={"driver": "cloud.other"})

    assert right.status_code == 200 and wrong.status_code == 404


async def test_the_capabilities_follow_the_routed_backends_catalog_entry(refreshed, container):
    model_id = await enabled_model(refreshed, IMAGE)
    changed = replace(fake_specs()[0], max_outputs_per_job=2)
    refreshed.backend().provider.discover = returning([changed, fake_specs()[1]])
    await refreshed.catalog.refresh("cloud-1")

    async with client_as(container, AccountType.ADMIN) as client:
        data = (await client.get(url(model_id))).json()["data"]

    assert data["max_outputs_per_job"] == 2


async def test_a_depot_model_sharing_a_cloud_slug_never_gets_the_cloud_capabilities(refreshed, container):
    from src.features.models.records import Model

    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    impostor = model_repo.create(Model(filename=slug, model_type="lora"))

    async with client_as(container, AccountType.ADMIN) as client:
        response = await client.get(url(impostor.id))

    assert response.status_code == 404
