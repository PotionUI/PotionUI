from dataclasses import replace
from decimal import Decimal

import pytest

from src.features.cloud.contracts import CloudError, CloudModelSpec, ParamSpec
from src.features.cloud.errors import (
    CloudBackendInactive,
    CloudBackendNotFound,
    CloudCatalogFilterError,
    CloudEntryNotFound,
    CloudEntryUnavailable,
)
from src.features.cloud.slug import cloud_model_slug
from src.features.cloud.spec_codec import spec_from_json, spec_to_json
from src.features.cloud.testing.fake import fake_specs
from tests.features.cloud.conftest import returning

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"


def specs_without(*ids):
    return [spec for spec in fake_specs() if spec.provider_model_id not in ids]


def test_a_slug_has_no_slash_and_only_safe_characters():
    slug = cloud_model_slug("openrouter", "Google/Veo-3.1:Free_Tier")
    assert slug == "openrouter~google~veo-3.1free_tier"
    assert "/" not in slug and "\\" not in slug


def test_a_model_spec_survives_a_round_trip_through_the_catalog_column():
    for spec in fake_specs():
        assert spec_from_json(spec_to_json(spec)) == spec


def test_a_price_keeps_its_exact_decimal_value_through_the_catalog_column():
    spec = spec_from_json(spec_to_json(fake_specs()[0]))
    assert spec.pricing[0].usd == Decimal("0.04")


async def test_refresh_fills_the_catalog_and_enables_nothing(fake_backend):
    report = await fake_backend.catalog.refresh("cloud-1")

    assert report["listed"] == 2 and report["accepted"] == 2 and report["created"] == 2
    page = fake_backend.catalog.list_entries("cloud-1")
    assert page["total"] == 2
    assert page["counts"] == {"total": 2, "enabled": 0, "missing": 0}
    assert all(item["enabled"] is False for item in page["items"])


async def test_nothing_is_offered_to_users_until_an_admin_enables_it(refreshed):
    assert await refreshed.backend().list_models() == []


async def test_a_second_refresh_updates_entries_without_duplicating_or_disabling_them(refreshed):
    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)

    report = await refreshed.catalog.refresh("cloud-1")

    assert report["created"] == 0
    page = refreshed.catalog.list_entries("cloud-1")
    assert page["total"] == 2
    assert {item["slug"]: item["enabled"] for item in page["items"]}[slug] is True


async def test_a_refresh_picks_up_a_changed_label(refreshed):
    slug = refreshed.slug_of(IMAGE)
    changed = [replace(spec, label="Renamed") if spec.provider_model_id == IMAGE else spec for spec in fake_specs()]
    refreshed.backend().provider.discover = returning(changed)

    await refreshed.catalog.refresh("cloud-1")

    items = {item["slug"]: item for item in refreshed.catalog.list_entries("cloud-1")["items"]}
    assert items[slug]["label"] == "Renamed"


async def test_a_model_that_vanishes_is_marked_unavailable_and_kept(refreshed):
    refreshed.backend().provider.discover = returning(specs_without(VIDEO))

    report = await refreshed.catalog.refresh("cloud-1")

    slug = refreshed.slug_of(VIDEO)
    assert report["vanished"] == [slug]
    items = {item["slug"]: item for item in refreshed.catalog.list_entries("cloud-1")["items"]}
    assert items[slug]["available"] is False
    assert items[slug]["missing_since"] is not None
    assert refreshed.catalog.list_entries("cloud-1")["counts"]["missing"] == 1


async def test_a_vanished_model_that_returns_becomes_available_again(refreshed):
    provider = refreshed.backend().provider
    original = provider.discover
    provider.discover = returning(specs_without(VIDEO))
    await refreshed.catalog.refresh("cloud-1")
    provider.discover = original

    await refreshed.catalog.refresh("cloud-1")

    items = {item["slug"]: item for item in refreshed.catalog.list_entries("cloud-1")["items"]}
    assert items[refreshed.slug_of(VIDEO)]["available"] is True
    assert items[refreshed.slug_of(VIDEO)]["missing_since"] is None


async def test_an_invalid_spec_is_skipped_and_reported_while_the_rest_are_kept(fake_backend):
    broken = CloudModelSpec(
        provider_model_id="fake/broken",
        label="Broken",
        tasks=frozenset({"not-a-task"}),
        outputs=frozenset({"image"}),
        params=(ParamSpec(name="not_canonical", kind="boolean"),),
    )
    fake_backend.backend().provider.discover = returning([*fake_specs(), broken])

    report = await fake_backend.catalog.refresh("cloud-1")

    assert report["accepted"] == 2
    assert [item["provider_model_id"] for item in report["skipped"]] == ["fake/broken"]
    assert any("not-a-task" in problem for problem in report["skipped"][0]["problems"])
    assert fake_backend.catalog.list_entries("cloud-1")["total"] == 2
    assert fake_backend.catalog.list_entries("cloud-1")["state"]["skipped"] == report["skipped"]


async def test_two_provider_ids_that_share_a_slug_keep_only_the_first(fake_backend):
    twin_a = replace(fake_specs()[0], provider_model_id="fake/model:a")
    twin_b = replace(fake_specs()[0], provider_model_id="fake/modela")
    fake_backend.backend().provider.discover = returning([twin_b, twin_a])

    report = await fake_backend.catalog.refresh("cloud-1")

    assert report["accepted"] == 1
    assert [item["provider_model_id"] for item in report["skipped"]] == ["fake/modela"]
    assert "already used" in report["skipped"][0]["problems"][0]


async def test_a_slug_that_already_belongs_to_another_model_is_not_handed_over(fake_backend):
    first = replace(fake_specs()[0], provider_model_id="fake/model:a")
    fake_backend.backend().provider.discover = returning([first])
    await fake_backend.catalog.refresh("cloud-1")
    imposter = replace(fake_specs()[0], provider_model_id="fake/modela")
    fake_backend.backend().provider.discover = returning([imposter])

    report = await fake_backend.catalog.refresh("cloud-1")

    assert report["accepted"] == 0
    assert "already belongs" in report["skipped"][0]["problems"][0]
    assert fake_backend.repository.provider_ids("cloud-1") == {"fake~fake~modela": "fake/model:a"}


async def test_a_provider_failure_during_discovery_reaches_the_caller_and_changes_nothing(refreshed):
    async def failing():
        raise CloudError("auth", "The provider rejected the API key.")

    refreshed.backend().provider.discover = failing

    with pytest.raises(CloudError):
        await refreshed.catalog.refresh("cloud-1")

    assert refreshed.catalog.list_entries("cloud-1")["counts"] == {"total": 2, "enabled": 0, "missing": 0}


async def test_the_listing_filters_by_task_output_enabled_and_search(refreshed):
    image_slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [image_slug], True)

    def slugs(**filters):
        return [item["slug"] for item in refreshed.catalog.list_entries("cloud-1", **filters)["items"]]

    assert slugs(task="txt2video") == [refreshed.slug_of(VIDEO)]
    assert slugs(task="img_edit") == [image_slug]
    assert slugs(output="image") == [image_slug]
    assert slugs(output="audio") == []
    assert slugs(enabled=True) == [image_slug]
    assert slugs(enabled=False) == [refreshed.slug_of(VIDEO)]
    assert slugs(search="video") == [refreshed.slug_of(VIDEO)]
    assert slugs(search="FAKE/IMAGE") == [image_slug]
    assert slugs(search="%") == []
    assert sorted(slugs(search="fake")) == sorted([image_slug, refreshed.slug_of(VIDEO)])


async def test_the_listing_pages(refreshed):
    first = refreshed.catalog.list_entries("cloud-1", limit=1, offset=0)
    second = refreshed.catalog.list_entries("cloud-1", limit=1, offset=1)

    assert first["total"] == second["total"] == 2
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["slug"] != second["items"][0]["slug"]


async def test_an_unknown_task_or_output_filter_is_refused(refreshed):
    with pytest.raises(CloudCatalogFilterError):
        refreshed.catalog.list_entries("cloud-1", task="juggling")
    with pytest.raises(CloudCatalogFilterError):
        refreshed.catalog.list_entries("cloud-1", output="smell")


async def test_the_listing_describes_the_provider_and_its_data_notice(refreshed):
    provider = refreshed.catalog.list_entries("cloud-1")["provider"]
    assert provider["key"] == "fake"
    assert provider["label"] == "Fake cloud"
    assert "nothing leaves this process" in provider["data_notice"]


async def test_an_unknown_or_non_cloud_backend_is_not_found(cloud_env):
    with pytest.raises(CloudBackendNotFound):
        cloud_env.catalog.list_entries("missing")
    with pytest.raises(CloudBackendNotFound):
        await cloud_env.catalog.refresh("missing")


async def test_a_disabled_backend_cannot_be_refreshed(cloud_env):
    from src.features.cloud.testing.fake import FakeCloudConfig

    await cloud_env.registry.add_backend(FakeCloudConfig(id="off", name="Off", enabled=False))

    with pytest.raises(CloudBackendInactive):
        await cloud_env.catalog.refresh("off")
    assert cloud_env.catalog.list_entries("off")["total"] == 0


async def test_enabling_an_unknown_slug_changes_nothing(refreshed):
    slug = refreshed.slug_of(IMAGE)

    with pytest.raises(CloudEntryNotFound) as raised:
        await refreshed.catalog.set_enabled("cloud-1", [slug, "fake~nope"], True)

    assert raised.value.slugs == ["fake~nope"]
    assert refreshed.catalog.list_entries("cloud-1")["counts"]["enabled"] == 0


async def test_a_model_the_provider_no_longer_offers_cannot_be_enabled(refreshed):
    refreshed.backend().provider.discover = returning(specs_without(VIDEO))
    await refreshed.catalog.refresh("cloud-1")

    with pytest.raises(CloudEntryUnavailable):
        await refreshed.catalog.set_enabled("cloud-1", [refreshed.slug_of(VIDEO)], True)


async def test_a_vanished_model_stays_enabled_but_is_not_offered_until_it_returns(refreshed):
    slug = refreshed.slug_of(VIDEO)
    provider = refreshed.backend().provider
    original = provider.discover
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    provider.discover = returning(specs_without(VIDEO))

    await refreshed.catalog.refresh("cloud-1")

    assert [m.filename for m in await refreshed.backend().list_models()] == []
    assert refreshed.catalog.list_entries("cloud-1")["counts"]["enabled"] == 1
    provider.discover = original
    await refreshed.catalog.refresh("cloud-1")
    assert [m.filename for m in await refreshed.backend().list_models()] == [slug]



async def test_an_empty_discovery_changes_nothing_and_says_so(refreshed):
    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    before = refreshed.catalog.list_entries("cloud-1")
    refreshed.backend().provider.discover = returning([])

    report = await refreshed.catalog.refresh("cloud-1")

    assert report["empty"] is True
    assert report["message"] == "The provider returned no models; nothing was changed."
    assert report["vanished"] == []
    after = refreshed.catalog.list_entries("cloud-1")
    assert after["counts"] == before["counts"] == {"total": 2, "enabled": 1, "missing": 0}
    assert after["state"] == before["state"]
    assert [m.filename for m in await refreshed.backend().list_models()] == [slug]


async def test_a_normal_refresh_is_not_flagged_empty(refreshed):
    report = await refreshed.catalog.refresh("cloud-1")

    assert report["empty"] is False and report["message"] is None
