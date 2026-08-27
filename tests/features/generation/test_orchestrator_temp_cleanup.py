"""GenerationOrchestrator._finish_generation cleaning up tracked temp video
sources.

Fixture pattern mirrors test_orchestrator_stats.py: every collaborator is
mocked, orchestrator constructed directly rather than through the container.
"""

import time
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def mock_pipeline_builder():
    from src.features.generation.pipeline_builder import PipelineBuilder
    return Mock(spec=PipelineBuilder)


@pytest.fixture
def mock_backend_registry():
    from src.features.backends.backend_registry import BackendRegistry
    registry = Mock(spec=BackendRegistry)
    backend = Mock()
    backend.backend_id = 'local_backend_1'
    backend.name = 'Local Backend'
    backend.engine = 'native'
    registry.get_backend = Mock(return_value=backend)
    return registry


@pytest.fixture
def mock_connection_manager():
    from src.platform.websocket.connection_hub import ConnectionHub
    return Mock(spec=ConnectionHub)


@pytest.fixture
def mock_settings():
    from src.platform.settings.settings import Settings
    return Mock(spec=Settings)


@pytest.fixture
def mock_output_processor():
    from src.features.generation.output_processor import OutputProcessor
    return Mock(spec=OutputProcessor)


@pytest.fixture
def mock_preset_template_loader():
    return Mock()


@pytest.fixture
def mock_generation_repo():
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo:
        mock_repo.get_by_id = Mock(return_value=Mock(user_id='user_123'))
        yield mock_repo


@pytest.fixture
def orchestrator(
    mock_pipeline_builder, mock_backend_registry, mock_connection_manager,
    mock_settings, mock_output_processor, mock_preset_template_loader,
):
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_hub=mock_connection_manager,
        settings=mock_settings,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
    )


def _record(state):
    from src.features.generation.status_tracker import GenerationRecord
    return GenerationRecord(
        id='gen-1', preset_id='native/Wan/video', backend_id='local_backend_1',
        state=state, created_at=time.time() - 5.0, started_at=time.time() - 5.0,
    )


class TestTempSourceCleanupWiring:
    """`_finish_generation` is the single place every terminal outcome
    (completed/failed/cancelled) passes through, so it's the one safe seam to
    unlink a generation's tracked temp video sources."""

    @pytest.mark.asyncio
    async def test_completed_generation_cleans_up_temp_sources(
        self, orchestrator, mock_generation_repo
    ):
        from src.features.generation.status_tracker import GenerationState
        record = _record(GenerationState.RUNNING)
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(return_value=_record(GenerationState.COMPLETED))

        with patch('src.features.generation.orchestrator.temp_source_tracker') as mock_tracker:
            mock_tracker.cleanup = Mock(return_value=2)
            await orchestrator._finish_generation('gen-1', record, output_callback=None)

        mock_tracker.cleanup.assert_called_once_with('gen-1')

    @pytest.mark.asyncio
    async def test_failed_generation_cleans_up_temp_sources(
        self, orchestrator, mock_generation_repo
    ):
        """A pipe error after the preview save but before the terminal gallery
        save must still unlink the source -- the failure path where the final
        save never happened."""
        from src.features.generation.status_tracker import GenerationState
        record = _record(GenerationState.FAILED)
        orchestrator.status_tracker.get = Mock(return_value=record)

        with patch('src.features.generation.orchestrator.temp_source_tracker') as mock_tracker:
            mock_tracker.cleanup = Mock(return_value=1)
            await orchestrator._finish_generation('gen-1', record, output_callback=None)

        mock_tracker.cleanup.assert_called_once_with('gen-1')

    @pytest.mark.asyncio
    async def test_cancelled_generation_cleans_up_temp_sources(
        self, orchestrator, mock_generation_repo
    ):
        from src.features.generation.status_tracker import GenerationState
        record = _record(GenerationState.CANCELLED)
        orchestrator.status_tracker.get = Mock(return_value=record)

        with patch('src.features.generation.orchestrator.temp_source_tracker') as mock_tracker:
            mock_tracker.cleanup = Mock(return_value=0)
            await orchestrator._finish_generation('gen-1', record, output_callback=None)

        mock_tracker.cleanup.assert_called_once_with('gen-1')

    @pytest.mark.asyncio
    async def test_cleanup_exception_does_not_break_completion(
        self, orchestrator, mock_generation_repo
    ):
        """Cleanup is best-effort -- a bug in it must never mask the actual
        generation outcome or break the completion/hook/notify flow."""
        from src.features.generation.status_tracker import GenerationState
        record = _record(GenerationState.RUNNING)
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(return_value=_record(GenerationState.COMPLETED))

        with patch('src.features.generation.orchestrator.temp_source_tracker') as mock_tracker:
            mock_tracker.cleanup = Mock(side_effect=Exception("disk error"))
            # Must not raise.
            await orchestrator._finish_generation('gen-1', record, output_callback=None)
