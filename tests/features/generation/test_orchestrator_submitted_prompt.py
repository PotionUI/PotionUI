"""A generation started without a prompt UI state (API, MCP, chat) keeps the
submitted prompt text so it can be reused and opened in a tab as submitted.
"""

from tests.fixtures.kits import inert_kit_expander
import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.forms.binding import BoundForm
from src.features.models.form_refs import make_model_ref, ModelRefNotAvailableError
from src.platform.plugins.registry import PluginRegistry
from src.features.generation.hooks import GENERATION_HOOKS


@pytest.fixture
def mock_pipeline_builder():
    from src.features.generation.pipeline_builder import PipelineBuilder, BuiltPipeline
    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id='gen_refs_test',
        preset_id='test_preset',
        preset_template=Mock(version='1.0.0'),
        pipes=[{'name': 'generator', 'config': {}}]
    ))
    return builder


@pytest.fixture
def mock_backend():
    backend = Mock()
    backend.backend_id = 'local_backend_1'
    backend.name = 'Local Backend'
    backend.engine = 'native'
    backend.start_generation = AsyncMock()
    backend.cancel_generation = AsyncMock(return_value=True)
    return backend


@pytest.fixture
def mock_backend_registry(mock_backend):
    from src.features.backends.backend_registry import BackendRegistry
    registry = Mock(spec=BackendRegistry)
    registry.select_backend_for_generation = Mock(return_value=mock_backend)
    registry.get_backend = Mock(return_value=mock_backend)
    return registry


@pytest.fixture
def mock_settings():
    from src.platform.settings.settings import Settings
    manager = Mock(spec=Settings)
    manager.get_setting = Mock(return_value='/outputs')
    return manager


@pytest.fixture
def mock_output_processor():
    from src.features.generation.output_processor import OutputProcessor
    processor = Mock(spec=OutputProcessor)
    processor.process_output = AsyncMock(return_value={'handler': 'TestHandler', 'processed': True})
    return processor


@pytest.fixture
def mock_preset_template_loader():
    loader = Mock()
    mock_preset = Mock()
    mock_preset.engine = 'native'
    loader.load_preset_by_id = Mock(return_value=mock_preset)
    return loader


@pytest.fixture
def mock_generation_repo():
    """Patch the module-level generation_repo singleton used by orchestrator."""
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo:
        mock_repo.create = Mock()
        mock_repo.update_status = Mock()
        mock_repo.get_by_id = Mock(return_value=Mock(user_id='user_123'))
        yield mock_repo


@pytest.fixture
def plugin_registry():
    """A real PluginRegistry so the before_start hook chain actually runs."""
    return PluginRegistry()


@pytest.fixture
def orchestrator(
    mock_pipeline_builder,
    mock_backend_registry,
    mock_connection_manager,
    mock_settings,
    mock_output_processor,
    mock_preset_template_loader,
    plugin_registry,
):
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_hub=mock_connection_manager,
        settings=mock_settings,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
        plugin_registry=plugin_registry, kit_expander=inert_kit_expander(),
    )


def _request(prompts):
    request = Mock()
    request.preset_id = 'test_preset_123'
    request.form_data = {'steps': 20}
    request.prompts = prompts
    request.prompt_state = None
    request.mode = 'txt2img'
    request.tag_ids = None
    request.collection_ids = None
    request.segments = None
    request.form_name = None
    return request


def _bind(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
    return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom')


async def _start(orchestrator, request):
    with patch('src.features.generation.orchestrator.bind_form', side_effect=_bind), \
         patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_prompt_1'):
        await orchestrator.start_generation(request, 'user_123')


class TestSubmittedPromptIsKept:
    @pytest.mark.asyncio
    async def test_single_prompt_without_ui_state_is_stored(self, orchestrator, mock_generation_repo):
        from src.features.generation.dto import PromptPair
        await _start(orchestrator, _request([PromptPair(positive='a lighthouse at dusk', negative='blurry')]))
        stored = mock_generation_repo.create.call_args[0][0]
        assert stored.prompt_state == {'prompt': 'a lighthouse at dusk', 'negativePrompt': 'blurry'}

    @pytest.mark.asyncio
    async def test_ui_state_from_a_tab_is_not_replaced(self, orchestrator, mock_generation_repo):
        from src.features.generation.dto import PromptPair
        request = _request([PromptPair(positive='x', negative='')])
        request.prompt_state = {'prompt': 'x', 'promptSegments': [{'content': 'x'}]}
        await _start(orchestrator, request)
        stored = mock_generation_repo.create.call_args[0][0]
        assert stored.prompt_state == {'prompt': 'x', 'promptSegments': [{'content': 'x'}]}

    @pytest.mark.asyncio
    async def test_empty_prompt_stores_nothing(self, orchestrator, mock_generation_repo):
        from src.features.generation.dto import PromptPair
        await _start(orchestrator, _request([PromptPair(positive='', negative='')]))
        assert mock_generation_repo.create.call_args[0][0].prompt_state is None
