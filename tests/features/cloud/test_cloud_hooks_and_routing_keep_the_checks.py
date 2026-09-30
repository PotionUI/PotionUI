from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.cloud.contracts import ParamSpec
from src.features.cloud.scope_repository import ModelPresetScopeRepository
from src.features.cloud.policy import CloudGenerationPolicy, CloudPolicyViolation
from src.features.cloud.testing.fake import fake_specs
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.pipeline_builder import BuiltPipeline, PipelineBuilder
from src.features.generation.routing.contracts import Candidate, RoutingDecision
from src.features.models.form_refs import make_model_ref
from src.features.models.repository import model_repo
from src.features.forms.binding import FormBindingError
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PipeTemplate, PresetTemplate
from tests.features.cloud.conftest import returning

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"


def field(name, type_, capability=None, default=None):
    return FieldTemplate(type=type_, name=name, default=default, capability=capability)


def template(task="txt2img"):
    fields = [
        field("model", "model"),
        field("quality", "string", {"model_field": "model", "param": "quality"}),
    ]
    return PresetTemplate(
        id="p", name="Cloud", version="1.0.0", path="/presets/p", engine="cloud", driver="cloud.fake",
        modes={"txt2img": ModeTemplate(
            forms=[FormTemplate(name="custom", fields=fields, default=True, order=0)],
            pipes=[PipeTemplate(name="cloud_generate", configuration={"task": task})],
        )},
    )


def make_orchestrator(env, *, hook=None, router=None, task="txt2img"):
    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id="g", preset_id="p", preset_template=Mock(version="1.0.0"), pipes=[{"name": "x", "config": {}}],
    ))
    loader = Mock()
    loader.load_preset_by_id = Mock(return_value=template(task))
    settings = Mock()
    settings.get_setting = Mock(return_value="/outputs")
    plugins = None
    if hook is not None:
        plugins = Mock()

        def execute(name, context):
            context.data["form_data"] = hook(dict(context.data["form_data"]))
            return context, True

        plugins.execute_hook = Mock(side_effect=execute)
    orchestrator = GenerationOrchestrator(
        pipeline_builder=builder, backend_registry=env.registry,
        connection_hub=Mock(broadcast_to_generation=AsyncMock()), settings=settings,
        output_processor=Mock(process_output=AsyncMock()), preset_template_loader=loader,
        plugin_registry=plugins, router=router,
        cloud_capabilities=env.capabilities,
        cloud_policy=CloudGenerationPolicy(env.repository, model_repo, ModelPresetScopeRepository()),
    )
    return orchestrator


def make_request(model_id, **extra):
    request = Mock()
    request.preset_id = "p"
    request.form_data = {"model": make_model_ref(model_id), **extra}
    request.prompts = None
    request.mode = "txt2img"
    request.form_name = None
    request.tag_ids = []
    request.segments = None
    return request


async def enabled(env, provider_model_id, backend_id="cloud-1"):
    slug = env.slug_of(provider_model_id, backend_id)
    await env.catalog.set_enabled(backend_id, [slug], True)
    return env.model_row(slug).id


@pytest.fixture
def generation_repo():
    with patch("src.features.generation.orchestrator.generation_repo") as repo:
        repo.get_by_id = Mock(return_value=Mock(user_id="u1"))
        yield repo


@pytest.fixture(autouse=True)
def quiet_settings(mock_db):
    with patch("src.features.generation.orchestrator.generate_ulid", return_value="gen-1"):
        yield


@pytest.fixture
def started(refreshed):
    refreshed.backend().start_generation = AsyncMock()
    return refreshed.backend().start_generation


def recorded_form(generation_repo):
    (generation,), _ = generation_repo.create.call_args
    return generation.form_data


async def test_a_clean_cloud_submission_starts_and_records_the_bound_form(refreshed, generation_repo, started):
    image_id = await enabled(refreshed, IMAGE)
    orchestrator = make_orchestrator(refreshed)

    await orchestrator.start_generation(make_request(image_id, quality="4"), "u1")

    assert recorded_form(generation_repo)["quality"] == "4"
    started.assert_awaited_once()


async def test_a_hook_that_swaps_in_a_model_that_is_not_enabled_is_refused_before_anything_is_recorded(
    refreshed, generation_repo, started
):
    image_id = await enabled(refreshed, IMAGE)
    video_slug = refreshed.slug_of(VIDEO)
    await refreshed.catalog.set_enabled("cloud-1", [video_slug], True)
    video_id = refreshed.model_row(video_slug).id
    await refreshed.catalog.set_enabled("cloud-1", [video_slug], False)
    orchestrator = make_orchestrator(
        refreshed, hook=lambda form: {**form, "model": make_model_ref(video_id)},
    )

    with pytest.raises(CloudPolicyViolation, match="not enabled"):
        await orchestrator.start_generation(make_request(image_id), "u1")

    generation_repo.create.assert_not_called()
    started.assert_not_awaited()


async def test_a_hook_that_adds_a_param_the_model_lacks_has_it_stripped(refreshed, generation_repo, started):
    video_id = await enabled(refreshed, VIDEO)
    orchestrator = make_orchestrator(refreshed, hook=lambda form: {**form, "quality": "5"}, task="txt2video")

    await orchestrator.start_generation(make_request(video_id), "u1")

    assert recorded_form(generation_repo)["quality"] is None


async def test_a_hook_that_adds_an_out_of_range_value_is_a_binding_error(refreshed, generation_repo, started):
    image_id = await enabled(refreshed, IMAGE)
    orchestrator = make_orchestrator(refreshed, hook=lambda form: {**form, "quality": "99"})

    with pytest.raises(FormBindingError) as raised:
        await orchestrator.start_generation(make_request(image_id), "u1")

    assert "at most 10" in raised.value.field_errors["quality"][0]
    generation_repo.create.assert_not_called()


async def test_a_hook_cannot_turn_a_good_task_into_a_bad_one_for_the_policy(refreshed, generation_repo, started):
    image_id = await enabled(refreshed, IMAGE)
    orchestrator = make_orchestrator(refreshed, hook=lambda form: dict(form))

    await orchestrator.start_generation(make_request(image_id), "u1")

    started.assert_awaited_once()


async def two_accounts(refreshed):
    await refreshed.add_backend("cloud-2", "Second account")
    tight = replace(fake_specs()[0], params=(ParamSpec(name="quality", kind="range", minimum=1, maximum=3),))
    refreshed.backend("cloud-2").provider.discover = returning([tight])
    await refreshed.catalog.refresh("cloud-2")
    model_id = await enabled(refreshed, IMAGE)
    await refreshed.catalog.set_enabled("cloud-2", [refreshed.slug_of(IMAGE, "cloud-2")], True)
    return model_id


def router_choosing(env, backend_id):
    chosen = env.backend(backend_id)
    decision = RoutingDecision(
        chosen=chosen, candidates=[Candidate(backend=env.backend("cloud-1")), Candidate(backend=env.backend("cloud-2"))],
        rule_trace=[],
    )
    return Mock(route=AsyncMock(return_value=decision))


async def test_the_form_is_checked_against_the_spec_of_the_backend_it_was_routed_to(refreshed, generation_repo):
    model_id = await two_accounts(refreshed)
    for backend_id in ("cloud-1", "cloud-2"):
        refreshed.backend(backend_id).start_generation = AsyncMock()

    loose = make_orchestrator(refreshed, router=router_choosing(refreshed, "cloud-1"))
    await loose.start_generation(make_request(model_id, quality="7"), "u1")

    tight = make_orchestrator(refreshed, router=router_choosing(refreshed, "cloud-2"))
    with pytest.raises(FormBindingError) as raised:
        await tight.start_generation(make_request(model_id, quality="7"), "u1")
    assert "at most 3" in raised.value.field_errors["quality"][0]


async def test_a_param_only_the_routed_backends_spec_lacks_is_stripped(refreshed, generation_repo):
    model_id = await two_accounts(refreshed)
    bare = replace(fake_specs()[0], params=())
    refreshed.backend("cloud-2").provider.discover = returning([bare])
    await refreshed.catalog.refresh("cloud-2")
    refreshed.backend("cloud-2").start_generation = AsyncMock()
    orchestrator = make_orchestrator(refreshed, router=router_choosing(refreshed, "cloud-2"))

    await orchestrator.start_generation(make_request(model_id, quality="7"), "u1")

    assert recorded_form(generation_repo)["quality"] is None


async def test_the_capabilities_resolver_can_be_pinned_to_one_backend(refreshed):
    model_id = await two_accounts(refreshed)

    first = refreshed.capabilities.spec_for(model_id, "cloud.fake", "cloud-1")
    second = refreshed.capabilities.spec_for(model_id, "cloud.fake", "cloud-2")
    anywhere = refreshed.capabilities.spec_for(model_id, "cloud.fake")

    assert {p.name: p.maximum for p in first.params}["quality"] == 10
    assert {p.name: p.maximum for p in second.params}["quality"] == 3
    assert anywhere == first
    assert refreshed.capabilities.spec_for(model_id, "cloud.fake", "cloud-9") is None


async def test_a_model_limited_to_other_presets_is_refused_when_posted_for_this_one(refreshed, generation_repo, started):
    image_id = await enabled(refreshed, IMAGE)
    ModelPresetScopeRepository().replace(image_id, ["some-other-preset"])
    orchestrator = make_orchestrator(refreshed)

    with pytest.raises(CloudPolicyViolation, match="not available in this preset"):
        await orchestrator.start_generation(make_request(image_id), "u1")

    generation_repo.create.assert_not_called()
    started.assert_not_awaited()


async def test_a_model_limited_to_this_preset_runs(refreshed, generation_repo, started):
    image_id = await enabled(refreshed, IMAGE)
    ModelPresetScopeRepository().replace(image_id, ["p"])
    orchestrator = make_orchestrator(refreshed)

    await orchestrator.start_generation(make_request(image_id), "u1")

    started.assert_awaited_once()
