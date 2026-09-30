from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.cloud.policy import CloudPolicyViolation
from src.features.forms.binding import BoundForm
from src.features.generation.pipeline_builder import BuiltPipeline, PipelineBuilder
from src.features.generation.orchestrator import GenerationOrchestrator


def make_orchestrator(engine):
    backend = Mock(backend_id="cloud-1", engine=engine)
    backend.name = "Cloud"
    backend.start_generation = AsyncMock()
    registry = Mock()
    registry.select_backend_for_generation = Mock(return_value=backend)
    registry.get_backend = Mock(return_value=backend)
    loader = Mock()
    loader.load_preset_by_id = Mock(return_value=Mock(engine=engine, version="1.0.0"))
    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id="g", preset_id="p", preset_template=Mock(version="1.0.0"), pipes=[{"name": "x", "config": {}}],
    ))
    settings = Mock()
    settings.get_setting = Mock(return_value="/outputs")
    orchestrator = GenerationOrchestrator(
        pipeline_builder=builder, backend_registry=registry, connection_hub=Mock(broadcast_to_generation=AsyncMock()),
        settings=settings, output_processor=Mock(process_output=AsyncMock()), preset_template_loader=loader,
    )
    return orchestrator, backend


def make_request():
    request = Mock()
    request.preset_id = "p"
    request.form_data = {"steps": 20}
    request.prompts = None
    request.mode = "txt2img"
    request.tag_ids = []
    request.segments = None
    return request


@pytest.fixture
def bind_calls():
    calls = []

    def passthrough(preset_template, mode, form_name, raw_form_data, user_id, **kwargs):
        calls.append(kwargs)
        return BoundForm(values=dict(raw_form_data or {}), form_name="custom")

    with patch("src.features.generation.orchestrator.bind_form", side_effect=passthrough):
        yield calls


@pytest.fixture
def generation_repo():
    with patch("src.features.generation.orchestrator.generation_repo") as repo:
        repo.get_by_id = Mock(return_value=Mock(user_id="u1"))
        yield repo


@pytest.fixture(autouse=True)
def fake_db(mock_db):
    return mock_db


async def test_the_policy_runs_with_the_routed_backend_before_anything_is_recorded(bind_calls, generation_repo):
    orchestrator, backend = make_orchestrator("cloud")
    orchestrator.cloud_policy = Mock()
    orchestrator.cloud_policy.check = Mock(side_effect=CloudPolicyViolation("'X' cannot do txt2img."))

    with pytest.raises(CloudPolicyViolation, match="cannot do txt2img"):
        await orchestrator.start_generation(make_request(), "u1")

    (preset, mode, bound, routed), _ = orchestrator.cloud_policy.check.call_args
    assert mode == "txt2img" and routed is backend and bound.values == {"steps": 20}
    generation_repo.create.assert_not_called()
    backend.start_generation.assert_not_called()


async def test_a_passing_policy_lets_the_generation_start(bind_calls, generation_repo):
    orchestrator, backend = make_orchestrator("cloud")
    orchestrator.cloud_policy = Mock()

    with patch("src.features.generation.orchestrator.generate_ulid", return_value="g1"):
        result = await orchestrator.start_generation(make_request(), "u1")

    assert result["generation_id"] == "g1"
    orchestrator.cloud_policy.check.assert_called_once()


async def test_the_policy_is_not_asked_about_other_engines(bind_calls, generation_repo):
    orchestrator, backend = make_orchestrator("native")
    orchestrator.cloud_policy = Mock()

    with patch("src.features.generation.orchestrator.generate_ulid", return_value="g2"):
        await orchestrator.start_generation(make_request(), "u1")

    orchestrator.cloud_policy.check.assert_not_called()


async def test_a_cloud_form_is_bound_against_the_models_catalog_entry(bind_calls, generation_repo):
    orchestrator, backend = make_orchestrator("cloud")
    orchestrator.cloud_capabilities = Mock()

    with patch("src.features.generation.orchestrator.generate_ulid", return_value="g3"):
        await orchestrator.start_generation(make_request(), "u1")

    bind_calls[0]["cloud_capabilities"]("m1", "cloud.fake")
    orchestrator.cloud_capabilities.spec_for.assert_called_with("m1", "cloud.fake", None)
    assert len(bind_calls) == 2
    bind_calls[1]["cloud_capabilities"]("m1", "cloud.fake")
    orchestrator.cloud_capabilities.spec_for.assert_called_with("m1", "cloud.fake", "cloud-1")


async def test_another_engines_form_is_bound_without_the_cloud_resolver(bind_calls, generation_repo):
    orchestrator, backend = make_orchestrator("native")
    orchestrator.cloud_capabilities = Mock()

    with patch("src.features.generation.orchestrator.generate_ulid", return_value="g4"):
        await orchestrator.start_generation(make_request(), "u1")

    assert "cloud_capabilities" not in bind_calls[0]


async def test_an_orchestrator_without_cloud_collaborators_behaves_as_before(bind_calls, generation_repo):
    orchestrator, backend = make_orchestrator("cloud")

    with patch("src.features.generation.orchestrator.generate_ulid", return_value="g5"):
        result = await orchestrator.start_generation(make_request(), "u1")

    assert result["generation_id"] == "g5" and "cloud_capabilities" not in bind_calls[0]
