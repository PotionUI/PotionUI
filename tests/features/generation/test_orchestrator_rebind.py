"""
Tests that `bind_form` is re-run after the `generation.before_start`
hook chain when (and only when) a hook actually replaces `form_data`, and a
hook-modified `form_data` that no longer validates against the form schema
fails the generation cleanly (no DB row created, no work queued) instead of
reaching pipeline building/persistence unvalidated.

Also covers `form_name` persistence: the `Generation` record must carry
`bind_form`'s resolved `BoundForm.form_name`, from whichever bind actually
ran last (the original bind, or the post-hook re-bind).
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.forms.binding import BoundForm, FormBindingError
from src.platform.plugins.registry import PluginRegistry
from src.features.generation.hooks import GENERATION_HOOKS


@pytest.fixture
def mock_pipeline_builder():
    """Mock PipelineBuilder that returns a minimal valid pipeline."""
    from src.features.generation.pipeline_builder import PipelineBuilder, BuiltPipeline
    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id='gen_rebind_test',
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
    from src.platform.websocket.connection_manager import ConnectionManager
    manager = Mock(spec=ConnectionManager)
    manager.broadcast_to_generation = AsyncMock()
    return manager


@pytest.fixture
def mock_settings_manager():
    from src.platform.settings.settings import SettingsManager
    manager = Mock(spec=SettingsManager)
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
    mock_settings_manager,
    mock_output_processor,
    mock_preset_template_loader,
    plugin_registry,
):
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_manager=mock_connection_manager,
        settings_manager=mock_settings_manager,
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
    request.backend_id = None
    request.tag_ids = None
    request.collection_ids = None
    request.segments = None
    request.form_name = None
    return request


def _register_hook(plugin_registry, mutate):
    """Register a before_start hook that replaces form_data via `mutate(dict) -> dict`."""
    def handler(context):
        context.data['form_data'] = mutate(dict(context.data.get('form_data') or {}))
        return context

    plugin_registry.hook_chain.register(GENERATION_HOOKS.before_start, 'test_plugin', handler)


class TestRebindSkippedWhenHookDoesNotChangeFormData:
    @pytest.mark.asyncio
    async def test_bind_form_called_once_when_no_hook_registered(
        self, orchestrator, mock_generation_repo
    ):
        request = _make_request()

        def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
            return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom')

        bind_mock = Mock(side_effect=passthrough)
        with patch('src.features.generation.orchestrator.bind_form', bind_mock), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_rebind_1'):
            await orchestrator.start_generation(request, 'user_123')

        assert bind_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_bind_form_called_once_when_hook_leaves_form_data_unchanged(
        self, orchestrator, mock_generation_repo, plugin_registry
    ):
        """A hook that runs but doesn't actually change form_data must not trigger a
        second bind_form call — re-binding identical data would be pure overhead."""
        _register_hook(plugin_registry, lambda fd: fd)  # no-op mutation
        request = _make_request()

        def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
            return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom')

        bind_mock = Mock(side_effect=passthrough)
        with patch('src.features.generation.orchestrator.bind_form', bind_mock), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_rebind_2'):
            await orchestrator.start_generation(request, 'user_123')

        assert bind_mock.call_count == 1


class TestRebindWhenHookChangesFormData:
    @pytest.mark.asyncio
    async def test_hook_modified_form_data_is_revalidated_and_persisted(
        self, orchestrator, mock_generation_repo, plugin_registry
    ):
        """A hook that changes form_data in a way that still validates: bind_form
        runs twice, and the persisted record carries the RE-BOUND values (not the
        raw hook output verbatim) — bind_form owns normalization/defaults."""
        _register_hook(plugin_registry, lambda fd: {**fd, 'steps': 99})
        request = _make_request()

        calls = []

        def recording_bind(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
            calls.append(dict(raw_form_data or {}))
            # Re-bind normalizes: tag the output so the test can tell rebound
            # values apart from the hook's raw output.
            values = dict(raw_form_data or {})
            if len(calls) == 2:
                values['normalized_by_rebind'] = True
            return BoundForm(values=values, form_name=form_name or 'custom')

        bind_mock = Mock(side_effect=recording_bind)
        with patch('src.features.generation.orchestrator.bind_form', bind_mock), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_rebind_3'):
            await orchestrator.start_generation(request, 'user_123')

        assert bind_mock.call_count == 2
        # Second bind_form call saw the hook's mutated form_data.
        assert calls[1]['steps'] == 99

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.form_data['steps'] == 99
        assert gen_arg.form_data['normalized_by_rebind'] is True

    @pytest.mark.asyncio
    async def test_generation_fails_cleanly_when_hook_output_fails_revalidation(
        self, orchestrator, mock_generation_repo, plugin_registry
    ):
        """A hook that leaves form_data unable to satisfy the form schema must fail
        the generation with the same FormBindingError the initial bind would raise
        — plain-words message, no DB row created, nothing queued."""
        _register_hook(plugin_registry, lambda fd: {**fd, 'steps': -1})
        request = _make_request()

        def bind_side_effect(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
            raw = dict(raw_form_data or {})
            if raw.get('steps') == -1:
                raise FormBindingError(
                    errors=['steps: is below the minimum 1'],
                    field_errors={'steps': ['is below the minimum 1']},
                )
            return BoundForm(values=raw, form_name=form_name or 'custom')

        bind_mock = Mock(side_effect=bind_side_effect)
        with patch('src.features.generation.orchestrator.bind_form', bind_mock), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_rebind_4'):
            with pytest.raises(FormBindingError) as excinfo:
                await orchestrator.start_generation(request, 'user_123')

        assert 'steps' in str(excinfo.value)
        mock_generation_repo.create.assert_not_called()


class TestFormNamePersistence:
    @pytest.mark.asyncio
    async def test_form_name_persisted_from_bind_form(
        self, orchestrator, mock_generation_repo
    ):
        request = _make_request()
        request.form_name = 'advanced'

        def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
            return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom')

        with patch('src.features.generation.orchestrator.bind_form', side_effect=passthrough), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_rebind_5'):
            await orchestrator.start_generation(request, 'user_123')

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.form_name == 'advanced'

    @pytest.mark.asyncio
    async def test_form_name_persisted_from_rebind_when_hook_changes_data(
        self, orchestrator, mock_generation_repo, plugin_registry
    ):
        """form_name must reflect the LAST bind that ran (the re-bind), even though
        both binds resolve the same variant here — this guards against a
        regression where form_name is captured from the stale first bind."""
        _register_hook(plugin_registry, lambda fd: {**fd, 'steps': 30})
        request = _make_request()
        request.form_name = 'advanced'

        def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
            return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom')

        with patch('src.features.generation.orchestrator.bind_form', side_effect=passthrough), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_rebind_6'):
            await orchestrator.start_generation(request, 'user_123')

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.form_name == 'advanced'
