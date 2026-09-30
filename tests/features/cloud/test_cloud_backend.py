import pytest

from src.features.backends.backend_config import config_class_engine
from src.features.backends.backend_registry import BackendRegistry
from src.features.cloud.backend import CloudBackend, CloudGenerationUnavailable
from src.features.cloud.contracts import CloudError, CloudHealth
from src.features.cloud.testing.fake import FakeClock, FakeCloudConfig, FakeCloudProvider
from src.features.generation.routing.contracts import Candidate, RoutingContext, RoutingRequest
from src.features.generation.routing.rules import ModelAvailability
from src.features.models.availability import NoBackendHoldsAllModelsError
from src.features.models.form_refs import make_model_ref
from tests.features.cloud.conftest import ScriptedPluginRegistry

IMAGE = "fake/image-1"


def build_registry(*provider_classes):
    return BackendRegistry(
        generation_engine_factory=lambda: None,
        plugin_registry=ScriptedPluginRegistry(*provider_classes),
    )


def test_a_registered_cloud_driver_is_a_creatable_driver_of_the_cloud_engine(mock_db):
    registry = build_registry(FakeCloudProvider)

    (descriptor,) = [d for d in registry.get_engine_descriptors() if d["driver"] == "cloud.fake"]

    assert descriptor["engine"] == "cloud"
    assert descriptor["creatable"] is True and descriptor["singleton"] is False
    assert descriptor["label"] == "Fake cloud"
    by_name = {field["name"]: field for field in descriptor["fields"]}
    assert by_name["api_key"]["secret"] is True
    assert by_name["max_parallel"]["default"] == 4
    assert "cloud" in registry.get_supported_engines()


def test_the_registry_builds_a_cloud_backend_class_for_the_driver(mock_db):
    registry = build_registry(FakeCloudProvider)

    backend_class = registry.get_registered_backend_types()["cloud.fake"]

    assert issubclass(backend_class, CloudBackend)
    assert backend_class.provider_class is FakeCloudProvider
    assert config_class_engine(registry.get_registered_config_types()["cloud.fake"]) == "cloud"


def test_without_a_cloud_provider_the_registry_has_no_cloud_driver(mock_db):
    registry = build_registry()

    assert "cloud.fake" not in registry.get_registered_backend_types()
    assert "cloud" not in registry.get_supported_engines()
    assert {"native.local", "native.remote"} <= set(registry.get_registered_backend_types())


def test_the_built_in_drivers_are_unchanged_by_a_cloud_registration(mock_db):
    plain = build_registry()
    with_cloud = build_registry(FakeCloudProvider)

    def without_cloud(registry):
        return {k: v for k, v in registry.get_registered_backend_types().items() if not k.startswith("cloud.")}

    assert without_cloud(with_cloud) == without_cloud(plain)


def test_two_providers_get_their_own_backend_classes(mock_db):
    class OtherProvider(FakeCloudProvider):
        key = "other"
        config_class = type("OtherConfig", (FakeCloudConfig,), {"__annotations__": {"driver": str}, "driver": "cloud.other"})

    registry = build_registry(FakeCloudProvider, OtherProvider)

    types = registry.get_registered_backend_types()
    assert types["cloud.fake"].provider_class is FakeCloudProvider
    assert types["cloud.other"].provider_class is OtherProvider
    assert types["cloud.fake"] is not types["cloud.other"]


async def test_adding_a_cloud_backend_yields_a_running_backend_instance(cloud_env):
    backend = await cloud_env.add_backend("cloud-1")

    assert isinstance(backend, CloudBackend)
    assert backend.engine == "cloud" and backend.driver == "cloud.fake"
    assert backend.provider.config is backend.config
    assert cloud_env.registry.get_backends_for_engine("cloud") == [backend]


async def test_a_cloud_backend_never_claims_a_local_gpu(fake_backend):
    backend = fake_backend.backend()

    assert backend.execution_device == "remote"
    assert backend.resolve_execution_device().kind == "remote"


async def test_a_cloud_backend_lists_models_and_is_authoritative_about_them(fake_backend):
    backend = fake_backend.backend()

    assert backend.supports_model_listing() is True
    assert backend.authoritative_listing is True
    assert await backend.list_models() == []


async def test_health_reports_available_when_the_provider_check_passes(fake_backend):
    health = await fake_backend.backend().health_check()

    assert health["status"] == "available"
    assert health["engine"] == "cloud"


async def test_health_reports_the_providers_message_when_the_check_fails(fake_backend):
    backend = fake_backend.backend()

    async def unhealthy():
        return CloudHealth(ok=False, message="Key revoked")

    backend.provider.check = unhealthy

    health = await backend.health_check()

    assert health["status"] == "error" and health["error"] == "Key revoked"


async def test_health_turns_a_provider_error_into_a_user_message(fake_backend):
    backend = fake_backend.backend()

    async def failing():
        raise CloudError("auth", "The provider rejected the API key.", detail="HTTP 401 secret-detail")

    backend.provider.check = failing

    health = await backend.health_check()

    assert health == {"status": "error", "engine": "cloud", "error": "The provider rejected the API key."}


async def test_health_survives_an_unexpected_provider_failure_without_leaking_it(fake_backend):
    backend = fake_backend.backend()

    async def exploding():
        raise RuntimeError("socket /home/secret/path")

    backend.provider.check = exploding

    health = await backend.health_check()

    assert health["status"] == "error"
    assert "secret" not in health["error"]


async def test_health_is_cached_for_a_minute_and_then_asked_again(fake_backend):
    backend = fake_backend.backend()
    clock = FakeClock()
    backend.clock = clock
    calls = []
    original = backend.provider.check

    async def counted():
        calls.append(1)
        return await original()

    backend.provider.check = counted

    await backend.health_check()
    clock.advance(30)
    await backend.health_check()
    assert len(calls) == 1
    clock.advance(31)
    await backend.health_check()
    assert len(calls) == 2


async def test_system_info_reports_catalog_counts_and_never_a_gpu(refreshed):
    await refreshed.catalog.set_enabled("cloud-1", [refreshed.slug_of(IMAGE)], True)

    info = await refreshed.backend().get_system_info()

    assert info["models_total"] == 2 and info["models_enabled"] == 1 and info["models_missing"] == 0
    assert info["provider"] == "Fake cloud"
    assert info["last_refreshed_at"] is not None
    assert not {"gpu", "vram", "gpu_info", "devices"} & set(info)


async def test_starting_a_generation_fails_clearly_until_the_runner_exists(fake_backend):
    with pytest.raises(CloudGenerationUnavailable, match="not available yet"):
        await fake_backend.backend().start_generation({"generation_id": "g1", "pipes": []}, lambda output: None)
    assert await fake_backend.backend().cancel_generation("g1") is False


async def test_routing_fails_loudly_for_a_model_that_is_no_longer_enabled(refreshed):
    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    model_id = refreshed.model_row(slug).id
    await refreshed.catalog.set_enabled("cloud-1", [slug], False)
    candidates = [Candidate(backend=refreshed.backend())]
    request = RoutingRequest(engine="cloud", preset=None, form_data={"model": make_model_ref(model_id)})

    with pytest.raises(NoBackendHoldsAllModelsError):
        await ModelAvailability().apply(candidates, request, RoutingContext(backend_registry=refreshed.registry))


async def test_routing_keeps_a_backend_that_holds_the_enabled_model(refreshed):
    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    model_id = refreshed.model_row(slug).id
    candidates = [Candidate(backend=refreshed.backend())]
    request = RoutingRequest(engine="cloud", preset=None, form_data={"model": make_model_ref(model_id)})

    result = await ModelAvailability().apply(candidates, request, RoutingContext(backend_registry=refreshed.registry))

    assert not result[0].dropped


async def test_a_reference_to_a_model_the_backend_no_longer_holds_fails_with_the_backend_named(refreshed):
    from src.features.models.form_refs import ModelRefNotAvailableError, resolve_form_model_refs

    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    model_id = refreshed.model_row(slug).id
    await refreshed.catalog.set_enabled("cloud-1", [slug], False)

    with pytest.raises(ModelRefNotAvailableError, match="cannot load"):
        resolve_form_model_refs({"model": make_model_ref(model_id)}, refreshed.backend(), locator=None)


async def test_a_reference_to_an_enabled_model_resolves_to_its_slug(refreshed):
    from src.features.models.form_refs import resolve_form_model_refs

    slug = refreshed.slug_of(IMAGE)
    await refreshed.catalog.set_enabled("cloud-1", [slug], True)
    model_id = refreshed.model_row(slug).id

    resolved = resolve_form_model_refs({"model": make_model_ref(model_id)}, refreshed.backend(), locator=None)

    assert resolved == {"model": slug}
