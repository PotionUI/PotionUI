"""
Tests for OutputProcessor.

This module tests the output processor which coordinates handling of generation
outputs by resolving each output's OutputTypeSpec (handler_cls) from the shared
output_type_registry, manages database updates, and handles errors gracefully.
"""

import asyncio
import threading
import time

import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.generation.output_processor import OutputProcessor
from src.pipelines.outputs import GenerationOutput
from src.features.generation.output_types import OutputTypeRegistry, OutputTypeSpec


class MockGenerationOutput(GenerationOutput):
    """Mock generation output for testing."""

    def __init__(self, **kwargs):
        self.data = kwargs.get('data', 'test_data')
        self.output_type = kwargs.get('output_type', 'test_output')
        self.progress = kwargs.get('progress', None)
        self.current_step = kwargs.get('current_step', None)
        self.current_step_num = kwargs.get('current_step_num', None)
        self.total_steps = kwargs.get('total_steps', None)


class MockProgress:
    """Mock progress object with current/max attributes."""

    def __init__(self, current, max):
        self.current = current
        self.max = max


class MockHandler:
    """Mock handler class emulating the BaseGenerationOutputHandler interface."""

    # Class-level hook so tests can control what handle() returns/raises
    handle_impl = None

    def __init__(self, generation_id, user_id=None, settings_manager=None, storage_driver=None):
        self.generation_id = generation_id
        self.user_id = user_id
        self.settings_manager = settings_manager
        self.storage_driver = storage_driver

    def handle(self, output):
        return type(self).handle_impl(output, self.generation_id, self.user_id, self.settings_manager)


@pytest.fixture
def mock_type_registry():
    """A fresh OutputTypeRegistry with a single spec for MockGenerationOutput."""
    registry = OutputTypeRegistry()
    registry.register(OutputTypeSpec(
        output_cls=MockGenerationOutput,
        key='mock',
        message_type='generation_update',
        serializer=None,
        handler_cls=MockHandler,
    ))
    return registry


@pytest.fixture
def mock_generation_repo():
    """Mock generation repository fixture."""
    with patch('src.features.generation.output_processor.generation_repo') as mock_repo:
        mock_repo.update_progress = Mock()
        yield mock_repo


@pytest.fixture
def output_processor(mock_type_registry, mock_settings_manager):
    """OutputProcessor instance with an isolated type registry."""
    return OutputProcessor(
        settings_manager=mock_settings_manager,
        type_registry=mock_type_registry
    )


class TestOutputProcessorStorageDriverPropagation:
    """`storage_driver` must reach every handler `OutputProcessor` constructs -
    it is the container's single shared driver, not something each handler
    resolves independently."""

    @pytest.mark.asyncio
    async def test_injected_storage_driver_reaches_the_handler(
        self, mock_type_registry, mock_settings_manager, mock_generation_repo
    ):
        mock_driver = Mock()
        processor = OutputProcessor(
            settings_manager=mock_settings_manager,
            storage_driver=mock_driver,
            type_registry=mock_type_registry,
        )

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, user_id, settings_manager: {'handler': 'MockHandler', 'processed': True}
        )

        # handle_impl only sees settings_manager, not storage_driver directly -
        # assert on the constructed handler instance instead by wrapping __init__.
        seen_drivers = []
        original_init = MockHandler.__init__

        def spying_init(self, generation_id, user_id=None, settings_manager=None, storage_driver=None):
            seen_drivers.append(storage_driver)
            original_init(self, generation_id, user_id, settings_manager, storage_driver)

        with patch.object(MockHandler, '__init__', spying_init):
            await processor.process_output("gen_1", MockGenerationOutput())

        assert seen_drivers == [mock_driver]


class TestOutputProcessorInitialization:
    """Test cases for OutputProcessor initialization."""

    def test_init_with_custom_type_registry(self, mock_type_registry, mock_settings_manager):
        """Test initialization with a custom output type registry."""
        processor = OutputProcessor(
            settings_manager=mock_settings_manager,
            type_registry=mock_type_registry
        )
        assert processor.type_registry == mock_type_registry
        assert processor.settings_manager == mock_settings_manager

    def test_init_with_default_type_registry(self, mock_settings_manager):
        """Test initialization falls back to the shared output_type_registry."""
        from src.features.generation import output_types as output_types_module

        processor = OutputProcessor(settings_manager=mock_settings_manager)
        assert processor.type_registry is output_types_module.output_type_registry

    def test_init_logs_registered_type_count(self, mock_type_registry, mock_settings_manager):
        """Test that initialization logs the number of registered output types."""
        with patch('src.features.generation.output_processor.logger') as mock_logger:
            processor = OutputProcessor(
                settings_manager=mock_settings_manager,
                type_registry=mock_type_registry
            )

        mock_logger.debug.assert_called_once()
        debug_message = mock_logger.debug.call_args[0][0]
        assert '1 registered output types' in debug_message


class TestOutputProcessorProcessing:
    """Test cases for output processing functionality."""

    @pytest.mark.asyncio
    async def test_process_output_success(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test successful output processing."""
        output = MockGenerationOutput(data="test_data")
        generation_id = "test_gen_123"

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, user_id, settings_manager: {
                'handler': 'MockHandler',
                'processed': True,
                'generation_id': gen_id,
                'output_type': type(out).__name__
            }
        )

        result = await output_processor.process_output(generation_id, output)

        assert result['handler'] == 'MockHandler'
        assert result['processed'] is True
        assert result['generation_id'] == generation_id

    @pytest.mark.asyncio
    async def test_process_output_with_user_id(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test output processing with user_id parameter."""
        output = MockGenerationOutput(data="test_data")
        generation_id = "test_gen_456"
        user_id = "user_789"

        captured = {}

        def handle_impl(out, gen_id, uid, settings_manager):
            captured['user_id'] = uid
            return {'handler': 'MockHandler', 'processed': True, 'generation_id': gen_id}

        MockHandler.handle_impl = staticmethod(handle_impl)

        await output_processor.process_output(generation_id, output, user_id)

        assert captured['user_id'] == user_id

    @pytest.mark.asyncio
    async def test_process_output_failure(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test failed output processing."""
        output = MockGenerationOutput(data="test_data")
        generation_id = "test_gen_456"

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'MockHandler',
                'processed': False,
                'error': 'Processing failed'
            }
        )

        result = await output_processor.process_output(generation_id, output)

        assert result['handler'] == 'MockHandler'
        assert result['processed'] is False
        assert result['error'] == 'Processing failed'

    @pytest.mark.asyncio
    async def test_process_output_no_handler(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test processing an output type with no registered spec."""
        class UnregisteredOutput(GenerationOutput):
            pass

        result = await output_processor.process_output("test_gen", UnregisteredOutput())

        assert result['handler'] == 'None'
        assert result['processed'] is False
        assert "No handler registered" in result['error']

    @pytest.mark.asyncio
    async def test_process_output_exception(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test output processing when handler raises exception."""
        output = MockGenerationOutput(data="test_data")
        generation_id = "test_gen_789"

        def raise_error(out, gen_id, uid, settings_manager):
            raise Exception("Handler registry error")

        MockHandler.handle_impl = staticmethod(raise_error)

        result = await output_processor.process_output(generation_id, output)

        assert result['handler'] == 'OutputProcessor'
        assert result['processed'] is False
        assert result['error'] == 'Handler registry error'

    @pytest.mark.asyncio
    async def test_process_output_logs_success(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test successful output processing logs correctly."""
        output = MockGenerationOutput(data="test_data")
        generation_id = "test_gen_log"

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'TestHandler',
                'processed': True,
                'generation_id': gen_id
            }
        )

        with patch('src.features.generation.output_processor.logger') as mock_logger:
            result = await output_processor.process_output(generation_id, output)

        mock_logger.debug.assert_called()
        debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
        success_logs = [msg for msg in debug_calls if 'Successfully processed' in msg]
        assert len(success_logs) > 0
        assert 'MockGenerationOutput' in success_logs[0]
        assert 'TestHandler' in success_logs[0]

    @pytest.mark.asyncio
    async def test_process_output_logs_failure(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test failed output processing logs warning."""
        output = MockGenerationOutput(data="test_data")
        generation_id = "test_gen_fail"

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'TestHandler',
                'processed': False,
                'error': 'Test error message'
            }
        )

        with patch('src.features.generation.output_processor.logger') as mock_logger:
            result = await output_processor.process_output(generation_id, output)

        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        assert 'Failed to process MockGenerationOutput' in warning_message
        assert 'Test error message' in warning_message


class TestOutputProcessorProgressTracking:
    """Test cases for progress tracking functionality."""

    @pytest.mark.asyncio
    async def test_progress_update_with_direct_value(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test progress update with direct float value."""
        output = MockGenerationOutput(
            data="test_data",
            progress=0.65,
            current_step="Step 13",
            current_step_num=13,
            total_steps=20
        )
        generation_id = "test_gen_progress"

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'MockHandler', 'processed': True, 'generation_id': gen_id
            }
        )

        await output_processor.process_output(generation_id, output)

        mock_generation_repo.update_progress.assert_called_once_with(
            generation_id, 0.65
        )

    @pytest.mark.asyncio
    async def test_progress_update_with_progress_object(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test progress update with progress object (current/max)."""
        output = MockGenerationOutput(
            data="test_data",
            progress=MockProgress(current=13, max=20),
            current_step="Generating",
            current_step_num=13,
            total_steps=20
        )
        generation_id = "test_gen_progress_obj"

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'MockHandler', 'processed': True, 'generation_id': gen_id
            }
        )

        await output_processor.process_output(generation_id, output)

        mock_generation_repo.update_progress.assert_called_once_with(
            generation_id, 0.65
        )

    @pytest.mark.asyncio
    async def test_progress_update_without_progress(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test that outputs without progress don't update database."""
        output = MockGenerationOutput(data="test_data")
        generation_id = "test_gen_no_progress"

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'MockHandler', 'processed': True, 'generation_id': gen_id
            }
        )

        await output_processor.process_output(generation_id, output)

        mock_generation_repo.update_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_progress_update_runs_off_the_event_loop(
        self,
        output_processor,
        mock_generation_repo,
        monkeypatch,
    ):
        """The per-step progress write must go through asyncio.to_thread, not
        run inline on the event loop."""
        recorded = []
        real_to_thread = asyncio.to_thread

        async def recording_to_thread(func, *args, **kwargs):
            recorded.append((func, args, kwargs))
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(
            'src.features.generation.output_processor.asyncio.to_thread',
            recording_to_thread,
        )

        output = MockGenerationOutput(data="test_data", progress=0.5)
        generation_id = "test_gen_offload"

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'MockHandler', 'processed': True, 'generation_id': gen_id
            }
        )

        await output_processor.process_output(generation_id, output)

        assert any(
            call[0] is mock_generation_repo.update_progress
            for call in recorded
        )
        mock_generation_repo.update_progress.assert_called_once_with(generation_id, 0.5)

    @pytest.mark.asyncio
    async def test_progress_update_with_only_step(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test progress update with only step information."""
        output = MockGenerationOutput(
            data="test_data",
            current_step="Loading model",
            current_step_num=1,
            total_steps=5
        )
        generation_id = "test_gen_step_only"

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'MockHandler', 'processed': True, 'generation_id': gen_id
            }
        )

        await output_processor.process_output(generation_id, output)

        mock_generation_repo.update_progress.assert_called_once_with(
            generation_id, None
        )

    @pytest.mark.asyncio
    async def test_progress_update_failure_doesnt_stop_processing(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test that progress update failure doesn't stop output processing."""
        output = MockGenerationOutput(
            data="test_data",
            progress=0.5,
            current_step="Processing"
        )
        generation_id = "test_gen_db_error"

        mock_generation_repo.update_progress = Mock(side_effect=Exception("Database error"))

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'MockHandler', 'processed': True, 'generation_id': gen_id
            }
        )

        result = await output_processor.process_output(generation_id, output)

        assert result['processed'] is True


class TestProgressCalculation:
    """Test cases for progress calculation logic."""

    def test_calculate_progress_direct_value(self, output_processor):
        """Test progress calculation with direct float value."""
        output = MockGenerationOutput(progress=0.75)
        progress = output_processor._calculate_progress(output)
        assert progress == 0.75

    def test_calculate_progress_object_value(self, output_processor):
        """Test progress calculation with progress object."""
        output = MockGenerationOutput(progress=MockProgress(current=15, max=20))
        progress = output_processor._calculate_progress(output)
        assert progress == 0.75

    def test_calculate_progress_zero_max(self, output_processor):
        """Test progress calculation with zero max value."""
        output = MockGenerationOutput(progress=MockProgress(current=5, max=0))
        progress = output_processor._calculate_progress(output)
        assert progress == 0.0

    def test_calculate_progress_none(self, output_processor):
        """Test progress calculation with None progress."""
        output = MockGenerationOutput(progress=None)
        progress = output_processor._calculate_progress(output)
        assert progress == 0.0

    def test_calculate_progress_no_attribute(self, output_processor):
        """Test progress calculation when output has no progress attribute."""
        output = MockGenerationOutput(data="test")
        delattr(output, 'progress')
        progress = output_processor._calculate_progress(output)
        assert progress == 0.0


class TestOutputTypeRegistration:
    """Test cases for output type registry access via OutputProcessor."""

    def test_get_registered_output_types(
        self,
        output_processor,
        mock_type_registry
    ):
        """Test getting list of registered OutputTypeSpec entries."""
        result = output_processor.get_registered_output_types()

        assert result == mock_type_registry.all()
        assert len(result) == 1
        assert result[0].key == 'mock'


class TestOutputProcessorIntegration:
    """Integration tests for OutputProcessor."""

    @pytest.mark.asyncio
    async def test_full_workflow_multiple_outputs(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test processing multiple outputs in sequence."""
        outputs = [
            MockGenerationOutput(data="output1", output_type="image", progress=0.33),
            MockGenerationOutput(data="output2", output_type="progress", progress=0.66),
            MockGenerationOutput(data="output3", output_type="artifact", progress=1.0)
        ]
        generation_id = "workflow_test_gen"

        call_results = []

        def track_calls(out, gen_id, uid, settings_manager):
            result = {
                'handler': f'Handler_{out.output_type}',
                'processed': True,
                'generation_id': gen_id,
                'output_data': out.data
            }
            call_results.append(result)
            return result

        MockHandler.handle_impl = staticmethod(track_calls)

        results = []
        for output in outputs:
            result = await output_processor.process_output(generation_id, output)
            results.append(result)

        assert len(results) == 3
        assert len(call_results) == 3
        assert mock_generation_repo.update_progress.call_count == 3

        for i, result in enumerate(results):
            assert result['processed'] is True
            assert result['generation_id'] == generation_id
            assert result['output_data'] == f"output{i+1}"

    @pytest.mark.asyncio
    async def test_mixed_success_failure_workflow(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test workflow with mixed success and failure results."""
        outputs = [
            MockGenerationOutput(data="success_output"),
            MockGenerationOutput(data="failure_output"),
            MockGenerationOutput(data="success_output2")
        ]
        generation_id = "mixed_workflow_gen"

        def mixed_results(out, gen_id, uid, settings_manager):
            if out.data == "failure_output":
                return {'handler': 'TestHandler', 'processed': False, 'error': 'Simulated failure'}
            return {'handler': 'TestHandler', 'processed': True, 'generation_id': gen_id}

        MockHandler.handle_impl = staticmethod(mixed_results)

        results = []
        for output in outputs:
            result = await output_processor.process_output(generation_id, output)
            results.append(result)

        assert results[0]['processed'] is True
        assert results[1]['processed'] is False
        assert results[1]['error'] == 'Simulated failure'
        assert results[2]['processed'] is True

    @pytest.mark.asyncio
    async def test_progress_tracking_throughout_workflow(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Test progress tracking through complete workflow."""
        generation_id = "progress_workflow_gen"

        workflow_steps = [
            MockGenerationOutput(data="init", progress=0.0, current_step="Initializing",
                                  current_step_num=0, total_steps=5),
            MockGenerationOutput(data="loading", progress=0.2, current_step="Loading model",
                                  current_step_num=1, total_steps=5),
            MockGenerationOutput(data="generating", progress=0.6, current_step="Generating",
                                  current_step_num=3, total_steps=5),
            MockGenerationOutput(data="finalizing", progress=1.0, current_step="Complete",
                                  current_step_num=5, total_steps=5)
        ]

        MockHandler.handle_impl = staticmethod(
            lambda out, gen_id, uid, settings_manager: {
                'handler': 'MockHandler', 'processed': True, 'generation_id': gen_id
            }
        )

        for step in workflow_steps:
            await output_processor.process_output(generation_id, step)

        assert mock_generation_repo.update_progress.call_count == 4

        progress_calls = mock_generation_repo.update_progress.call_args_list
        assert progress_calls[0][0][1] == 0.0
        assert progress_calls[1][0][1] == 0.2
        assert progress_calls[2][0][1] == 0.6
        assert progress_calls[3][0][1] == 1.0


class TestOutputProcessorHandlerOffload:
    """handler.handle() runs off the event loop thread."""

    @pytest.mark.asyncio
    async def test_handler_handle_runs_on_a_worker_thread(
        self,
        output_processor,
        mock_generation_repo
    ):
        """handler.handle() must not execute on the event-loop thread that
        called process_output()."""
        caller_thread_id = threading.get_ident()
        handler_thread_id = {}

        def handle_impl(out, gen_id, uid, settings_manager):
            handler_thread_id['id'] = threading.get_ident()
            return {'handler': 'MockHandler', 'processed': True}

        MockHandler.handle_impl = staticmethod(handle_impl)

        await output_processor.process_output("gen-thread", MockGenerationOutput())

        assert handler_thread_id['id'] != caller_thread_id

    @pytest.mark.asyncio
    async def test_sequential_calls_preserve_per_generation_order(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Callers (OutputBridge.run(), in production) await process_output()
        fully before processing the next output for the same generation.
        Even though handle() now runs on a thread pool, artificially varying
        the time each call takes must not scramble which result comes back
        for which call, as long as the caller awaits each in turn."""
        durations = [0.03, 0.0, 0.02, 0.0, 0.01]

        def handle_impl(out, gen_id, uid, settings_manager):
            time.sleep(out.data)
            return {'handler': 'MockHandler', 'processed': True, 'output_id': out.output_type}

        MockHandler.handle_impl = staticmethod(handle_impl)

        outputs = [
            MockGenerationOutput(data=d, output_type=f"output-{i}")
            for i, d in enumerate(durations)
        ]

        results = []
        for out in outputs:
            results.append(await output_processor.process_output("gen-order", out))

        assert [r['output_id'] for r in results] == [f"output-{i}" for i in range(len(durations))]

    @pytest.mark.asyncio
    async def test_db_write_happens_exactly_once_per_output(
        self,
        output_processor,
        mock_generation_repo
    ):
        """Offloading to a thread must not cause the handler's DB write to
        run more than once per processed output."""
        write_calls = []

        def handle_impl(out, gen_id, uid, settings_manager):
            write_calls.append(gen_id)
            return {'handler': 'MockHandler', 'processed': True}

        MockHandler.handle_impl = staticmethod(handle_impl)

        await output_processor.process_output("gen-once", MockGenerationOutput())

        assert write_calls == ["gen-once"]

    @pytest.mark.asyncio
    async def test_exception_in_threaded_handler_is_still_caught(
        self,
        output_processor,
        mock_generation_repo
    ):
        """An exception raised inside the thread-offloaded handler must
        propagate back through the awaited call and be caught the same way
        a synchronous handler exception was."""
        def handle_impl(out, gen_id, uid, settings_manager):
            raise RuntimeError("boom from worker thread")

        MockHandler.handle_impl = staticmethod(handle_impl)

        result = await output_processor.process_output("gen-error", MockGenerationOutput())

        assert result['handler'] == 'OutputProcessor'
        assert result['processed'] is False
        assert result['error'] == "boom from worker thread"
