from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.forms.binding import BoundForm, FormBindingError
from src.features.generation.dto import GenerationRequest

_RESOURCES = {
    "refs": [
        {"field": "references", "kind": "image", "token": "<Picture @>"},
        {"field": "reference_videos", "kind": "video", "token": "<Video @>"},
    ]
}


@pytest.fixture
def backend():
    backend = Mock()
    backend.backend_id = "local_backend_1"
    backend.name = "Local Backend"
    backend.engine = "native"
    backend.start_generation = AsyncMock()
    return backend


@pytest.fixture
def orchestrator(backend):
    from src.features.backends.backend_registry import BackendRegistry
    from src.features.generation.orchestrator import GenerationOrchestrator
    from src.features.generation.pipeline_builder import BuiltPipeline, PipelineBuilder
    from src.features.generation.output_processor import OutputProcessor
    from src.platform.settings.settings import Settings
    from src.platform.websocket.connection_hub import ConnectionHub

    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id="gen_resources", preset_id="p", preset_template=Mock(version="1.0.0"),
        pipes=[{"name": "generator", "config": {}}],
    ))
    registry = Mock(spec=BackendRegistry)
    registry.select_backend_for_generation = Mock(return_value=backend)
    registry.get_backend = Mock(return_value=backend)
    hub = Mock(spec=ConnectionHub)
    hub.broadcast_to_generation = AsyncMock()
    settings = Mock(spec=Settings)
    settings.get_setting = Mock(return_value="/outputs")
    loader = Mock()
    loader.load_preset_by_id = Mock(return_value=SimpleNamespace(engine="native", prompt_resources=_RESOURCES, vars={}, tags=[]))
    return GenerationOrchestrator(
        pipeline_builder=builder,
        backend_registry=registry,
        connection_hub=hub,
        settings=settings,
        output_processor=Mock(spec=OutputProcessor),
        preset_template_loader=loader,
    )


@pytest.fixture
def generation_repo():
    with patch("src.features.generation.orchestrator.generation_repo") as repo:
        repo.create = Mock()
        repo.get_by_id = Mock(return_value=Mock(user_id="user_1"))
        yield repo


def _request(positive, form_data):
    return GenerationRequest(
        preset_id="h3",
        mode="refs",
        prompts=[{"positive": positive, "negative": ""}],
        form_data=form_data,
    )


def _passthrough_bind(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
    return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom")


async def _start(orchestrator, request):
    with patch("src.features.generation.orchestrator.bind_form", Mock(side_effect=_passthrough_bind)), \
         patch("src.features.generation.orchestrator.generate_ulid", return_value="gen_resources"):
        return await orchestrator.start_generation(request, "user_1")


class TestStartGenerationResolvesPromptResources:
    @pytest.mark.asyncio
    async def test_markers_become_tokens_before_the_generation_is_queued(self, orchestrator, generation_repo):
        request = _request(
            "@[references:uploads/b.png] next to @[references:uploads/a.png], moving like @[reference_videos:uploads/v.mp4]",
            {
                "references": [{"path": "uploads/a.png", "relative_path": "uploads/a.png"}, "uploads/b.png"],
                "reference_videos": ["uploads/v.mp4"],
            },
        )

        await _start(orchestrator, request)

        assert request.prompts[0].positive == "<Picture 2> next to <Picture 1>, moving like <Video 1>"
        generation_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_marker_for_a_removed_item_fails_before_anything_is_persisted(self, orchestrator, generation_repo):
        request = _request("@[references:uploads/gone.png] waves", {"references": ["uploads/a.png"]})

        with pytest.raises(FormBindingError) as exc:
            await _start(orchestrator, request)

        assert list(exc.value.field_errors) == ["references"]
        assert "removed" in exc.value.field_errors["references"][0]
        generation_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_marker_for_an_unmapped_field_fails_before_anything_is_persisted(self, orchestrator, generation_repo):
        request = _request("@[reference_audios:uploads/voice.wav]", {"reference_audios": ["uploads/voice.wav"]})

        with pytest.raises(FormBindingError) as exc:
            await _start(orchestrator, request)

        assert list(exc.value.field_errors) == ["reference_audios"]
        generation_repo.create.assert_not_called()
