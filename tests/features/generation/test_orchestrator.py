"""
Comprehensive tests for GenerationOrchestrator.

Tests cover:
- Initialization and dependency injection
- Backend selection using preset's engine
- Unified pipeline execution (all backends receive pipeline_data + emit)
- Output handling and WebSocket broadcast
- Error handling and status updates via GenerationStatusTracker
- Generation cancellation
- User ID context passing

Note: All backends now use the same pipeline_data format and receive a
sync, thread-safe ``emit`` callable (backed by OutputBridge) instead of a
raw callback kwarg.
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch

# Avoid circular imports by importing only at runtime
# from src.features.generation.orchestrator import GenerationOrchestrator  # Imported in fixtures


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    """`bind_form` (src/core/form/binding.py) needs a real preset form-schema
    tree to resolve `form_name`/apply defaults; these tests use plain Mocks for
    the preset template, so patch it to a pure passthrough here. The
    orchestrator's actual `bind_form` wiring is covered by
    tests/core/form/test_binding.py and tests/core/generation/test_orchestrator_bind_form.py."""
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough):
        yield


@pytest.fixture
def mock_pipeline_builder():
    """Mock PipelineBuilder."""
    from src.features.generation.pipeline_builder import PipelineBuilder, BuiltPipeline
    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id='test_gen_123',
        preset_id='test_preset',
        preset_template=Mock(version='1.0.0'),
        pipes=[
            {'name': 'downloader', 'config': {}},
            {'name': 'generator', 'config': {'steps': 20}}
        ]
    ))
    return builder


@pytest.fixture
def mock_backend_registry():
    """Mock BackendRegistry."""
    from src.features.backends.backend_registry import BackendRegistry
    registry = Mock(spec=BackendRegistry)

    # Mock local backend
    local_backend = Mock()
    local_backend.backend_id = 'local_backend_1'
    local_backend.name = 'Local Backend'
    local_backend.engine = 'native'
    local_backend.start_generation = AsyncMock()
    local_backend.cancel_generation = AsyncMock(return_value=True)

    # Mock remote backend
    remote_backend = Mock()
    remote_backend.backend_id = 'remote_backend_1'
    remote_backend.name = 'Remote Backend'
    remote_backend.engine = 'comfyui'
    remote_backend.start_generation = AsyncMock()
    remote_backend.cancel_generation = AsyncMock(return_value=True)

    registry.select_backend_for_generation = Mock(return_value=local_backend)
    registry.get_backend = Mock(return_value=local_backend)
    registry._local_backend = local_backend
    registry._remote_backend = remote_backend

    return registry


@pytest.fixture
def mock_connection_manager():
    """Mock ConnectionManager."""
    from src.platform.websocket.connection_manager import ConnectionManager
    manager = Mock(spec=ConnectionManager)
    manager.broadcast_to_generation = AsyncMock()
    return manager


@pytest.fixture
def mock_settings_manager():
    """Mock SettingsManager."""
    from src.platform.settings.settings import SettingsManager
    manager = Mock(spec=SettingsManager)
    manager.get_setting = Mock(return_value='/outputs')
    return manager


@pytest.fixture
def mock_output_processor():
    """Mock OutputProcessor."""
    from src.features.generation.output_processor import OutputProcessor
    processor = Mock(spec=OutputProcessor)
    processor.process_output = AsyncMock(return_value={
        'handler': 'TestHandler',
        'processed': True
    })
    return processor


@pytest.fixture
def mock_generation_repo():
    """Mock generation repository (patched everywhere it's imported)."""
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo, \
         patch('src.features.generation.notifier.generation_repo', mock_repo), \
         patch('src.features.generation.status_tracker.generation_repo', mock_repo):
        mock_repo.create = Mock()
        mock_repo.update_status = Mock()
        mock_repo.get_by_id = Mock()
        yield mock_repo


@pytest.fixture
def mock_preset_template_loader():
    """Mock preset template loader."""
    loader = Mock()
    # Default: return a preset that speaks the native engine
    mock_preset = Mock()
    mock_preset.engine = 'native'
    loader.load_preset_by_id = Mock(return_value=mock_preset)
    return loader


@pytest.fixture
def orchestrator(
    mock_pipeline_builder,
    mock_backend_registry,
    mock_connection_manager,
    mock_settings_manager,
    mock_output_processor,
    mock_preset_template_loader
):
    """Create GenerationOrchestrator with mocked dependencies."""
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_manager=mock_connection_manager,
        settings_manager=mock_settings_manager,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader
    )


@pytest.fixture
def sample_request():
    """Sample GenerationRequest."""
    request = Mock()
    request.preset_id = 'test_preset_123'
    request.form_data = {'steps': 20, 'cfg_scale': 7.5}
    request.prompt = 'beautiful landscape'
    request.negative_prompt = 'ugly, blurry'
    request.prompts = None  # Explicit None for no prompts
    request.prompt_state = None
    request.mode = 'txt2img'
    request.backend_id = None
    request.tag_ids = None
    request.source_prompt_id = None
    return request


class TestGenerationOrchestratorInitialization:
    """Test cases for orchestrator initialization."""

    def test_initialization_with_dependencies(
        self,
        mock_pipeline_builder,
        mock_backend_registry,
        mock_connection_manager,
        mock_settings_manager,
        mock_output_processor,
        mock_preset_template_loader
    ):
        """Test proper initialization with all dependencies."""
        from src.features.generation.orchestrator import GenerationOrchestrator
        from src.features.generation.status_tracker import GenerationStatusTracker

        orchestrator = GenerationOrchestrator(
            pipeline_builder=mock_pipeline_builder,
            backend_registry=mock_backend_registry,
            connection_manager=mock_connection_manager,
            settings_manager=mock_settings_manager,
            output_processor=mock_output_processor,
            preset_template_loader=mock_preset_template_loader
        )

        assert orchestrator.pipeline_builder == mock_pipeline_builder
        assert orchestrator.backend_registry == mock_backend_registry
        assert orchestrator.connection_manager == mock_connection_manager
        assert orchestrator.settings_manager == mock_settings_manager
        assert orchestrator.output_processor == mock_output_processor
        assert orchestrator.preset_template_loader == mock_preset_template_loader
        assert isinstance(orchestrator.status_tracker, GenerationStatusTracker)

    def test_initialization_empty_tracking(self, orchestrator):
        """Test that the status tracker starts empty."""
        assert len(orchestrator.status_tracker.list_all()) == 0

    def test_accepts_injected_status_tracker(
        self,
        mock_pipeline_builder,
        mock_backend_registry,
        mock_connection_manager,
        mock_settings_manager,
        mock_output_processor,
        mock_preset_template_loader
    ):
        """A pre-built status tracker can be injected (as the composition root does)."""
        from src.features.generation.orchestrator import GenerationOrchestrator
        from src.features.generation.status_tracker import GenerationStatusTracker

        tracker = GenerationStatusTracker()
        orchestrator = GenerationOrchestrator(
            pipeline_builder=mock_pipeline_builder,
            backend_registry=mock_backend_registry,
            connection_manager=mock_connection_manager,
            settings_manager=mock_settings_manager,
            output_processor=mock_output_processor,
            preset_template_loader=mock_preset_template_loader,
            status_tracker=tracker
        )

        assert orchestrator.status_tracker is tracker


class TestBackendPersistence:
    """The selected backend is recorded on the Generation record."""

    @pytest.mark.asyncio
    async def test_start_generation_persists_selected_backend_id(
        self,
        orchestrator,
        sample_request,
        mock_generation_repo
    ):
        """Whatever select_backend_for_generation returned must be what is stored —
        an engine can have several backends, so the preset alone cannot identify it."""
        user_id = 'user_123'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_be_1'):
            await orchestrator.start_generation(sample_request, user_id)

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.backend_id == 'local_backend_1'

    @pytest.mark.asyncio
    async def test_persisted_backend_follows_selection_not_the_request(
        self,
        orchestrator,
        sample_request,
        mock_backend_registry,
        mock_generation_repo
    ):
        """A pinned backend_id is a *request*; the registry decides. We persist the
        decision, so history cannot disagree with what actually ran."""
        chosen = mock_backend_registry._remote_backend
        mock_backend_registry.select_backend_for_generation = Mock(return_value=chosen)
        user_id = 'user_123'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_be_2'):
            await orchestrator.start_generation(sample_request, user_id)

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.backend_id == 'remote_backend_1'


class TestModeAndPromptStatePersistence:
    """Test that mode and prompt_state are persisted on the Generation record."""

    @pytest.mark.asyncio
    async def test_start_generation_persists_mode_and_prompt_state(
        self,
        orchestrator,
        sample_request,
        mock_generation_repo
    ):
        """Non-default mode and a populated prompt_state are persisted as-is."""
        sample_request.mode = 'img2img'
        sample_request.prompt_state = {
            'segments': [{'text': 'a cat', 'weight': 1.0}],
            'timeline': ['step1', 'step2']
        }
        user_id = 'user_123'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_mode_1'):
            await orchestrator.start_generation(sample_request, user_id)

        mock_generation_repo.create.assert_called_once()
        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.mode == 'img2img'
        assert gen_arg.prompt_state == {
            'segments': [{'text': 'a cat', 'weight': 1.0}],
            'timeline': ['step1', 'step2']
        }

    @pytest.mark.asyncio
    async def test_start_generation_defaults_when_unset(
        self,
        orchestrator,
        sample_request,
        mock_generation_repo
    ):
        """Defaults are preserved when the request doesn't set mode/prompt_state."""
        user_id = 'user_123'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_mode_2'):
            await orchestrator.start_generation(sample_request, user_id)

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.mode == 'txt2img'
        assert gen_arg.prompt_state is None

    @pytest.mark.asyncio
    async def test_start_generation_uses_persisted_mode_for_pipeline_build(
        self,
        orchestrator,
        sample_request,
        mock_pipeline_builder,
        mock_generation_repo
    ):
        """_start_generation must use db_generation.mode, not re-derive from request."""
        sample_request.mode = 'img2img'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_mode_3'):
            await orchestrator.start_generation(sample_request, 'user_123')

        mock_pipeline_builder.build_pipeline.assert_called_once()
        _, kwargs = mock_pipeline_builder.build_pipeline.call_args
        assert kwargs['mode'] == 'img2img'


class TestSourcePromptIdPersistence:
    """`source_prompt_id` (Prompt Library provenance) is carried from the
    request onto the Generation record verbatim - see
    src.features.generation.dto.GenerationRequest.source_prompt_id."""

    @pytest.mark.asyncio
    async def test_start_generation_persists_source_prompt_id(
        self,
        orchestrator,
        sample_request,
        mock_generation_repo
    ):
        sample_request.source_prompt_id = 'prompt-abc'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_prompt_1'):
            await orchestrator.start_generation(sample_request, 'user_123')

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.source_prompt_id == 'prompt-abc'

    @pytest.mark.asyncio
    async def test_start_generation_defaults_source_prompt_id_to_none(
        self,
        orchestrator,
        sample_request,
        mock_generation_repo
    ):
        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_prompt_2'):
            await orchestrator.start_generation(sample_request, 'user_123')

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.source_prompt_id is None


class TestLocalGenerationStartup:
    """Test cases for starting local generations."""

    @pytest.mark.asyncio
    async def test_start_local_generation_success(
        self,
        orchestrator,
        sample_request,
        mock_backend_registry,
        mock_pipeline_builder,
        mock_generation_repo
    ):
        """Test successful local generation startup."""
        user_id = 'user_123'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_123'):
            result = await orchestrator.start_generation(sample_request, user_id)

        # Verify result structure. An idle backend dispatches inline, so the
        # generation is already RUNNING by the time start_generation returns;
        # it only stays 'pending' while it waits in the queue.
        assert result['generation_id'] == 'gen_123'
        assert result['status']['status'] == 'running'
        assert result['queue_position'] is None
        assert result['backend']['id'] == 'local_backend_1'
        assert result['backend']['name'] == 'Local Backend'
        assert result['backend']['engine'] == 'native'

        # Verify database record created
        mock_generation_repo.create.assert_called_once()
        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.id == 'gen_123'
        assert gen_arg.preset_id == 'test_preset_123'
        assert gen_arg.user_id == user_id
        assert gen_arg.status == 'pending'

        # Verify pipeline was built
        mock_pipeline_builder.build_pipeline.assert_called_once()

        # Verify backend was called with (pipeline_data, emit)
        backend = mock_backend_registry.select_backend_for_generation.return_value
        backend.start_generation.assert_called_once()
        call_args = backend.start_generation.call_args[0]
        assert call_args[0]['pipes']
        assert callable(call_args[1])  # bridge.emit

    @pytest.mark.asyncio
    async def test_start_generation_updates_status_tracking(
        self,
        orchestrator,
        sample_request,
        mock_generation_repo
    ):
        """Test that status tracking is updated on start."""
        user_id = 'user_123'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_456'):
            await orchestrator.start_generation(sample_request, user_id)

        # Verify status tracking
        assert orchestrator.status_tracker.get('gen_456') is not None
        status = orchestrator.status_tracker.get('gen_456')
        assert status.id == 'gen_456'
        # Dispatched inline onto an idle backend -> RUNNING, with started_at set
        # so duration_ms measures execution rather than queue wait.
        assert status.state.value == 'running'
        assert status.started_at is not None
        assert status.preset_id == 'test_preset_123'
        assert status.backend_id == 'local_backend_1'

    @pytest.mark.asyncio
    async def test_start_generation_with_specific_backend(
        self,
        orchestrator,
        sample_request,
        mock_backend_registry,
        mock_generation_repo
    ):
        """Test backend selection with specific backend_id."""
        user_id = 'user_123'
        sample_request.backend_id = 'specific_backend_id'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_789'):
            await orchestrator.start_generation(sample_request, user_id)

        # Verify backend registry was called with specific ID
        mock_backend_registry.select_backend_for_generation.assert_called_once()
        call_kwargs = mock_backend_registry.select_backend_for_generation.call_args[1]
        assert call_kwargs['backend_id'] == 'specific_backend_id'
        assert call_kwargs['engine'] == 'native'

    @pytest.mark.asyncio
    async def test_start_generation_pipeline_build_failure(
        self,
        orchestrator,
        sample_request,
        mock_pipeline_builder,
        mock_generation_repo
    ):
        """Test handling of pipeline build failure."""
        user_id = 'user_123'

        # Make pipeline builder fail
        mock_pipeline_builder.build_pipeline.side_effect = Exception("Invalid preset configuration")

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_fail'):
            with pytest.raises(Exception, match="Invalid preset configuration"):
                await orchestrator.start_generation(sample_request, user_id)

        # Verify status was transitioned to failed
        assert orchestrator.status_tracker.get('gen_fail').state.value == 'failed'
        mock_generation_repo.update_status.assert_called_with(
            'gen_fail', 'failed', error_message="Invalid preset configuration"
        )

    @pytest.mark.asyncio
    async def test_start_generation_updates_preset_version(
        self,
        orchestrator,
        sample_request,
        mock_pipeline_builder,
        mock_generation_repo
    ):
        """Test that preset version is recorded via the generation repository."""
        user_id = 'user_123'

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_version'):
            await orchestrator.start_generation(sample_request, user_id)

        mock_generation_repo.update_preset_version.assert_called_with('gen_version', '1.0.0')


class TestUnifiedBackendPath:
    """Test cases for unified backend handling.

    All backends (local, comfyui, etc.) now receive the same pipeline_data
    format plus a sync ``emit`` callable.
    """

    @pytest.mark.asyncio
    async def test_all_backends_receive_pipeline_data(
        self,
        orchestrator,
        sample_request,
        mock_backend_registry,
        mock_pipeline_builder,
        mock_generation_repo
    ):
        """Test that all backend types receive pipeline_data with pipes."""
        user_id = 'user_123'

        # Test with comfyui backend type
        comfyui_backend = Mock()
        comfyui_backend.backend_id = 'comfyui_backend_1'
        comfyui_backend.name = 'ComfyUI Backend'
        comfyui_backend.engine = 'comfyui'
        comfyui_backend.start_generation = AsyncMock()
        comfyui_backend.cancel_generation = AsyncMock(return_value=True)

        mock_backend_registry.select_backend_for_generation.return_value = comfyui_backend

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_comfyui'):
            result = await orchestrator.start_generation(sample_request, user_id)

        # Verify result
        assert result['backend']['engine'] == 'comfyui'
        assert result['backend']['name'] == 'ComfyUI Backend'

        # Verify backend was called with pipeline_data (not serialized request)
        comfyui_backend.start_generation.assert_called_once()
        call_args = comfyui_backend.start_generation.call_args[0]
        pipeline_data = call_args[0]

        # Verify it's pipeline_data with pipes (not serialized request)
        assert 'pipes' in pipeline_data
        assert isinstance(pipeline_data['pipes'], list)

        # Verify pipeline builder was called
        mock_pipeline_builder.build_pipeline.assert_called_once()


class TestOutputHandling:
    """Test cases for generation output handling."""

    @pytest.mark.asyncio
    async def test_handle_output_with_progress(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """Test handling output with progress information."""
        from src.pipelines.outputs import ProgressGenerationOutput
        from src.features.generation.status_tracker import GenerationState
        from src.pipelines.outputs import Progress
        generation_id = 'gen_progress'
        user_id = 'user_123'

        orchestrator.status_tracker.create(id=generation_id)
        orchestrator.status_tracker.transition(generation_id, GenerationState.RUNNING)

        # Mock repository to return generation
        mock_gen = Mock(user_id=user_id)
        mock_generation_repo.get_by_id.return_value = mock_gen

        # Create output with progress
        output = ProgressGenerationOutput(
            pipe_id=2,
            pipe_name='generator',
            state='Generating',
            progress=Progress(current=10, max=20)
        )
        output.current_step = 'Step 10'
        output.current_step_num = 10
        output.total_steps = 20

        callback = AsyncMock()
        await orchestrator._handle_generation_output(
            generation_id, output, 'local', callback
        )

        # Verify status was updated
        status = orchestrator.status_tracker.get(generation_id)
        assert status.progress == 0.5  # 10/20
        assert status.current_step == 'Step 10'
        assert status.current_step_num == 10
        assert status.total_steps == 20

        # Verify output processor was called
        mock_output_processor.process_output.assert_called_once_with(
            generation_id, output, user_id
        )

        # Verify callback was called
        callback.assert_called_once_with(generation_id, output)

    @pytest.mark.asyncio
    async def test_handle_error_output_fails_and_notifies(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """An ErrorGenerationOutput transitions to FAILED and raises a
        failure notification carrying the error + detail body."""
        from src.pipelines.outputs import ErrorGenerationOutput
        from src.features.generation.status_tracker import GenerationState

        generation_id = 'gen_error'
        user_id = 'user_err'
        orchestrator.status_tracker.create(id=generation_id)
        orchestrator.status_tracker.transition(generation_id, GenerationState.RUNNING)
        mock_generation_repo.get_by_id.return_value = Mock(user_id=user_id)

        output = ErrorGenerationOutput(
            error='KSampler: CUDA out of memory',
            detail='Node 12 (KSampler)\nRuntimeError: CUDA out of memory',
        )

        mock_manager = Mock()
        callback = AsyncMock()
        with patch('src.platform.plugins.runtime_registries.get_global_notification_manager', return_value=mock_manager):
            await orchestrator._handle_generation_output(
                generation_id, output, 'comfyui', callback
            )

        # Status transitioned to failed
        assert orchestrator.status_tracker.get(generation_id).state.value == 'failed'

        # Notification raised with the right shape (both toast + persistent via show_toast)
        mock_manager.assert_called_once()
        kwargs = mock_manager.call_args.kwargs
        assert kwargs['level'] == 'error'
        assert kwargs['type'] == 'generation.failed'
        assert kwargs['message'] == 'KSampler: CUDA out of memory'
        assert kwargs['show_toast'] is True
        assert kwargs['user_id'] == user_id
        assert kwargs['metadata']['generation_id'] == generation_id
        assert kwargs['metadata']['detail'] == 'Node 12 (KSampler)\nRuntimeError: CUDA out of memory'

    @pytest.mark.asyncio
    async def test_notify_generation_failure_swallows_errors(
        self,
        orchestrator,
        mock_generation_repo
    ):
        """A notification-manager failure must never break generation handling."""
        with patch('src.platform.plugins.runtime_registries.get_global_notification_manager',
                   side_effect=RuntimeError('NotificationManager not initialized yet')):
            # Should not raise
            orchestrator._notify_generation_failure('gen_x', 'boom', 'trace')

    @pytest.mark.asyncio
    async def test_handle_output_completion_signal(
        self,
        orchestrator,
        mock_generation_repo
    ):
        """Test handling completion signal (output=None)."""
        generation_id = 'gen_complete'
        orchestrator.status_tracker.create(id=generation_id, backend_id='local_backend_1')

        callback = AsyncMock()
        await orchestrator._handle_generation_output(
            generation_id, None, 'local', callback
        )

        # Verify status was updated
        status = orchestrator.status_tracker.get(generation_id)
        assert status.state.value == 'completed'
        assert status.completed_at is not None

        # Verify database was updated
        mock_generation_repo.update_status.assert_called_with(
            generation_id, 'completed', error_message=None
        )

        # Verify callback was called with None
        callback.assert_called_once_with(generation_id, None)

    @pytest.mark.asyncio
    async def test_handle_output_unknown_generation(
        self,
        orchestrator,
        mock_output_processor
    ):
        """Test handling output for unknown generation."""
        generation_id = 'unknown_gen'
        output = Mock()

        await orchestrator._handle_generation_output(
            generation_id, output, 'local', None
        )

        # Verify output processor was NOT called
        mock_output_processor.process_output.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_output_processor_error(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """Test handling when output processor raises error."""
        generation_id = 'gen_error'
        user_id = 'user_123'

        orchestrator.status_tracker.create(id=generation_id)
        mock_generation_repo.get_by_id.return_value = Mock(user_id=user_id)

        # Make output processor fail
        mock_output_processor.process_output.side_effect = Exception("Processing failed")

        # Create output with proper progress attribute that won't cause issues
        output = Mock()
        output.progress = 0.5  # Simple float progress
        output.current_step = None
        output.total_steps = None
        output.current_step_num = None
        callback = AsyncMock()

        # Should not raise exception
        await orchestrator._handle_generation_output(
            generation_id, output, 'local', callback
        )

        # Verify callback was still called
        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_error_output_transitions_to_failed(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """ErrorGenerationOutput should transition the tracked status to failed."""
        from src.pipelines.outputs import ErrorGenerationOutput

        generation_id = 'gen_error_output'
        orchestrator.status_tracker.create(id=generation_id)
        mock_generation_repo.get_by_id.return_value = Mock(user_id='user_123')

        output = ErrorGenerationOutput(error="pipe exploded")
        callback = AsyncMock()

        await orchestrator._handle_generation_output(generation_id, output, 'local', callback)

        status = orchestrator.status_tracker.get(generation_id)
        assert status.state.value == 'failed'
        assert status.error == 'pipe exploded'

    @pytest.mark.asyncio
    async def test_completion_after_error_does_not_revert_to_completed(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """The None sentinel after a FAILED transition must not overwrite it with COMPLETED."""
        from src.pipelines.outputs import ErrorGenerationOutput

        generation_id = 'gen_error_then_complete'
        orchestrator.status_tracker.create(id=generation_id)
        mock_generation_repo.get_by_id.return_value = Mock(user_id='user_123')

        await orchestrator._handle_generation_output(
            generation_id, ErrorGenerationOutput(error="boom"), 'local', None
        )
        await orchestrator._handle_generation_output(generation_id, None, 'local', None)

        assert orchestrator.status_tracker.get(generation_id).state.value == 'failed'

    @pytest.mark.asyncio
    async def test_stale_progress_after_terminal_state_is_dropped(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """Progress arriving after cancellation should not be processed."""
        from src.pipelines.outputs import ProgressGenerationOutput
        from src.features.generation.status_tracker import GenerationState

        generation_id = 'gen_cancelled_progress'
        orchestrator.status_tracker.create(id=generation_id)
        orchestrator.status_tracker.transition(generation_id, GenerationState.CANCELLED)

        output = ProgressGenerationOutput(state='still going')
        await orchestrator._handle_generation_output(generation_id, output, 'local', None)

        mock_output_processor.process_output.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_output_after_cancel_is_dropped(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """An ErrorGenerationOutput arriving after the record is already
        CANCELLED (the cancel route already transitioned it and broadcast
        generation_cancelled) must not relabel the generation as failed."""
        from src.pipelines.outputs import ErrorGenerationOutput
        from src.features.generation.status_tracker import GenerationState

        generation_id = 'gen_cancelled_error'
        orchestrator.status_tracker.create(id=generation_id)
        orchestrator.status_tracker.transition(generation_id, GenerationState.CANCELLED)

        mock_manager = Mock()
        with patch('src.platform.plugins.runtime_registries.get_global_notification_manager', return_value=mock_manager):
            await orchestrator._handle_generation_output(
                generation_id, ErrorGenerationOutput(error='sampling cancelled'), 'local', None
            )

        assert orchestrator.status_tracker.get(generation_id).state.value == 'cancelled'
        mock_output_processor.process_output.assert_not_called()
        mock_manager.assert_not_called()


class TestFinalSaveFailure:
    """A final (non-temporary) image/video/audio save failing is caught and
    reported as handler metadata, not an exception - _handle_generation_output
    must turn that metadata back into a generation failure instead of letting
    it sail through to COMPLETED (the None sentinel only wins the state when
    nothing already failed it)."""

    @pytest.mark.asyncio
    async def test_failed_final_image_save_fails_the_generation(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        from PIL import Image
        from src.pipelines.outputs import ImageGenerationOutput, ErrorGenerationOutput

        generation_id = 'gen_save_fail'
        user_id = 'user_save_fail'
        orchestrator.status_tracker.create(id=generation_id)
        mock_generation_repo.get_by_id.return_value = Mock(user_id=user_id)

        mock_output_processor.process_output.return_value = {
            'handler': 'ImageGenerationOutputHandler',
            'processed': True,  # matches the real handler's current shape
            'temporary': False,
            'save_error': 'Failed to save image',
        }

        output = ImageGenerationOutput(image=Image.new('RGB', (4, 4)), temporary=False)
        callback = AsyncMock()

        mock_manager = Mock()
        with patch('src.platform.plugins.runtime_registries.get_global_notification_manager', return_value=mock_manager):
            await orchestrator._handle_generation_output(generation_id, output, 'local', callback)

        status = orchestrator.status_tracker.get(generation_id)
        assert status.state.value == 'failed'
        assert status.error == 'Failed to save image'

        callback.assert_called_once()
        forwarded_output = callback.call_args[0][1]
        assert isinstance(forwarded_output, ErrorGenerationOutput)
        assert forwarded_output.error == 'Failed to save image'

        mock_manager.assert_called_once()
        assert mock_manager.call_args.kwargs['message'] == 'Failed to save image'

    @pytest.mark.asyncio
    async def test_processed_false_without_save_error_still_fails(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """The exception-path shape ('processed': False, 'error': ...) must
        fail the generation too, not just the 'save_error' key."""
        from PIL import Image
        from src.pipelines.outputs import ImageGenerationOutput

        generation_id = 'gen_save_fail_exc'
        mock_generation_repo.get_by_id.return_value = Mock(user_id='user_x')
        orchestrator.status_tracker.create(id=generation_id)

        mock_output_processor.process_output.return_value = {
            'handler': 'ImageGenerationOutputHandler',
            'processed': False,
            'error': 'disk is full',
        }

        output = ImageGenerationOutput(image=Image.new('RGB', (4, 4)), temporary=False)
        with patch('src.platform.plugins.runtime_registries.get_global_notification_manager', return_value=Mock()):
            await orchestrator._handle_generation_output(generation_id, output, 'local', None)

        assert orchestrator.status_tracker.get(generation_id).state.value == 'failed'
        assert orchestrator.status_tracker.get(generation_id).error == 'disk is full'

    @pytest.mark.asyncio
    async def test_temporary_output_save_metadata_does_not_fail_generation(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """A save_error on a TEMPORARY output (no attempt to persist it
        permanently) must not fail the generation - only a final save matters."""
        from PIL import Image
        from src.pipelines.outputs import ImageGenerationOutput

        generation_id = 'gen_temp_output'
        mock_generation_repo.get_by_id.return_value = Mock(user_id='user_x')
        orchestrator.status_tracker.create(id=generation_id)
        from src.features.generation.status_tracker import GenerationState
        orchestrator.status_tracker.transition(generation_id, GenerationState.RUNNING)

        mock_output_processor.process_output.return_value = {
            'handler': 'ImageGenerationOutputHandler',
            'processed': True,
            'temporary': True,
        }

        output = ImageGenerationOutput(image=Image.new('RGB', (4, 4)), temporary=True)
        callback = AsyncMock()
        await orchestrator._handle_generation_output(generation_id, output, 'local', callback)

        assert orchestrator.status_tracker.get(generation_id).state.value == 'running'
        callback.assert_called_once_with(generation_id, output)

    @pytest.mark.asyncio
    async def test_completion_after_final_save_failure_does_not_revert_to_completed(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        from PIL import Image
        from src.pipelines.outputs import ImageGenerationOutput

        generation_id = 'gen_save_fail_then_complete'
        mock_generation_repo.get_by_id.return_value = Mock(user_id='user_x')
        orchestrator.status_tracker.create(id=generation_id)

        mock_output_processor.process_output.return_value = {
            'handler': 'ImageGenerationOutputHandler',
            'processed': True,
            'temporary': False,
            'save_error': 'Failed to save image',
        }

        output = ImageGenerationOutput(image=Image.new('RGB', (4, 4)), temporary=False)
        with patch('src.platform.plugins.runtime_registries.get_global_notification_manager', return_value=Mock()):
            await orchestrator._handle_generation_output(generation_id, output, 'local', None)
        await orchestrator._handle_generation_output(generation_id, None, 'local', None)

        assert orchestrator.status_tracker.get(generation_id).state.value == 'failed'


class TestGenerationCancellation:
    """Test cases for generation cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_generation_success(
        self,
        orchestrator,
        mock_backend_registry,
        mock_generation_repo
    ):
        """Test successful generation cancellation."""
        generation_id = 'gen_cancel'
        orchestrator.status_tracker.create(id=generation_id, backend_id='local_backend_1')

        result = await orchestrator.cancel_generation(generation_id)

        assert result is True

        # Verify status was updated
        status = orchestrator.status_tracker.get(generation_id)
        assert status.state.value == 'cancelled'
        assert status.completed_at is not None

        # Verify database was updated
        mock_generation_repo.update_status.assert_called_with(
            generation_id, 'cancelled', error_message=None
        )

        # Verify backend was called
        backend = mock_backend_registry.get_backend.return_value
        backend.cancel_generation.assert_called_once_with(generation_id)

    @pytest.mark.asyncio
    async def test_cancel_unknown_generation(
        self,
        orchestrator
    ):
        """Test cancelling unknown generation."""
        result = await orchestrator.cancel_generation('unknown_gen')
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_already_completed_generation(
        self,
        orchestrator,
        mock_generation_repo
    ):
        """Test cancelling already completed generation."""
        from src.features.generation.status_tracker import GenerationState

        generation_id = 'gen_completed'
        orchestrator.status_tracker.create(id=generation_id)
        orchestrator.status_tracker.transition(generation_id, GenerationState.COMPLETED)

        result = await orchestrator.cancel_generation(generation_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled_generation(
        self,
        orchestrator,
        mock_generation_repo
    ):
        """Test cancelling already cancelled generation."""
        from src.features.generation.status_tracker import GenerationState

        generation_id = 'gen_cancelled'
        orchestrator.status_tracker.create(id=generation_id)
        orchestrator.status_tracker.transition(generation_id, GenerationState.CANCELLED)

        result = await orchestrator.cancel_generation(generation_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_generation_backend_error(
        self,
        orchestrator,
        mock_backend_registry,
        mock_generation_repo
    ):
        """Test cancellation when backend raises error."""
        generation_id = 'gen_backend_error'
        orchestrator.status_tracker.create(id=generation_id, backend_id='local_backend_1')

        # Make backend raise error
        backend = mock_backend_registry.get_backend.return_value
        backend.cancel_generation.side_effect = Exception("Backend error")

        # Should still update status despite backend error
        result = await orchestrator.cancel_generation(generation_id)
        assert result is True

        # Verify status was still updated
        assert orchestrator.status_tracker.get(generation_id).state.value == 'cancelled'


class TestStatusRetrieval:
    """Test cases for status retrieval."""

    @pytest.mark.asyncio
    async def test_get_generation_status_exists(self, orchestrator):
        """Test getting status for existing generation."""
        generation_id = 'gen_status'
        orchestrator.status_tracker.create(id=generation_id)

        status = await orchestrator.get_generation_status(generation_id)
        assert status.id == generation_id

    @pytest.mark.asyncio
    async def test_get_generation_status_not_found(self, orchestrator):
        """Test getting status for non-existent generation."""
        status = await orchestrator.get_generation_status('unknown_gen')
        assert status is None

    def test_list_active_generations(self, orchestrator):
        """Test listing active (pending/running) generations."""
        from src.features.generation.status_tracker import GenerationState

        orchestrator.status_tracker.create(id='gen1')
        orchestrator.status_tracker.create(id='gen2')
        orchestrator.status_tracker.transition('gen2', GenerationState.RUNNING)

        result = orchestrator.list_active_generations()

        assert len(result) == 2
        assert {r['id'] for r in result} == {'gen1', 'gen2'}

    def test_list_active_generations_empty(self, orchestrator):
        """Test listing when no active generations."""
        result = orchestrator.list_active_generations()
        assert result == []

    def test_list_active_generations_excludes_terminal(self, orchestrator):
        """Terminal-state generations should not appear in the active list."""
        from src.features.generation.status_tracker import GenerationState

        orchestrator.status_tracker.create(id='gen_done')
        orchestrator.status_tracker.transition('gen_done', GenerationState.COMPLETED)

        assert orchestrator.list_active_generations() == []


class TestErrorHandling:
    """Test cases for error handling."""

    @pytest.mark.asyncio
    async def test_start_generation_no_backend_available(
        self,
        orchestrator,
        sample_request,
        mock_backend_registry
    ):
        """Test error when no backend provides the preset's engine.

        The registry now raises NoBackendForEngineError instead of returning
        None from select_backend_for_generation().
        """
        from src.features.backends.backend_registry import NoBackendForEngineError

        mock_backend_registry.select_backend_for_generation.side_effect = NoBackendForEngineError(
            "No enabled backend provides engine 'native'"
        )

        with pytest.raises(NoBackendForEngineError, match="No enabled backend provides engine"):
            await orchestrator.start_generation(sample_request, 'user_123')

    @pytest.mark.asyncio
    async def test_start_generation_preset_not_found(
        self,
        orchestrator,
        sample_request,
        mock_preset_template_loader
    ):
        """Test error when the preset itself cannot be found."""
        mock_preset_template_loader.load_preset_by_id.return_value = None

        with pytest.raises(ValueError, match="not found"):
            await orchestrator.start_generation(sample_request, 'user_123')

    @pytest.mark.asyncio
    async def test_start_generation_database_error(
        self,
        orchestrator,
        sample_request,
        mock_generation_repo
    ):
        """Test handling of database errors during startup."""
        mock_generation_repo.create.side_effect = Exception("Database error")

        with pytest.raises(Exception, match="Database error"):
            await orchestrator.start_generation(sample_request, 'user_123')


class TestIntegrationWorkflows:
    """Integration tests for complete workflows."""

    @pytest.mark.asyncio
    async def test_complete_local_generation_workflow(
        self,
        orchestrator,
        sample_request,
        mock_output_processor,
        mock_generation_repo
    ):
        """Test complete workflow from start to completion."""
        user_id = 'user_workflow'

        # Start generation
        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_workflow'):
            result = await orchestrator.start_generation(sample_request, user_id)

        generation_id = result['generation_id']

        # Simulate progress output
        mock_generation_repo.get_by_id.return_value = Mock(user_id=user_id)

        progress_output = Mock()
        progress_output.progress = 0.5
        progress_output.current_step = 'Generating'
        progress_output.current_step_num = 5
        progress_output.total_steps = 10

        await orchestrator._handle_generation_output(
            generation_id, progress_output, 'local', None
        )

        # Verify progress was tracked
        assert orchestrator.status_tracker.get(generation_id).progress == 0.5

        # Simulate completion
        await orchestrator._handle_generation_output(
            generation_id, None, 'local', None
        )

        # Verify completion
        assert orchestrator.status_tracker.get(generation_id).state.value == 'completed'

    @pytest.mark.asyncio
    async def test_generation_with_multiple_outputs(
        self,
        orchestrator,
        mock_output_processor,
        mock_generation_repo
    ):
        """Test handling multiple outputs from a generation."""
        generation_id = 'gen_multi'
        user_id = 'user_123'

        orchestrator.status_tracker.create(id=generation_id)
        mock_generation_repo.get_by_id.return_value = Mock(user_id=user_id)

        # Send multiple outputs
        outputs = [
            Mock(progress=0.2, current_step='Loading', current_step_num=1, total_steps=5),
            Mock(progress=0.4, current_step='Processing', current_step_num=2, total_steps=5),
            Mock(progress=0.8, current_step='Finalizing', current_step_num=4, total_steps=5)
        ]

        for output in outputs:
            await orchestrator._handle_generation_output(
                generation_id, output, 'local', None
            )

        # Verify all outputs were processed
        assert mock_output_processor.process_output.call_count == 3

        # Verify final progress state
        assert orchestrator.status_tracker.get(generation_id).progress == 0.8
