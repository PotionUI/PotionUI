import tempfile
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.features.backends.backend_registry import BackendRegistry
from src.features.cloud.capabilities import CloudCapabilities
from src.features.cloud.catalog import CloudCatalog
from src.features.cloud.dto import DirectorEstimateRequest
from src.features.cloud.repository import CloudCatalogRepository
from src.features.cloud.routes import CloudCapabilitiesController
from src.features.cloud.testing.fake import FakeCloudConfig, FakeCloudProvider, fake_director_specs, fake_specs
from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.backend_indexer import BackendModelIndexer
from src.features.models.exceptions import ModelNotFoundException
from src.features.models.repository import model_repo
from src.platform.plugins import runtime_registries
from src.platform.security.user import AccountType, User
from src.plugin_api import cloud as api
from tests.features.cloud.conftest import ScriptedPluginRegistry, returning

SECRET = "sk-plugin-api-test-credential"
FILM = [
    {"task": "txt2video", "params": {"duration_s": 2}},
    {"task": "img2video", "params": {"duration_s": 4}},
]


def _user(account_type):
    return User(id="u1", username="u1", email="u1@example.test", password_hash="h", account_type=account_type)


ADMIN = _user(AccountType.ADMIN)


@pytest.fixture
async def cloud(mock_db, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    registry = BackendRegistry(
        generation_engine_factory=lambda: None,
        plugin_registry=ScriptedPluginRegistry(FakeCloudProvider),
    )
    repository = CloudCatalogRepository()
    catalog = CloudCatalog(
        backend_registry=registry,
        repository=repository,
        model_repository=model_repo,
        backend_indexer=BackendModelIndexer(),
    )
    capabilities = CloudCapabilities(
        backend_registry=registry,
        repository=repository,
        model_repository=model_repo,
        model_access_policy=ModelAccessPolicy(model_repo),
    )
    await registry.add_backend(FakeCloudConfig(id="cloud-1", name="Fake cloud", api_key=SECRET))
    registry.get_backend("cloud-1").provider.discover = returning([*fake_specs(), *fake_director_specs()])
    await catalog.refresh("cloud-1")
    monkeypatch.setattr(
        runtime_registries, "_container", SimpleNamespace(cloud_catalog=catalog, cloud_capabilities=capabilities)
    )
    return SimpleNamespace(registry=registry, repository=repository, catalog=catalog, capabilities=capabilities)


def _slug(cloud, provider_model_id):
    return next(slug for slug, known in cloud.repository.provider_ids("cloud-1").items() if known == provider_model_id)


async def _turn(cloud, provider_model_id, enabled=True):
    slug = _slug(cloud, provider_model_id)
    return (await cloud.catalog.set_enabled("cloud-1", [slug], enabled))["models"][slug]


async def test_an_admin_gets_exactly_what_the_estimate_route_returns(cloud):
    model_id = await _turn(cloud, "fake/director-1")
    route = await CloudCapabilitiesController(cloud.capabilities).estimate(
        model_id, DirectorEstimateRequest.model_validate({"shots": FILM})
    )

    estimate = api.estimate_cloud_cost(ADMIN, model_id, FILM)

    assert estimate == route.data
    assert estimate["known"] is True and Decimal(estimate["total_usd"]) == Decimal("0.60")


async def test_a_shot_without_a_task_is_completed_like_the_routes_body(cloud):
    model_id = await _turn(cloud, "fake/director-1")

    completed = api.estimate_cloud_cost(ADMIN, model_id, [{"params": {"duration_s": 2}}])

    assert completed == api.estimate_cloud_cost(ADMIN, model_id, [{"task": "txt2video", "params": {"duration_s": 2}}])


async def test_the_estimate_follows_the_driver_it_is_given(cloud):
    model_id = await _turn(cloud, "fake/director-1")

    assert api.estimate_cloud_cost(ADMIN, model_id, FILM, driver="cloud.fake")["model_id"] == model_id
    with pytest.raises(ModelNotFoundException):
        api.estimate_cloud_cost(ADMIN, model_id, FILM, driver="cloud.other")


@pytest.mark.parametrize("user", [None, _user(AccountType.USER)])
async def test_only_an_admin_sees_a_cost(cloud, user):
    model_id = await _turn(cloud, "fake/director-1")

    with pytest.raises(PermissionError, match="^Only an administrator can see what a cloud run costs$"):
        api.estimate_cloud_cost(user, model_id, FILM)


async def test_a_shot_the_estimator_refuses_raises_its_error(cloud):
    model_id = await _turn(cloud, "fake/director-1")

    with pytest.raises(api.DirectorEstimateError, match="unknown task 'dance'") as raised:
        api.estimate_cloud_cost(ADMIN, model_id, [{"task": "dance"}])

    assert isinstance(raised.value, ValueError)


async def test_an_empty_film_is_refused_like_the_routes_body(cloud):
    model_id = await _turn(cloud, "fake/director-1")

    with pytest.raises(ValidationError) as raised:
        api.estimate_cloud_cost(ADMIN, model_id, [])

    assert isinstance(raised.value, ValueError)


async def test_a_model_that_is_not_an_enabled_cloud_model_is_not_priced(cloud):
    turned_off = await _turn(cloud, "fake/director-1")
    await _turn(cloud, "fake/director-1", enabled=False)

    for model_id in ("nope", turned_off):
        with pytest.raises(ModelNotFoundException):
            api.estimate_cloud_cost(ADMIN, model_id, FILM)


async def test_the_enabled_models_of_a_backend_are_the_ones_turned_on_in_its_catalog(cloud):
    assert api.enabled_cloud_models("cloud-1") == []

    model_ids = [await _turn(cloud, spec.provider_model_id) for spec in fake_director_specs()]
    entries = api.enabled_cloud_models("cloud-1")

    assert sorted(entry.model_id for entry in entries) == sorted(model_ids)
    assert {entry.provider_model_id for entry in entries} == {spec.provider_model_id for spec in fake_director_specs()}
    assert [entry.slug for entry in entries] == sorted(entry.slug for entry in entries)
    assert all(entry.enabled and entry.backend_id == "cloud-1" for entry in entries)
    assert api.enabled_cloud_models("cloud-2") == []


async def test_an_enabled_cloud_model_resolves_to_its_backend_driver_and_entry(cloud):
    model_id = await _turn(cloud, "fake/director-1")

    resolved = api.resolve_cloud_model(model_id)

    assert (resolved.model_id, resolved.backend_id, resolved.driver) == (model_id, "cloud-1", "cloud.fake")
    assert resolved.entry.provider_model_id == "fake/director-1"
    assert resolved.spec.provider_model_id == "fake/director-1"
    assert api.resolve_cloud_model("nope") is None


async def test_a_model_turned_off_leaves_the_enabled_list_and_no_longer_resolves(cloud):
    model_id = await _turn(cloud, "fake/director-1")
    await _turn(cloud, "fake/director-1", enabled=False)

    assert api.enabled_cloud_models("cloud-1") == []
    assert api.resolve_cloud_model(model_id) is None


async def test_no_cloud_read_carries_the_backends_credential(cloud):
    model_id = await _turn(cloud, "fake/director-1")

    reads = [
        api.estimate_cloud_cost(ADMIN, model_id, FILM),
        api.enabled_cloud_models("cloud-1"),
        api.resolve_cloud_model(model_id),
    ]

    assert cloud.registry.get_backend("cloud-1").config.api_key == SECRET
    assert SECRET not in repr(reads)
