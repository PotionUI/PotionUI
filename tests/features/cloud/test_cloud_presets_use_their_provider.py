from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import Field

from src.features.backends.backend_registry import BackendRegistry
from src.features.cloud.catalog import CloudCatalog
from src.features.cloud.repository import CloudCatalogRepository
from src.features.cloud.testing.fake import FakeCloudConfig, FakeCloudProvider
from src.features.generation.routing.contracts import NoEligibleBackendError, RoutingRequest
from src.features.generation.routing.registry import BUILTIN_RULES
from src.features.generation.routing.router import GenerationRouter
from src.features.models.availability import models_for_engine
from src.features.models.backend_indexer import BackendModelIndexer
from src.features.models.form_refs import make_model_ref
from src.features.models.repository import model_repo
from src.features.presets.routes import PresetController
from tests.features.cloud.conftest import ScriptedPluginRegistry

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"


class SecondConfig(FakeCloudConfig):
    driver: str = Field(default="cloud.fake2")


class SecondProvider(FakeCloudProvider):
    key = "fake2"
    label = "Second fake cloud"
    config_class = SecondConfig


class TwoDrivers:
    def __init__(self, registry, catalog):
        self.registry = registry
        self.catalog = catalog

    def backend(self, backend_id):
        return self.registry.get_backend(backend_id)

    def slug(self, backend_id, provider_model_id):
        return next(
            slug for slug, known in self.catalog.repository.provider_ids(backend_id).items()
            if known == provider_model_id
        )

    def model_id(self, backend_id, provider_model_id):
        return model_repo.get_by_identity("cloud", self.slug(backend_id, provider_model_id)).id

    def router(self):
        return GenerationRouter(list(BUILTIN_RULES), backend_registry=self.registry)


@pytest.fixture
async def two(mock_db):
    registry = BackendRegistry(
        generation_engine_factory=lambda: None,
        plugin_registry=ScriptedPluginRegistry(FakeCloudProvider, SecondProvider),
    )
    catalog = CloudCatalog(
        backend_registry=registry,
        repository=CloudCatalogRepository(),
        model_repository=model_repo,
        backend_indexer=BackendModelIndexer(),
    )
    await registry.add_backend(FakeCloudConfig(id="cloud-a", name="Provider A"))
    await registry.add_backend(SecondConfig(id="cloud-b", name="Provider B"))
    for backend_id in ("cloud-a", "cloud-b"):
        await catalog.refresh(backend_id)
        slugs = [s for s in catalog.repository.provider_ids(backend_id)]
        await catalog.set_enabled(backend_id, slugs, True)
    return TwoDrivers(registry, catalog)


def preset(driver):
    return SimpleNamespace(id="p1", engine="cloud", driver=driver, requirements=[])


def filenames(entries):
    return sorted(entry["filename"] for entry in entries)


async def test_a_listing_for_one_driver_never_shows_the_other_drivers_models(two):
    only_a = models_for_engine("cloud", two.registry, model_type="cloud", driver="cloud.fake")
    only_b = models_for_engine("cloud", two.registry, model_type="cloud", driver="cloud.fake2")

    assert filenames(only_a) == sorted([two.slug("cloud-a", IMAGE), two.slug("cloud-a", VIDEO)])
    assert filenames(only_b) == sorted([two.slug("cloud-b", IMAGE), two.slug("cloud-b", VIDEO)])
    assert all(name.startswith("fake~") for name in filenames(only_a))
    assert all(name.startswith("fake2~") for name in filenames(only_b))


async def test_a_listing_without_a_driver_still_shows_every_enabled_model_of_the_engine(two):
    everything = models_for_engine("cloud", two.registry, model_type="cloud")

    assert len(everything) == 4


async def test_a_driver_with_no_backend_lists_nothing(two):
    assert models_for_engine("cloud", two.registry, model_type="cloud", driver="cloud.nobody") == []


async def test_the_preset_models_endpoint_narrows_to_the_presets_driver(two):
    loader = Mock()
    loader.load_preset_by_id.return_value = preset("cloud.fake2")
    controller = PresetController(SimpleNamespace(preset_loader=loader), two.registry)

    response = await controller.get_preset_models("p1", model_type="cloud", admin=True)

    models = response.data["models"]
    assert filenames(models) == sorted([two.slug("cloud-b", IMAGE), two.slug("cloud-b", VIDEO)])
    assert all(entry["backend_ids"] == ["cloud-b"] for entry in models)


async def test_the_preset_models_endpoint_passes_the_task_filter_through(two):
    loader = Mock()
    loader.load_preset_by_id.return_value = preset("cloud.fake")
    controller = PresetController(SimpleNamespace(preset_loader=loader), two.registry)

    response = await controller.get_preset_models("p1", model_type="cloud", tasks="txt2video, img2video")

    assert filenames(response.data["models"]) == [two.slug("cloud-a", VIDEO)]


async def test_a_task_filter_keeps_models_whose_catalog_tasks_intersect_it(two):
    def listed(tasks, driver="cloud.fake"):
        return filenames(models_for_engine("cloud", two.registry, model_type="cloud", driver=driver, tasks=tasks))

    assert listed(["txt2img"]) == [two.slug("cloud-a", IMAGE)]
    assert listed(["img_edit"]) == [two.slug("cloud-a", IMAGE)]
    assert listed(["txt2video"]) == [two.slug("cloud-a", VIDEO)]
    assert listed(["txt2img", "txt2video"]) == sorted([two.slug("cloud-a", IMAGE), two.slug("cloud-a", VIDEO)])
    assert listed(["upscale_image"]) == []
    assert listed(["txt2video"], driver="cloud.fake2") == [two.slug("cloud-b", VIDEO)]


async def test_a_task_filter_does_not_list_a_model_that_is_disabled(two):
    await two.catalog.set_enabled("cloud-a", [two.slug("cloud-a", IMAGE)], False)

    assert models_for_engine("cloud", two.registry, model_type="cloud", driver="cloud.fake", tasks=["txt2img"]) == []


async def test_a_task_filter_is_ignored_for_other_model_types(two):
    from src.features.models.availability_repository import model_availability_repo
    from src.features.models.availability_records import ModelAvailability

    native = next(b for b in two.registry.get_backends_for_engine("native"))
    lora = model_repo.create(depot_model("a.safetensors", "lora"))
    model_availability_repo.upsert(ModelAvailability(
        id=None, model_id=lora.id, backend_id=native.backend_id, ref="loras/a.safetensors",
        size=None, confidence="reported", digest=None,
    ))

    plain = models_for_engine("native", two.registry, model_type="lora")
    filtered = models_for_engine("native", two.registry, model_type="lora", tasks=["txt2img"])

    assert filenames(plain) == ["a.safetensors"]
    assert filenames(filtered) == ["a.safetensors"]


async def test_the_task_query_only_returns_enabled_models_of_the_named_backends(two):
    repository = two.catalog.repository
    image = two.model_id("cloud-a", IMAGE)
    await two.catalog.set_enabled("cloud-a", [two.slug("cloud-a", IMAGE)], False)

    assert image not in repository.model_ids_for_tasks(["cloud-a"], ["txt2img"])
    assert repository.model_ids_for_tasks(["cloud-b"], ["txt2img"]) == [two.model_id("cloud-b", IMAGE)]
    assert repository.model_ids_for_tasks([], ["txt2img"]) == []
    assert repository.model_ids_for_tasks(["cloud-a"], []) == []


async def test_the_indexed_flag_for_admins_follows_the_presets_driver(two):
    await two.catalog.set_enabled("cloud-a", list(two.catalog.repository.provider_ids("cloud-a")), False)
    loader = Mock()
    controller = PresetController(SimpleNamespace(preset_loader=loader), two.registry)

    loader.load_preset_by_id.return_value = preset("cloud.fake")
    for_a = await controller.get_preset_models("p1", model_type="cloud", admin=True)
    loader.load_preset_by_id.return_value = preset("cloud.fake2")
    for_b = await controller.get_preset_models("p1", model_type="cloud", admin=True)

    assert for_a.data["indexed"] is False
    assert for_b.data["indexed"] is True


def depot_model(filename, model_type):
    from src.features.models.records import Model

    return Model(filename=filename, model_type=model_type)


async def test_routing_keeps_only_backends_of_the_presets_driver_and_the_trace_names_the_rest(two):
    decision = await two.router().route(RoutingRequest(engine="cloud", preset=preset("cloud.fake"), form_data={}))

    assert decision.chosen.backend_id == "cloud-a"
    trace = decision.to_trace_dict()
    (step,) = [t for t in trace["rule_trace"] if t["rule"] == "preset_driver"]
    assert (step["before"], step["after"]) == (2, 1)
    (dropped,) = [c for c in trace["candidates"] if c["dropped"]]
    assert dropped["backend_id"] == "cloud-b" and dropped["backend_name"] == "Provider B"
    assert dropped["reasons"] == ["preset requires driver cloud.fake; this backend uses cloud.fake2"]


async def test_a_preset_for_one_driver_routes_its_own_models_to_its_own_backend(two):
    form = {"model": make_model_ref(two.model_id("cloud-b", IMAGE))}

    decision = await two.router().route(RoutingRequest(engine="cloud", preset=preset("cloud.fake2"), form_data=form))

    assert decision.chosen.backend_id == "cloud-b"


async def test_the_other_drivers_model_posted_directly_for_a_preset_is_refused(two):
    form = {"model": make_model_ref(two.model_id("cloud-b", IMAGE))}

    with pytest.raises(NoEligibleBackendError) as raised:
        await two.router().route(RoutingRequest(engine="cloud", preset=preset("cloud.fake"), form_data=form))

    assert "preset requires driver cloud.fake" in str(raised.value)
    assert "does not hold every selected model" in str(raised.value)


async def test_each_driver_refuses_the_model_of_the_other(two):
    form_a = {"model": make_model_ref(two.model_id("cloud-a", VIDEO))}

    with pytest.raises(NoEligibleBackendError):
        await two.router().route(RoutingRequest(engine="cloud", preset=preset("cloud.fake2"), form_data=form_a))


async def test_without_a_driver_the_same_model_routes_to_the_backend_that_holds_it(two):
    form = {"model": make_model_ref(two.model_id("cloud-b", IMAGE))}

    decision = await two.router().route(RoutingRequest(engine="cloud", preset=preset(None), form_data=form))

    assert decision.chosen.backend_id == "cloud-b"
    assert not any(c.dropped and "driver" in " ".join(c.reasons) for c in decision.candidates)


async def test_a_preset_whose_driver_has_no_backend_fails_naming_the_driver(two):
    with pytest.raises(NoEligibleBackendError) as raised:
        await two.router().route(RoutingRequest(engine="cloud", preset=preset("cloud.nobody"), form_data={}))

    assert "cloud.nobody" in str(raised.value)
    assert "Provider A" in str(raised.value) and "Provider B" in str(raised.value)
