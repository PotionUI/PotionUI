"""The persisted `Generation.form_data` must keep `model:<id>` refs (the form
contract `bind_form`/reuse/bundle export expect) while `request.form_data` -
what `_start_generation`'s pipeline build actually reads - carries the
engine-native values `resolve_form_model_refs` produces for the selected
backend. See `GenerationOrchestrator.start_generation`.
"""

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
def mock_connection_manager():
    from src.platform.websocket.connection_hub import ConnectionHub
    manager = Mock(spec=ConnectionHub)
    manager.broadcast_to_generation = AsyncMock()
    return manager


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
        plugin_registry=plugin_registry,
    )


def _make_request(form_data=None):
    request = Mock()
    request.preset_id = 'test_preset_123'
    request.form_data = form_data if form_data is not None else {'steps': 20}
    request.prompts = None
    request.prompt_state = None
    request.mode = 'txt2img'
    request.tag_ids = None
    request.collection_ids = None
    request.segments = None
    request.form_name = None
    return request


def _passthrough_bind(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
    return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom')


def _fake_resolve(refs_by_value):
    """A `resolve_form_model_refs` stand-in that rewrites known `model:<id>`
    strings anywhere in the form (dict/list nesting included, same shape the
    real LoRA picker rows use) and leaves everything else untouched."""

    def resolve(form_data, backend_id):
        def rewrite(node):
            if isinstance(node, str) and node in refs_by_value:
                return refs_by_value[node]
            if isinstance(node, dict):
                return {key: rewrite(value) for key, value in node.items()}
            if isinstance(node, list):
                return [rewrite(item) for item in node]
            return node

        return rewrite(form_data)

    return resolve


class TestPersistedFormDataKeepsModelRefs:
    @pytest.mark.asyncio
    async def test_record_keeps_refs_request_form_data_gets_resolved_paths(
        self, orchestrator, mock_generation_repo
    ):
        raw_form_data = {
            'checkpoint': make_model_ref('main'),
            'loras': [{'model': make_model_ref('abc'), 'strength': 1.0}],
        }
        request = _make_request(form_data=raw_form_data)
        resolve = _fake_resolve({
            make_model_ref('main'): '/abs/path/main.safetensors',
            make_model_ref('abc'): '/abs/path/abc.safetensors',
        })

        with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough_bind), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_refs_1'), \
             patch('src.features.generation.orchestrator.resolve_form_model_refs', side_effect=resolve):
            await orchestrator.start_generation(request, 'user_123')

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.form_data == {
            'checkpoint': make_model_ref('main'),
            'loras': [{'model': make_model_ref('abc'), 'strength': 1.0}],
        }
        assert request.form_data == {
            'checkpoint': '/abs/path/main.safetensors',
            'loras': [{'model': '/abs/path/abc.safetensors', 'strength': 1.0}],
        }


class TestBeforeStartHookSeesUnresolvedRefs:
    @pytest.mark.asyncio
    async def test_hook_receives_model_refs_not_resolved_paths(
        self, orchestrator, mock_generation_repo, plugin_registry
    ):
        captured = {}

        def handler(context):
            captured['form_data'] = dict(context.data.get('form_data') or {})
            return context

        plugin_registry.hook_chain.register(GENERATION_HOOKS.before_start, 'test_plugin', handler)

        request = _make_request(form_data={'checkpoint': make_model_ref('main')})
        resolve = _fake_resolve({make_model_ref('main'): '/abs/path/main.safetensors'})

        with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough_bind), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_refs_2'), \
             patch('src.features.generation.orchestrator.resolve_form_model_refs', side_effect=resolve):
            await orchestrator.start_generation(request, 'user_123')

        assert captured['form_data'] == {'checkpoint': make_model_ref('main')}
        # And, symmetrically, the DB record persisted the same unresolved shape.
        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.form_data == {'checkpoint': make_model_ref('main')}


class TestResolveErrorPreventsPersistence:
    @pytest.mark.asyncio
    async def test_ref_not_available_prevents_generation_create(
        self, orchestrator, mock_generation_repo
    ):
        request = _make_request(form_data={'checkpoint': make_model_ref('missing')})

        with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough_bind), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_refs_3'), \
             patch('src.features.generation.orchestrator.resolve_form_model_refs',
                   side_effect=ModelRefNotAvailableError('backend cannot load this model')):
            with pytest.raises(ModelRefNotAvailableError):
                await orchestrator.start_generation(request, 'user_123')

        mock_generation_repo.create.assert_not_called()
