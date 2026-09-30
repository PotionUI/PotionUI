import json
from unittest.mock import MagicMock

import pytest

from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.availability import models_for_engine
from src.features.models.catalog import ListModelsParams, ModelCatalog
from src.features.models.records import Model, ModelInfo
from src.features.models.repository import model_repo
from src.platform.security.user import AccountType

IMAGE = "fake/image-1"
PRICE_WORDS = ("pricing", "price", "usd", "cost", "0.04")


def user(account_type, user_id="u1"):
    return MagicMock(account_type=account_type, id=user_id)


def add_user(db, user_id="u1"):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (user_id, user_id, f"{user_id}@example.test"),
        )


def assign(db, model_id, user_id="u1"):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO user_models (id, user_id, model_id) VALUES (?, ?, ?)", (f"um-{model_id}", user_id, model_id)
        )


def catalog_for(env):
    scanner = MagicMock()
    scanner.MODEL_TYPE_MAPPING = {}
    scanner.get_indexing_status.return_value = {}
    return ModelCatalog(
        model_repo, ModelAccessPolicy(model_repo), scanner,
        user_attribute_repository=MagicMock(get_maps=MagicMock(return_value={}), get_map=MagicMock(return_value={})),
        locator=MagicMock(locations=MagicMock(return_value=[]), location_summaries=MagicMock(return_value={})),
        backend_registry=env.registry,
    )


async def enabled(env):
    slug = env.slug_of(IMAGE)
    await env.catalog.set_enabled("cloud-1", [slug], True)
    return slug, env.model_row(slug).id


def assert_no_price(payload):
    text = json.dumps(payload, default=str).lower()
    for word in PRICE_WORDS:
        assert word not in text, word


@pytest.mark.parametrize("admin", [False, True])
async def test_the_picker_listing_carries_the_provider_label_and_vendor(refreshed, admin):
    await enabled(refreshed)

    (entry,) = models_for_engine("cloud", refreshed.registry, model_type="cloud", admin=admin)

    assert (entry["provider_label"], entry["vendor"]) == ("Fake cloud", "fake")
    assert_no_price(entry)


async def test_a_users_library_rows_carry_the_provider_label_and_vendor(refreshed, mock_db):
    add_user(mock_db)
    slug, model_id = await enabled(refreshed)
    assign(mock_db, model_id)

    result = catalog_for(refreshed).list_models(ListModelsParams(model_type="cloud", limit=10), user(AccountType.USER))

    (row,) = result["models"]
    assert row["id"] == model_id and (row["provider_label"], row["vendor"]) == ("Fake cloud", "fake")
    assert_no_price(result["models"])


async def test_an_admins_library_rows_carry_them_too(refreshed):
    await enabled(refreshed)

    result = catalog_for(refreshed).list_models(ListModelsParams(model_type="cloud", limit=10, all_models=True), user(AccountType.ADMIN))

    assert (result["models"][0]["provider_label"], result["models"][0]["vendor"]) == ("Fake cloud", "fake")


@pytest.mark.parametrize("account", [AccountType.USER, AccountType.ADMIN])
async def test_get_by_id_carries_them_for_users_and_admins(refreshed, mock_db, account):
    add_user(mock_db)
    _, model_id = await enabled(refreshed)
    assign(mock_db, model_id)

    payload = catalog_for(refreshed).get_model_by_id(model_id, user=user(account))["model"]

    assert (payload["provider_label"], payload["vendor"]) == ("Fake cloud", "fake")
    assert_no_price(payload)


async def test_the_rest_of_the_payload_keeps_its_shape(refreshed, mock_db):
    add_user(mock_db)
    _, model_id = await enabled(refreshed)
    assign(mock_db, model_id)
    plain = model_repo.get_by_id(model_id, include_providers=False).to_dict(include_providers=False, admin=False)

    payload = catalog_for(refreshed).get_model_by_id(model_id, user=user(AccountType.USER))["model"]

    assert set(payload) - set(plain) == {"provider_label", "vendor", "user_model_metadata"}
    assert {key: payload[key] for key in plain} == plain


async def test_non_cloud_models_get_neither_field_anywhere(refreshed, mock_db):
    add_user(mock_db)
    lora = model_repo.create(Model(filename="a.safetensors", model_type="lora"))
    assign(mock_db, lora.id)
    catalog = catalog_for(refreshed)

    listed = catalog.list_models(ListModelsParams(model_type="lora", limit=10), user(AccountType.USER))["models"][0]
    single = catalog.get_model_by_id(lora.id, user=user(AccountType.USER))["model"]

    for payload in (listed, single):
        assert "provider_label" not in payload and "vendor" not in payload


async def test_a_model_of_a_provider_that_is_not_installed_has_a_null_label(refreshed, mock_db):
    ghost = model_repo.create(Model(filename="ghost~thing", model_type="cloud"))
    model_repo.upsert_provider(ghost.id, ModelInfo(provider="cloud.ghost", provider_model_id="ghost/thing", name="Thing", tags=[]))

    payload = catalog_for(refreshed).get_model_by_id(ghost.id, user=user(AccountType.ADMIN))["model"]

    assert payload["provider_label"] is None and payload["vendor"] is None


async def test_a_cloud_model_without_a_provider_row_falls_back_to_its_slug_prefix(refreshed):
    bare = model_repo.create(Model(filename="fake~bare", model_type="cloud"))

    payload = catalog_for(refreshed).get_model_by_id(bare.id, user=user(AccountType.ADMIN))["model"]

    assert payload["provider_label"] == "Fake cloud" and payload["vendor"] is None


async def test_the_label_follows_the_registered_provider_class(refreshed):
    slug, model_id = await enabled(refreshed)
    refreshed.registry.get_registered_backend_types()["cloud.fake"].provider_class.label = "Renamed Cloud"
    try:
        payload = catalog_for(refreshed).get_model_by_id(model_id, user=user(AccountType.ADMIN))["model"]
    finally:
        refreshed.registry.get_registered_backend_types()["cloud.fake"].provider_class.label = "Fake cloud"

    assert payload["provider_label"] == "Renamed Cloud"
