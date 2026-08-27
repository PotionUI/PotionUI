"""GenerationOrchestrator._finish_generation writing the durable generation_stats
row.

Fixture pattern mirrors test_orchestrator_auto_tags.py: every collaborator is
mocked, orchestrator constructed directly rather than through the container.
"""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.fixture
def mock_pipeline_builder():
    from src.features.generation.pipeline_builder import PipelineBuilder
    return Mock(spec=PipelineBuilder)


@pytest.fixture
def mock_backend():
    backend = Mock()
    backend.backend_id = 'local_backend_1'
    backend.name = 'Local Backend'
    backend.engine = 'native'
    return backend


@pytest.fixture
def mock_backend_registry(mock_backend):
    from src.features.backends.backend_registry import BackendRegistry
    registry = Mock(spec=BackendRegistry)
    registry.get_backend = Mock(return_value=mock_backend)
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
def mock_generation_stats_repository():
    from src.features.stats.generation_stats_repository import GenerationStatsRepository
    repository = Mock(spec=GenerationStatsRepository)
    repository.record_completion = Mock()
    return repository


@pytest.fixture
def mock_generation_repo():
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo:
        mock_repo.get_by_id = Mock(return_value=Mock(user_id='user_123'))
        yield mock_repo


@pytest.fixture
def orchestrator(
    mock_pipeline_builder, mock_backend_registry, mock_connection_manager,
    mock_settings, mock_output_processor, mock_preset_template_loader,
    mock_generation_stats_repository,
):
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_hub=mock_connection_manager,
        settings=mock_settings,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
        generation_stats_repository=mock_generation_stats_repository,
    )


def _completed_record():
    from src.features.generation.status_tracker import GenerationRecord, GenerationState
    return GenerationRecord(
        id='gen-1', preset_id='native/SDXL/base', backend_id='local_backend_1',
        state=GenerationState.COMPLETED, created_at=time.time() - 5.0, started_at=time.time() - 5.0,
    )


class TestRecordCompletionWiring:
    @pytest.mark.asyncio
    async def test_completed_generation_writes_stats_row(
        self, orchestrator, mock_generation_stats_repository, mock_generation_repo, mock_backend
    ):
        mock_backend.generation_engine = Mock()
        mock_backend.generation_engine.pop_resource_stats = Mock(return_value={
            'cold_start': True, 'model_load_ms': 4000.0, 'peak_vram_mb': 8192.0,
            'peak_ram_mb': 16384.0, 'cpu_percent': 55.0,
        })
        record = _completed_record()
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(return_value=record)

        await orchestrator._finish_generation('gen-1', record, output_callback=None)

        mock_generation_stats_repository.record_completion.assert_called_once()
        _, kwargs = mock_generation_stats_repository.record_completion.call_args
        assert kwargs['generation_id'] == 'gen-1'
        assert kwargs['preset_id'] == 'native/SDXL/base'
        assert kwargs['engine'] == 'native'
        assert kwargs['backend_id'] == 'local_backend_1'
        assert kwargs['cold_start'] is True
        assert kwargs['model_load_ms'] == 4000.0
        assert kwargs['peak_vram_mb'] == 8192.0
        assert kwargs['duration_ms'] > 0

    @pytest.mark.asyncio
    async def test_failed_generation_does_not_write_stats(
        self, orchestrator, mock_generation_stats_repository, mock_generation_repo
    ):
        from src.features.generation.status_tracker import GenerationRecord, GenerationState
        record = GenerationRecord(
            id='gen-2', preset_id='p1', backend_id='b1',
            state=GenerationState.FAILED, created_at=time.time(), started_at=time.time(),
        )
        orchestrator.status_tracker.get = Mock(return_value=record)

        await orchestrator._finish_generation('gen-2', record, output_callback=None)

        mock_generation_stats_repository.record_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_backend_generation_manager_still_records_with_nulls(
        self, orchestrator, mock_generation_stats_repository, mock_generation_repo, mock_backend
    ):
        """A backend with no `generation_engine.pop_resource_stats` (e.g. a
        plugin backend that predates the generation-stats feature) must not crash the write -- the
        resource fields are simply None."""
        del mock_backend.generation_engine  # Mock() would otherwise auto-vivify one
        mock_backend.generation_engine = None
        record = _completed_record()
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(return_value=record)

        await orchestrator._finish_generation('gen-1', record, output_callback=None)

        mock_generation_stats_repository.record_completion.assert_called_once()
        _, kwargs = mock_generation_stats_repository.record_completion.call_args
        assert kwargs['cold_start'] is None
        assert kwargs['peak_vram_mb'] is None

    @pytest.mark.asyncio
    async def test_stats_repository_exception_does_not_break_completion(
        self, orchestrator, mock_generation_stats_repository, mock_generation_repo
    ):
        mock_generation_stats_repository.record_completion.side_effect = Exception("db is locked")
        record = _completed_record()
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(return_value=record)

        # Must not raise.
        await orchestrator._finish_generation('gen-1', record, output_callback=None)

    @pytest.mark.asyncio
    async def test_no_generation_stats_repository_is_a_no_op(
        self, mock_pipeline_builder, mock_backend_registry, mock_connection_manager,
        mock_settings, mock_output_processor, mock_preset_template_loader,
        mock_generation_repo,
    ):
        from src.features.generation.orchestrator import GenerationOrchestrator
        orchestrator = GenerationOrchestrator(
            pipeline_builder=mock_pipeline_builder,
            backend_registry=mock_backend_registry,
            connection_hub=mock_connection_manager,
            settings=mock_settings,
            output_processor=mock_output_processor,
            preset_template_loader=mock_preset_template_loader,
        )  # no generation_stats_repository passed -- defaults to None
        record = _completed_record()
        orchestrator.status_tracker.get = Mock(return_value=record)
        orchestrator.status_tracker.transition = Mock(return_value=record)

        await orchestrator._finish_generation('gen-1', record, output_callback=None)  # must not raise
