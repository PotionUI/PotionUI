"""
Tests for GenerationOrchestrator prompt-segment persistence.

Verifies that `segments` on a GenerationRequest are persisted to the newly-created
generation record via GenerationSegmentRepository.create_for_generation, mirroring
the auto-tag application tests in test_orchestrator_auto_tags.py.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    """See test_orchestrator.py::_bind_form_passthrough."""
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough):
        yield


@pytest.fixture
def mock_pipeline_builder():
    """Mock PipelineBuilder that returns a minimal valid pipeline."""
    from src.features.generation.pipeline_builder import PipelineBuilder, BuiltPipeline
    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id='gen_segment_test',
        preset_id='test_preset',
        preset_template=Mock(version='1.0.0'),
        pipes=[{'name': 'generator', 'config': {}}]
    ))
    return builder


@pytest.fixture
def mock_backend():
    """Mock backend that does nothing on start_generation."""
    backend = Mock()
    backend.backend_id = 'local_backend_1'
    backend.name = 'Local Backend'
    backend.engine = 'native'
    backend.start_generation = AsyncMock()
    backend.cancel_generation = AsyncMock(return_value=True)
    return backend


@pytest.fixture
def mock_backend_registry(mock_backend):
    """Mock BackendRegistry pre-configured to return mock_backend."""
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
def mock_db():
    """Patch the database cursor used when updating preset_version."""
    mock_cursor = Mock()
    mock_cursor.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor.__exit__ = Mock(return_value=False)
    db = Mock()
    db.get_cursor = Mock(return_value=mock_cursor)
    with patch('src.platform.database.database.db', db):
        yield db


@pytest.fixture
def orchestrator(
    mock_pipeline_builder,
    mock_backend_registry,
    mock_connection_manager,
    mock_settings_manager,
    mock_output_processor,
    mock_preset_template_loader,
):
    """Create a GenerationOrchestrator with all deps mocked."""
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_manager=mock_connection_manager,
        settings_manager=mock_settings_manager,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
    )


def _make_request(segments=None):
    """Build a minimal GenerationRequest mock."""
    request = Mock()
    request.preset_id = 'test_preset_123'
    request.form_data = {'steps': 20}
    request.prompts = None
    request.mode = 'txt2img'
    request.backend_id = None
    request.tag_ids = []
    request.segments = segments
    return request


class TestSegmentPersistenceOnStartGeneration:
    """Tests that segments are persisted after the generation record is created."""

    @pytest.mark.asyncio
    async def test_segments_persisted_with_correct_generation_id(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        segments = [{'channel': 'positive', 'segment_index': 0, 'text': 'a segment'}]
        request = _make_request(segments=segments)

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_segment_1'), \
             patch(
                 'src.features.generation.segment_repository.generation_segment_repo'
             ) as mock_segment_repo:
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_segment_1'
        mock_segment_repo.create_for_generation.assert_called_once_with('gen_segment_1', segments)

    @pytest.mark.asyncio
    async def test_no_persistence_when_segments_empty(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        request = _make_request(segments=[])

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_no_segments'), \
             patch(
                 'src.features.generation.segment_repository.generation_segment_repo'
             ) as mock_segment_repo:
            await orchestrator.start_generation(request, 'user_123')

        mock_segment_repo.create_for_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_persistence_when_segments_is_none(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        request = _make_request(segments=None)

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_none_segments'), \
             patch(
                 'src.features.generation.segment_repository.generation_segment_repo'
             ) as mock_segment_repo:
            await orchestrator.start_generation(request, 'user_123')

        mock_segment_repo.create_for_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_segment_persistence_failure_is_swallowed(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        """A failing create_for_generation call must not abort the generation."""
        segments = [{'channel': 'positive', 'segment_index': 0, 'text': 'oops'}]
        request = _make_request(segments=segments)

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_segment_fail'), \
             patch(
                 'src.features.generation.segment_repository.generation_segment_repo'
             ) as mock_segment_repo:
            mock_segment_repo.create_for_generation.side_effect = Exception("db error")
            # Must not raise
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_segment_fail'
        assert result['status']['status'] == 'running'
