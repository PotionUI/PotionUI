from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.features.cloud.policy import CloudGenerationPolicy, CloudPolicyViolation
from src.features.cloud.routes import build_router
from src.features.cloud.scope_repository import ModelPresetScopeRepository
from src.features.forms.binding import bind_form
from src.features.models.availability import models_for_engine
from src.features.models.form_refs import make_model_ref
from src.features.models.repository import model_repo
from src.features.presets.routes import PresetController
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PipeTemplate, PresetTemplate
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"


def add_preset(env, preset_id, name, engine="cloud", driver="cloud.fake"):
    env.presets.presets.append(SimpleNamespace(id=preset_id, name=name, engine=engine, driver=driver))


async def enable_both(env):
    slugs = [env.slug_of(IMAGE), env.slug_of(VIDEO)]
    await env.catalog.set_enabled("cloud-1", slugs, True)
    return env.model_row(slugs[0]).id, env.model_row(slugs[1]).id


def listed(env, preset_id):
    return sorted(
        entry["filename"]
        for entry in models_for_engine("cloud", env.registry, model_type="cloud", driver="cloud.fake", preset_id=preset_id)
    )


def client_as(container, account):
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id="u1", username="u", email="u@example.test", password_hash="h", account_type=account,
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def scope_url(model_id):
    return f"/api/cloud/models/{model_id}/scope"


async def test_an_unscoped_model_is_listed_in_every_preset(refreshed):
    await enable_both(refreshed)

    assert listed(refreshed, "preset-a") == listed(refreshed, "preset-b") == listed(refreshed, None)
    assert len(listed(refreshed, "preset-a")) == 2


async def test_a_scoped_model_is_listed_only_in_its_presets(refreshed):
    image_id, video_id = await enable_both(refreshed)
    ModelPresetScopeRepository().replace(image_id, ["preset-a"])

    assert listed(refreshed, "preset-a") == sorted([refreshed.slug_of(IMAGE), refreshed.slug_of(VIDEO)])
    assert listed(refreshed, "preset-b") == [refreshed.slug_of(VIDEO)]
    assert len(listed(refreshed, None)) == 2


async def test_a_model_scoped_to_several_presets_is_listed_in_each_of_them(refreshed):
    image_id, _ = await enable_both(refreshed)
    ModelPresetScopeRepository().replace(image_id, ["preset-a", "preset-b"])

    assert refreshed.slug_of(IMAGE) in listed(refreshed, "preset-a")
    assert refreshed.slug_of(IMAGE) in listed(refreshed, "preset-b")
    assert refreshed.slug_of(IMAGE) not in listed(refreshed, "preset-c")


async def test_clearing_a_scope_brings_the_model_back_everywhere(refreshed):
    image_id, _ = await enable_both(refreshed)
    repository = ModelPresetScopeRepository()
    repository.replace(image_id, ["preset-a"])
    repository.replace(image_id, [])

    assert refreshed.slug_of(IMAGE) in listed(refreshed, "preset-b")


async def test_a_scoped_out_model_leaves_the_preset_models_endpoint(refreshed):
    image_id, _ = await enable_both(refreshed)
    ModelPresetScopeRepository().replace(image_id, ["preset-a"])
    loader = Mock()
    controller = PresetController(SimpleNamespace(preset_loader=loader), refreshed.registry)

    loader.load_preset_by_id.return_value = SimpleNamespace(id="preset-b", engine="cloud", driver="cloud.fake")
    other = await controller.get_preset_models("preset-b", model_type="cloud")
    loader.load_preset_by_id.return_value = SimpleNamespace(id="preset-a", engine="cloud", driver="cloud.fake")
    own = await controller.get_preset_models("preset-a", model_type="cloud")

    assert [m["filename"] for m in other.data["models"]] == [refreshed.slug_of(VIDEO)]
    assert len(own.data["models"]) == 2


def template(preset_id):
    fields = [FieldTemplate(type="model", name="model")]
    return PresetTemplate(
        id=preset_id, name=preset_id, version="1.0.0", path="/p", engine="cloud", driver="cloud.fake",
        modes={"txt2img": ModeTemplate(
            forms=[FormTemplate(name="custom", fields=fields, default=True, order=0)],
            pipes=[PipeTemplate(name="cloud_generate", configuration={"task": "txt2img"})],
        )},
    )


def bound(template_, model_id):
    return bind_form(template_, "txt2img", None, {"model": make_model_ref(model_id)}, "u1")


async def test_the_server_refuses_a_model_posted_for_a_preset_outside_its_scope(refreshed):
    image_id, _ = await enable_both(refreshed)
    ModelPresetScopeRepository().replace(image_id, ["preset-a"])
    policy = CloudGenerationPolicy(refreshed.repository, model_repo, ModelPresetScopeRepository())
    outside = template("preset-b")

    with pytest.raises(CloudPolicyViolation) as raised:
        policy.check(outside, "txt2img", bound(outside, image_id), refreshed.backend())

    assert str(raised.value) == "'Fake Image' is not available in this preset. Choose another model or another preset."


async def test_the_server_accepts_a_scoped_model_in_its_own_preset(refreshed):
    image_id, _ = await enable_both(refreshed)
    ModelPresetScopeRepository().replace(image_id, ["preset-a"])
    policy = CloudGenerationPolicy(refreshed.repository, model_repo, ModelPresetScopeRepository())
    inside = template("preset-a")

    policy.check(inside, "txt2img", bound(inside, image_id), refreshed.backend())


async def test_the_server_accepts_an_unscoped_model_in_any_preset(refreshed):
    image_id, _ = await enable_both(refreshed)
    policy = CloudGenerationPolicy(refreshed.repository, model_repo, ModelPresetScopeRepository())
    anywhere = template("preset-z")

    policy.check(anywhere, "txt2img", bound(anywhere, image_id), refreshed.backend())


async def test_an_admin_reads_an_unscoped_model(refreshed, container):
    image_id, _ = await enable_both(refreshed)
    add_preset(refreshed, "preset-a", "Alpha")
    add_preset(refreshed, "preset-native", "Native", engine="native", driver=None)
    add_preset(refreshed, "preset-other", "Other", driver="cloud.other")

    async with client_as(container, AccountType.ADMIN) as client:
        data = (await client.get(scope_url(image_id))).json()["data"]

    assert data["scoped"] is False and data["preset_ids"] == [] and data["presets"] == []
    assert data["driver"] == "cloud.fake" and data["label"] == "Fake Image"
    assert data["candidates"] == [{"id": "preset-a", "title": "Alpha"}]


async def test_an_admin_replaces_the_scope_and_reads_it_back(refreshed, container):
    image_id, _ = await enable_both(refreshed)
    add_preset(refreshed, "preset-a", "Alpha")
    add_preset(refreshed, "preset-b", "Beta")

    async with client_as(container, AccountType.ADMIN) as client:
        put = await client.put(scope_url(image_id), json={"preset_ids": ["preset-b", "preset-a", "preset-a"]})
        get = await client.get(scope_url(image_id))
        cleared = await client.put(scope_url(image_id), json={"preset_ids": []})

    assert put.status_code == 200 and put.json()["data"] == get.json()["data"]
    data = put.json()["data"]
    assert data["scoped"] is True and data["preset_ids"] == ["preset-a", "preset-b"]
    assert data["presets"] == [
        {"id": "preset-a", "title": "Alpha", "engine": "cloud", "driver": "cloud.fake", "missing": False, "compatible": True},
        {"id": "preset-b", "title": "Beta", "engine": "cloud", "driver": "cloud.fake", "missing": False, "compatible": True},
    ]
    assert cleared.json()["data"]["scoped"] is False
    assert ModelPresetScopeRepository().get(image_id) == []


async def test_a_scope_naming_an_unknown_preset_is_refused_and_changes_nothing(refreshed, container):
    image_id, _ = await enable_both(refreshed)
    add_preset(refreshed, "preset-a", "Alpha")
    ModelPresetScopeRepository().replace(image_id, ["preset-a"])

    async with client_as(container, AccountType.ADMIN) as client:
        response = await client.put(scope_url(image_id), json={"preset_ids": ["preset-a", "nope"]})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "cloud_scope_invalid"
    assert "Preset 'nope' does not exist." in response.json()["detail"]["message"]
    assert ModelPresetScopeRepository().get(image_id) == ["preset-a"]


async def test_a_scope_naming_a_preset_of_another_provider_or_engine_is_refused(refreshed, container):
    image_id, _ = await enable_both(refreshed)
    add_preset(refreshed, "preset-other", "Other", driver="cloud.other")
    add_preset(refreshed, "preset-native", "Native", engine="native", driver=None)

    async with client_as(container, AccountType.ADMIN) as client:
        other = await client.put(scope_url(image_id), json={"preset_ids": ["preset-other"]})
        native = await client.put(scope_url(image_id), json={"preset_ids": ["preset-native"]})

    assert other.status_code == native.status_code == 422
    assert "cannot use this model" in other.json()["detail"]["message"]
    assert "cannot use this model" in native.json()["detail"]["message"]


async def test_a_scope_can_only_be_set_on_a_cloud_model(refreshed, container):
    from src.features.models.records import Model

    lora = model_repo.create(Model(filename="a.safetensors", model_type="lora"))

    async with client_as(container, AccountType.ADMIN) as client:
        get = await client.get(scope_url(lora.id))
        put = await client.put(scope_url(lora.id), json={"preset_ids": []})
        unknown = await client.get(scope_url("nope"))

    assert get.status_code == put.status_code == unknown.status_code == 404


@pytest.mark.parametrize("method", ["get", "put"])
async def test_a_regular_user_is_refused_the_scope_endpoints(refreshed, container, method):
    image_id, _ = await enable_both(refreshed)

    async with client_as(container, AccountType.USER) as client:
        response = await client.request(method, scope_url(image_id), json={"preset_ids": []})

    assert response.status_code == 403


async def test_a_scope_row_for_a_preset_that_no_longer_exists_is_shown_as_missing(refreshed, container):
    image_id, _ = await enable_both(refreshed)
    add_preset(refreshed, "preset-a", "Alpha")
    ModelPresetScopeRepository().replace(image_id, ["preset-a", "preset-gone"])

    async with client_as(container, AccountType.ADMIN) as client:
        data = (await client.get(scope_url(image_id))).json()["data"]
        replaced = await client.put(scope_url(image_id), json={"preset_ids": ["preset-a"]})

    gone = next(entry for entry in data["presets"] if entry["id"] == "preset-gone")
    assert gone == {"id": "preset-gone", "title": None, "engine": None, "driver": None, "missing": True, "compatible": False}
    assert replaced.json()["data"]["preset_ids"] == ["preset-a"]


async def test_a_scope_row_for_a_missing_preset_keeps_the_model_out_of_other_presets(refreshed):
    image_id, _ = await enable_both(refreshed)
    ModelPresetScopeRepository().replace(image_id, ["preset-gone"])

    assert refreshed.slug_of(IMAGE) not in listed(refreshed, "preset-a")


async def test_disabling_a_model_keeps_its_scope_for_when_it_is_enabled_again(refreshed):
    image_id, _ = await enable_both(refreshed)
    ModelPresetScopeRepository().replace(image_id, ["preset-a"])
    slug = refreshed.slug_of(IMAGE)

    await refreshed.catalog.set_enabled("cloud-1", [slug], False)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)

    assert ModelPresetScopeRepository().get(image_id) == ["preset-a"]


async def test_the_only_enabled_model_scoped_to_another_preset_leaves_an_empty_list(refreshed):
    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    ModelPresetScopeRepository().replace(refreshed.model_row(slug).id, ["preset-a"])

    assert listed(refreshed, "preset-b") == []
    assert listed(refreshed, "preset-a") == [slug]


async def test_a_native_listing_with_a_preset_id_is_unchanged(refreshed):
    from src.features.models.availability_records import ModelAvailability
    from src.features.models.availability_repository import model_availability_repo
    from src.features.models.records import Model

    native = next(b for b in refreshed.registry.get_backends_for_engine("native"))
    lora = model_repo.create(Model(filename="a.safetensors", model_type="lora"))
    model_availability_repo.upsert(ModelAvailability(
        id=None, model_id=lora.id, backend_id=native.backend_id, ref="loras/a.safetensors",
        size=None, confidence="reported", digest=None,
    ))
    image_id, _ = await enable_both(refreshed)
    ModelPresetScopeRepository().replace(image_id, ["preset-a"])

    plain = models_for_engine("native", refreshed.registry, model_type="lora")
    scoped = models_for_engine("native", refreshed.registry, model_type="lora", preset_id="preset-b")

    assert [m["filename"] for m in scoped] == [m["filename"] for m in plain] == ["a.safetensors"]


async def test_a_scope_with_an_unknown_and_a_cross_driver_preset_reports_both_problems(refreshed, container):
    image_id, _ = await enable_both(refreshed)
    add_preset(refreshed, "preset-other", "Other", driver="cloud.other")

    async with client_as(container, AccountType.ADMIN) as client:
        response = await client.put(scope_url(image_id), json={"preset_ids": ["nope", "preset-other"]})

    message = response.json()["detail"]["message"]
    assert response.status_code == 422
    assert "Preset 'nope' does not exist." in message
    assert "Preset 'Other' cannot use this model" in message
