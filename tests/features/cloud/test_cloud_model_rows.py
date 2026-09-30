import pytest

from src.features.generation.handlers.param_handler import ParamGenerationOutputHandler
from src.features.models.availability import models_for_engine
from src.features.models.availability_repository import model_availability_repo
from src.features.models.repository import model_repo
from tests.features.cloud.conftest import returning

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"


def add_user(db, user_id="u1"):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (user_id, user_id, f"{user_id}@example.test"),
        )


def count(db, sql, params=()):
    with db.get_cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


async def enable(env, *provider_ids):
    slugs = [env.slug_of(provider_id) for provider_id in provider_ids]
    await env.catalog.set_enabled("cloud-1", slugs, True)
    return slugs


async def disable(env, *provider_ids):
    slugs = [env.slug_of(provider_id) for provider_id in provider_ids]
    await env.catalog.set_enabled("cloud-1", slugs, False)
    return slugs


async def test_enabling_a_model_creates_its_model_row_and_its_availability_on_that_backend(refreshed):
    (slug,) = await enable(refreshed, IMAGE)

    model = refreshed.model_row(slug)
    assert model is not None and model.model_type == "cloud"
    claim = model_availability_repo.get(model.id, "cloud-1")
    assert claim is not None and claim.ref == slug
    assert [m.filename for m in await refreshed.backend().list_models()] == [slug]


async def test_a_cloud_model_row_has_no_file_no_hash_no_size_and_no_location(refreshed, mock_db):
    (slug,) = await enable(refreshed, IMAGE)

    model = refreshed.model_row(slug)
    assert model.sha256 is None and model.file_size is None
    assert count(mock_db, "SELECT COUNT(*) FROM model_locations WHERE model_id = ?", (model.id,)) == 0


async def test_enabling_writes_a_providers_row_carrying_the_label_and_the_wire_id(refreshed):
    (slug,) = await enable(refreshed, IMAGE)

    (info,) = model_repo.get_providers(refreshed.model_row(slug).id)
    assert info.provider == "cloud.fake"
    assert info.provider_model_id == IMAGE
    assert info.name == "Fake Image"
    assert info.tags == ["fake"]


async def test_the_model_is_named_by_its_label_even_when_its_slug_contains_a_dot(refreshed, dotted_slug):
    await refreshed.catalog.set_enabled("cloud-1", [dotted_slug], True)

    assert refreshed.model_row(dotted_slug).display_name == "Fake Image 1.5"


async def test_a_non_admin_listing_shows_the_label_not_the_slug(refreshed, dotted_slug):
    await refreshed.catalog.set_enabled("cloud-1", [dotted_slug], True)

    (listed,) = model_repo.get_all(model_type="cloud", include_providers=False)
    by_id = model_repo.get_by_id(listed.id, include_providers=False)
    bare = model_repo.get_by_identity("cloud", dotted_slug, include_providers=False)

    for model in (listed, by_id, bare):
        payload = model.to_dict(include_providers=False, admin=False)
        assert payload["name"] == "Fake Image 1.5"
        assert "providers" not in payload


async def test_a_depot_model_without_providers_still_skips_the_provider_lookup(mock_db):
    from src.features.models.records import Model

    lora = model_repo.create(Model(filename="a.safetensors", model_type="lora"))

    assert model_repo.get_by_id(lora.id, include_providers=False).providers == []


async def test_enabling_one_model_leaves_the_other_out_of_the_backend(refreshed):
    await enable(refreshed, IMAGE)

    assert refreshed.model_row(refreshed.slug_of(VIDEO)) is None


async def test_disabling_removes_the_availability_but_keeps_the_model_row(refreshed):
    (slug,) = await enable(refreshed, IMAGE)
    model_id = refreshed.model_row(slug).id

    await disable(refreshed, IMAGE)

    assert model_availability_repo.get(model_id, "cloud-1") is None
    assert refreshed.model_row(slug).id == model_id
    assert await refreshed.backend().list_models() == []


async def test_disabling_keeps_history_assignments_and_custom_names(refreshed, mock_db):
    add_user(mock_db)
    (slug,) = await enable(refreshed, IMAGE)
    model_id = refreshed.model_row(slug).id
    with mock_db.get_cursor() as cursor:
        cursor.execute("INSERT INTO generations (id, form_data) VALUES ('g1', '{}')")
        cursor.execute("INSERT INTO generation_models (id, generation_id, model_id) VALUES ('gm1', 'g1', ?)", (model_id,))
        cursor.execute("INSERT INTO user_models (id, user_id, model_id) VALUES ('um1', 'u1', ?)", (model_id,))
        cursor.execute(
            "INSERT INTO user_model_meta (user_id, model_id, custom_name, is_favorite) VALUES ('u1', ?, 'My pick', 1)",
            (model_id,),
        )

    await disable(refreshed, IMAGE)

    assert count(mock_db, "SELECT COUNT(*) FROM generation_models WHERE model_id = ?", (model_id,)) == 1
    assert count(mock_db, "SELECT COUNT(*) FROM user_models WHERE model_id = ?", (model_id,)) == 1
    assert count(mock_db, "SELECT COUNT(*) FROM user_model_meta WHERE model_id = ? AND custom_name = 'My pick'", (model_id,)) == 1


async def test_re_enabling_reuses_the_same_model_row_and_keeps_what_hangs_off_it(refreshed, mock_db):
    add_user(mock_db)
    (slug,) = await enable(refreshed, IMAGE)
    model_id = refreshed.model_row(slug).id
    with mock_db.get_cursor() as cursor:
        cursor.execute("INSERT INTO user_models (id, user_id, model_id) VALUES ('um1', 'u1', ?)", (model_id,))
    await disable(refreshed, IMAGE)

    await enable(refreshed, IMAGE)

    assert refreshed.model_row(slug).id == model_id
    assert model_availability_repo.get(model_id, "cloud-1") is not None
    assert count(mock_db, "SELECT COUNT(*) FROM models WHERE model_type = 'cloud'") == 1
    assert count(mock_db, "SELECT COUNT(*) FROM providers WHERE model_id = ?", (model_id,)) == 1
    assert count(mock_db, "SELECT COUNT(*) FROM user_models WHERE model_id = ?", (model_id,)) == 1


async def test_a_disabled_model_is_not_deleted_as_an_unclaimed_orphan(refreshed):
    (slug,) = await enable(refreshed, IMAGE)
    model_id = refreshed.model_row(slug).id
    await disable(refreshed, IMAGE)

    assert model_repo.delete_unclaimed_orphans([model_id]) == 0
    assert refreshed.model_row(slug) is not None


async def test_enable_and_disable_report_what_changed_and_what_was_already_so(refreshed):
    image, video = refreshed.slug_of(IMAGE), refreshed.slug_of(VIDEO)
    await refreshed.catalog.set_enabled("cloud-1", [image], True)

    report = await refreshed.catalog.set_enabled("cloud-1", [image, video], True)

    assert report["changed"] == [video]
    assert report["unchanged"] == [image]
    assert set(report["models"]) == {image, video}
    assert all(report["models"].values())
    again = await refreshed.catalog.set_enabled("cloud-1", [image, video], True)
    assert again["changed"] == []


async def test_enabling_and_disabling_many_at_once_works_in_one_call(refreshed):
    slugs = [refreshed.slug_of(IMAGE), refreshed.slug_of(VIDEO)]

    await refreshed.catalog.set_enabled("cloud-1", slugs, True)
    assert sorted(m.filename for m in await refreshed.backend().list_models()) == sorted(slugs)

    await refreshed.catalog.set_enabled("cloud-1", slugs, False)
    assert await refreshed.backend().list_models() == []


async def test_the_picker_lists_only_the_enabled_models_of_the_cloud_engine(refreshed):
    (slug,) = await enable(refreshed, IMAGE)

    entries = models_for_engine("cloud", refreshed.registry, model_type="cloud", admin=False)

    assert [entry["filename"] for entry in entries] == [slug]
    assert entries[0]["name"] == "Fake Image"


async def test_after_the_last_model_is_disabled_the_picker_is_empty_not_unfiltered(refreshed):
    await enable(refreshed, IMAGE)
    await disable(refreshed, IMAGE)

    assert models_for_engine("cloud", refreshed.registry, model_type="cloud", admin=False) == []


async def test_a_backend_that_never_enabled_anything_offers_an_empty_picker(refreshed):
    assert models_for_engine("cloud", refreshed.registry, model_type="cloud", admin=False) == []


async def test_two_backends_of_one_provider_share_the_model_row_and_keep_separate_availability(refreshed, mock_db):
    second = await refreshed.add_backend("cloud-2", "Second account")
    await refreshed.catalog.refresh("cloud-2")
    (slug,) = await enable(refreshed, IMAGE)
    await refreshed.catalog.set_enabled("cloud-2", [slug], True)
    model_id = refreshed.model_row(slug).id

    await refreshed.catalog.set_enabled("cloud-1", [slug], False)

    assert model_availability_repo.get(model_id, "cloud-1") is None
    assert model_availability_repo.get(model_id, "cloud-2") is not None
    assert count(mock_db, "SELECT COUNT(*) FROM models WHERE model_type = 'cloud'") == 1
    assert second.backend_id == "cloud-2"


async def test_removing_the_backend_drops_its_catalog_and_availability_but_not_the_model(refreshed):
    (slug,) = await enable(refreshed, IMAGE)
    model_id = refreshed.model_row(slug).id

    await refreshed.registry.remove_backend("cloud-1")

    assert refreshed.repository.counts("cloud-1")["total"] == 0
    assert model_availability_repo.get(model_id, "cloud-1") is None
    assert refreshed.model_row(slug) is not None


async def test_history_finds_a_cloud_model_from_its_slug_even_when_it_contains_a_dot(refreshed, dotted_slug):
    await refreshed.catalog.set_enabled("cloud-1", [dotted_slug], True)

    found = ParamGenerationOutputHandler._resolve_model(model_repo, dotted_slug)

    assert found is not None and found.filename == dotted_slug
    assert "/" not in dotted_slug


async def test_a_retry_repairs_an_enable_whose_first_sync_failed(refreshed):
    slug = refreshed.slug_of(IMAGE)
    indexer = refreshed.catalog.backend_indexer
    original = indexer.index_backend
    calls = []

    async def failing_once(backend):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("index interrupted")
        return await original(backend)

    indexer.index_backend = failing_once

    with pytest.raises(RuntimeError):
        await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    assert refreshed.model_row(slug) is None

    report = await refreshed.catalog.set_enabled("cloud-1", [slug], True)

    assert report["changed"] == []
    model = refreshed.model_row(slug)
    assert model is not None and model_availability_repo.get(model.id, "cloud-1") is not None
    assert model_repo.get_providers(model.id)


async def test_a_retry_repairs_a_disable_whose_first_sync_failed(refreshed):
    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    model_id = refreshed.model_row(slug).id
    indexer = refreshed.catalog.backend_indexer
    original = indexer.index_backend
    calls = []

    async def failing_once(backend):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("index interrupted")
        return await original(backend)

    indexer.index_backend = failing_once

    with pytest.raises(RuntimeError):
        await refreshed.catalog.set_enabled("cloud-1", [slug], False)
    assert model_availability_repo.get(model_id, "cloud-1") is not None

    await refreshed.catalog.set_enabled("cloud-1", [slug], False)

    assert model_availability_repo.get(model_id, "cloud-1") is None
