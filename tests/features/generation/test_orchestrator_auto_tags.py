"""
Tests for GenerationOrchestrator auto-tag application.

Verifies that tag_ids on a GenerationRequest are applied to the
newly-created generation record via TagRepository.add_tag_to_generation.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, call


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
        generation_id='gen_tag_test',
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
    mock_settings,
    mock_output_processor,
    mock_preset_template_loader,
):
    """Create a GenerationOrchestrator with all deps mocked."""
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_hub=mock_connection_manager,
        settings=mock_settings,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
    )


def _make_request(tag_ids=None):
    """Build a minimal GenerationRequest mock."""
    request = Mock()
    request.preset_id = 'test_preset_123'
    request.form_data = {'steps': 20}
    request.prompts = None
    request.mode = 'txt2img'
    request.backend_id = None
    request.tag_ids = tag_ids or []
    request.collection_ids = []
    return request


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestAutoTagApplicationOnStartGeneration:
    """Tests that auto-tags are applied after the generation record is created."""

    @pytest.mark.asyncio
    async def test_auto_tags_applied_for_each_tag_id(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        """add_tag_to_generation is called once for every tag in request.tag_ids."""
        request = _make_request(tag_ids=['tag-1', 'tag-2'])

        mock_tag_repo_instance = Mock()
        mock_tag_repo_instance.add_tag_to_generation = Mock()

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_auto_tag'), \
             patch(
                 'src.features.tags.repository.TagRepository',
                 return_value=mock_tag_repo_instance
             ):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_auto_tag'

        expected_calls = [
            call('gen_auto_tag', 'tag-1'),
            call('gen_auto_tag', 'tag-2'),
        ]
        mock_tag_repo_instance.add_tag_to_generation.assert_has_calls(
            expected_calls, any_order=False
        )
        assert mock_tag_repo_instance.add_tag_to_generation.call_count == 2

    @pytest.mark.asyncio
    async def test_no_auto_tags_when_tag_ids_empty(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        """TagRepository is NOT instantiated when tag_ids is empty."""
        request = _make_request(tag_ids=[])

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_no_tag'), \
             patch('src.features.tags.repository.TagRepository') as mock_tag_cls:
            await orchestrator.start_generation(request, 'user_123')

        mock_tag_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_auto_tags_when_tag_ids_is_none(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        """TagRepository is NOT instantiated when tag_ids is None (falsy)."""
        request = _make_request(tag_ids=None)

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_none_tag'), \
             patch('src.features.tags.repository.TagRepository') as mock_tag_cls:
            await orchestrator.start_generation(request, 'user_123')

        mock_tag_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_tag_failure_is_swallowed_generation_still_starts(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        """A failing add_tag_to_generation call must not abort the generation."""
        request = _make_request(tag_ids=['bad-tag'])

        mock_tag_repo_instance = Mock()
        mock_tag_repo_instance.add_tag_to_generation = Mock(
            side_effect=Exception("Tag not found")
        )

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_tag_fail'), \
             patch(
                 'src.features.tags.repository.TagRepository',
                 return_value=mock_tag_repo_instance
             ):
            # Must not raise
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_tag_fail'
        assert result['status']['status'] == 'running'

    @pytest.mark.asyncio
    async def test_auto_tags_applied_with_correct_generation_id(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        """The generation_id passed to add_tag_to_generation must match the ULID."""
        request = _make_request(tag_ids=['tag-xyz'])

        mock_tag_repo_instance = Mock()
        mock_tag_repo_instance.add_tag_to_generation = Mock()

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='specific_ulid'), \
             patch(
                 'src.features.tags.repository.TagRepository',
                 return_value=mock_tag_repo_instance
             ):
            await orchestrator.start_generation(request, 'user_123')

        mock_tag_repo_instance.add_tag_to_generation.assert_called_once_with(
            'specific_ulid', 'tag-xyz'
        )

    @pytest.mark.asyncio
    async def test_auto_tags_applied_after_db_record_created(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        """DB record creation happens before auto-tag application."""
        call_order = []

        original_create = mock_generation_repo.create.side_effect
        mock_generation_repo.create.side_effect = lambda g: call_order.append('db_create')

        mock_tag_repo_instance = Mock()

        def record_tag_call(gen_id, tag_id):
            call_order.append(f'tag_{tag_id}')

        mock_tag_repo_instance.add_tag_to_generation = Mock(side_effect=record_tag_call)

        request = _make_request(tag_ids=['tag-a'])

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_order'), \
             patch(
                 'src.features.tags.repository.TagRepository',
                 return_value=mock_tag_repo_instance
             ):
            await orchestrator.start_generation(request, 'user_123')

        assert call_order.index('db_create') < call_order.index('tag_tag-a')

    @pytest.mark.asyncio
    async def test_single_auto_tag_applied(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        """A single tag_id results in exactly one call to add_tag_to_generation."""
        request = _make_request(tag_ids=['only-tag'])

        mock_tag_repo_instance = Mock()
        mock_tag_repo_instance.add_tag_to_generation = Mock()

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_single'), \
             patch(
                 'src.features.tags.repository.TagRepository',
                 return_value=mock_tag_repo_instance
             ):
            await orchestrator.start_generation(request, 'user_123')

        mock_tag_repo_instance.add_tag_to_generation.assert_called_once_with(
            'gen_single', 'only-tag'
        )


class TestAutoCollectionApplicationOnStartGeneration:
    """Tests that selected collections receive the new generation."""

    @pytest.mark.asyncio
    async def test_auto_collections_applied_for_each_collection(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        request = _make_request()
        request.collection_ids = ['collection-1', 'collection-2']

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_auto_collection'), \
             patch('src.features.collections.repository.CollectionRepository'), \
             patch('src.features.collections.operations.add_members') as mock_add_members:
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_auto_collection'
        assert mock_add_members.call_args_list[0].args[1:] == ('collection-1', ['gen_auto_collection'], 'user_123', 'history')
        assert mock_add_members.call_args_list[1].args[1:] == ('collection-2', ['gen_auto_collection'], 'user_123', 'history')

    @pytest.mark.asyncio
    async def test_auto_collection_failure_does_not_abort_generation(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        request = _make_request()
        request.collection_ids = ['missing-collection']

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_collection_fail'), \
             patch('src.features.collections.repository.CollectionRepository'), \
             patch('src.features.collections.operations.add_members', side_effect=ValueError('Collection not found')):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_collection_fail'
        assert result['status']['status'] == 'running'
