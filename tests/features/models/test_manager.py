"""
Tests for ModelIndexManager business logic.
"""

import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime

from pathlib import Path

from src.features.models.manager import ModelIndexManager, ListModelsParams, TYPE_DIR_MAP
from src.features.models.indexer import ModelScanner
from src.features.models.exceptions import (
    ModelAlreadyAssignedException,
    ModelNotFoundException,
    ModelAccessDeniedException,
    ModelIndexingException,
    ProviderFetchException,
    InvalidTagException,
    ModelDownloadException,
    ModelAssignmentException,
)
from src.platform.security.user import User, AccountType


@pytest.fixture
def mock_model_repository():
    """Create a mock model repository."""
    repo = Mock()
    repo.get_all.return_value = []
    repo.count_total.return_value = 0
    repo.count_by_type.return_value = {}
    repo.get_total_size_by_type.return_value = {}
    repo.get_available_model_ids_for_user.return_value = []
    return repo


@pytest.fixture
def mock_tag_repository():
    """Create a mock tag repository."""
    repo = Mock()
    repo.get_tag_by_id.return_value = None
    repo.set_model_tags.return_value = True
    return repo


@pytest.fixture
def mock_user_attribute_repository():
    """Create a mock per-user attribute overlay repository."""
    repo = Mock()
    repo.get_maps.return_value = {}
    repo.get_map.return_value = {}
    return repo


@pytest.fixture
def mock_plugin_registry():
    """Create a mock plugin registry."""
    registry = Mock()
    # Default: no hooks block
    mock_context = Mock()
    mock_context.data = {}
    registry.execute_hook.return_value = (mock_context, [])
    return registry


@pytest.fixture
def mock_admin_user():
    """Create a mock admin user."""
    user = Mock(spec=User)
    user.id = 'admin-user-id'
    user.account_type = AccountType.ADMIN
    return user


@pytest.fixture
def mock_regular_user():
    """Create a mock regular user."""
    user = Mock(spec=User)
    user.id = 'regular-user-id'
    user.account_type = AccountType.USER
    return user


@pytest.fixture
def manager(mock_model_repository, mock_tag_repository, mock_plugin_registry, mock_user_attribute_repository):
    """Create a ModelIndexManager instance with mocked dependencies."""
    mgr = ModelIndexManager(
        model_repository=mock_model_repository,
        tag_repository=mock_tag_repository,
        plugin_registry=mock_plugin_registry,
        settings_manager=Mock(),
        download_manager=Mock(),
        user_attribute_repository=mock_user_attribute_repository,
    )
    # The directory scanner is an injected dependency of the catalog; stub it so
    # the aggregate stats read never touches disk or the database.
    mgr._catalog.scanner = MagicMock()
    mgr._catalog.scanner.get_indexing_status.return_value = {}
    return mgr


class TestListModels:
    """Tests for list_models method."""

    def test_list_models_empty(self, manager, mock_model_repository, mock_admin_user):
        """Test listing models when none exist."""
        manager._catalog.scanner.get_indexing_status.return_value = {'total_models_db': 0}

        params = ListModelsParams()
        result = manager.list_models(params, mock_admin_user)

        assert result['models'] == []
        assert result['total'] == 0
        mock_model_repository.get_all.assert_called_once()

    def test_list_models_with_results(self, manager, mock_model_repository, mock_admin_user):
        """Test listing models returns proper data."""
        mock_model = Mock(id='test-id')
        mock_model.to_dict.return_value = {'id': 'test-id', 'filename': 'model.safetensors'}
        mock_model_repository.get_all.return_value = [mock_model]
        mock_model_repository.count_total.return_value = 1

        with patch('src.features.models.catalog.model_availability_repo') as mock_avail:
            manager._catalog.scanner.get_indexing_status.return_value = {'total_models_db': 1}
            mock_avail.backend_ids_by_model.return_value = {'test-id': ['local']}
            mock_avail.has_any.return_value = True

            params = ListModelsParams()
            result = manager.list_models(params, mock_admin_user)

        assert len(result['models']) == 1
        assert result['models'][0]['id'] == 'test-id'
        assert result['total'] == 1
        assert result['models'][0]['backend_ids'] == ['local']
        assert result['availability_indexed'] is True

    def test_list_models_marks_availability_unindexed_rather_than_unavailable(
        self, manager, mock_model_repository, mock_admin_user
    ):
        """`backend_ids: []` with `availability_indexed: false` means nobody asked.

        The UI must not render that as "available on no backend" — before the first
        index run every model would look broken.
        """
        mock_model = Mock(id='test-id')
        mock_model.to_dict.return_value = {'id': 'test-id', 'filename': 'model.safetensors'}
        mock_model_repository.get_all.return_value = [mock_model]
        mock_model_repository.count_total.return_value = 1

        with patch('src.features.models.catalog.model_availability_repo') as mock_avail:
            manager._catalog.scanner.get_indexing_status.return_value = {'total_models_db': 1}
            mock_avail.backend_ids_by_model.return_value = {}
            mock_avail.has_any.return_value = False

            result = manager.list_models(ListModelsParams(), mock_admin_user)

        assert result['models'][0]['backend_ids'] == []
        assert result['availability_indexed'] is False

    def test_list_models_queries_availability_once_for_the_page(
        self, manager, mock_model_repository, mock_admin_user
    ):
        """One query for the whole page, never one per row."""
        models = []
        for i in range(3):
            m = Mock(id=f'id-{i}')
            m.to_dict.return_value = {'id': f'id-{i}'}
            models.append(m)
        mock_model_repository.get_all.return_value = models
        mock_model_repository.count_total.return_value = 3

        with patch('src.features.models.catalog.model_availability_repo') as mock_avail:
            manager._catalog.scanner.get_indexing_status.return_value = {'total_models_db': 3}
            mock_avail.backend_ids_by_model.return_value = {}
            mock_avail.has_any.return_value = True

            manager.list_models(ListModelsParams(), mock_admin_user)

        mock_avail.backend_ids_by_model.assert_called_once_with(['id-0', 'id-1', 'id-2'])

    def test_list_models_filters_for_regular_user(self, manager, mock_model_repository, mock_regular_user):
        """Test that regular users get filtered model list."""
        mock_model_repository.get_available_model_ids_for_user.return_value = ['model-1', 'model-2']

        params = ListModelsParams()
        manager.list_models(params, mock_regular_user)

        # Verify that allowed_model_ids was passed
        call_kwargs = mock_model_repository.get_all.call_args[1]
        assert call_kwargs['allowed_model_ids'] == ['model-1', 'model-2']

    def test_list_models_admin_all_models(self, manager, mock_model_repository, mock_admin_user):
        """Test that admin with all_models=True gets all models."""
        params = ListModelsParams(all_models=True)
        manager.list_models(params, mock_admin_user)

        # Verify that allowed_model_ids is None (all models)
        call_kwargs = mock_model_repository.get_all.call_args[1]
        assert call_kwargs['allowed_model_ids'] is None


class TestGetModelTypes:
    """Tests for get_model_types, in particular the include_empty zero-count injection."""

    def _stub_scanner_mapping(self, manager):
        # The `manager` fixture replaces the catalog's scanner with a bare
        # MagicMock; MODEL_TYPE_MAPPING must be stubbed explicitly to the real
        # class attribute so get_model_types can read the known-types superset.
        # `models_dir` must be a real Path too - `_type_directory` joins it with
        # `TYPE_DIR_MAP`, and a bare MagicMock's `__truediv__` would silently
        # return another MagicMock instead of a real path.
        manager._catalog.scanner.MODEL_TYPE_MAPPING = ModelScanner.MODEL_TYPE_MAPPING
        manager._catalog.scanner.models_dir = Path('models')

    def test_admin_include_empty_adds_zero_count_types(
        self, manager, mock_model_repository, mock_admin_user
    ):
        self._stub_scanner_mapping(manager)
        mock_model_repository.count_by_type.return_value = {'checkpoint': 3, 'lora': 5}
        mock_model_repository.get_total_size_by_type.return_value = {'checkpoint': 1000, 'lora': 2000}

        result = manager.get_model_types(mock_admin_user, user_scoped=False, include_empty=True)

        types_by_name = {t['type']: t for t in result['types']}
        known_types = set(ModelScanner.MODEL_TYPE_MAPPING.values())
        expected_empty = known_types - {'checkpoint', 'lora'}

        assert expected_empty  # sanity: mapping has more types than the two with rows
        for model_type in expected_empty:
            entry = types_by_name[model_type]
            assert entry['count'] == 0
            assert entry['size_bytes'] == 0
            assert entry['size_mb'] == 0
            assert entry['size_gb'] == 0
            assert entry['directory'] == str(Path('models') / TYPE_DIR_MAP.get(model_type, model_type))

        # Existing non-empty types are untouched.
        assert types_by_name['checkpoint']['count'] == 3
        assert types_by_name['lora']['count'] == 5

    def test_admin_include_empty_false_is_unchanged(
        self, manager, mock_model_repository, mock_admin_user
    ):
        self._stub_scanner_mapping(manager)
        mock_model_repository.count_by_type.return_value = {'checkpoint': 3}
        mock_model_repository.get_total_size_by_type.return_value = {'checkpoint': 1000}

        result = manager.get_model_types(mock_admin_user, user_scoped=False, include_empty=False)

        assert [t['type'] for t in result['types']] == ['checkpoint']
        assert result['total_types'] == 1

        # Omitting include_empty entirely defaults to the same behavior.
        result_default = manager.get_model_types(mock_admin_user, user_scoped=False)
        assert [t['type'] for t in result_default['types']] == ['checkpoint']

    def test_admin_user_scoped_ignores_include_empty(
        self, manager, mock_model_repository, mock_admin_user
    ):
        self._stub_scanner_mapping(manager)
        mock_model_repository.get_available_model_ids_for_user.return_value = ['model-1']
        mock_model_repository.count_by_type.return_value = {'checkpoint': 1}
        mock_model_repository.get_total_size_by_type.return_value = {'checkpoint': 500}

        result = manager.get_model_types(mock_admin_user, user_scoped=True, include_empty=True)

        assert [t['type'] for t in result['types']] == ['checkpoint']
        assert result['total_types'] == 1

    def test_regular_user_ignores_include_empty(
        self, manager, mock_model_repository, mock_regular_user
    ):
        self._stub_scanner_mapping(manager)
        mock_model_repository.get_available_model_ids_for_user.return_value = ['model-1']
        mock_model_repository.count_by_type.return_value = {'lora': 2}
        mock_model_repository.get_total_size_by_type.return_value = {'lora': 200}

        result = manager.get_model_types(mock_regular_user, user_scoped=False, include_empty=True)

        assert [t['type'] for t in result['types']] == ['lora']
        assert result['total_types'] == 1

    def test_total_types_matches_returned_list_length(
        self, manager, mock_model_repository, mock_admin_user
    ):
        self._stub_scanner_mapping(manager)
        mock_model_repository.count_by_type.return_value = {}
        mock_model_repository.get_total_size_by_type.return_value = {}

        result = manager.get_model_types(mock_admin_user, user_scoped=False, include_empty=True)

        assert result['total_types'] == len(result['types'])
        assert result['total_types'] == len(set(ModelScanner.MODEL_TYPE_MAPPING.values()))


class TestGetModel:
    """Tests for get_model_by_id method."""

    def test_get_model_found(self, manager, mock_model_repository):
        """Test getting a model that exists."""
        mock_model = Mock()
        mock_model.id = 'test-id'
        mock_model.to_dict.return_value = {'id': 'test-id', 'filename': 'model.safetensors'}
        mock_model_repository.get_by_id.return_value = mock_model

        result = manager.get_model_by_id('test-id')

        assert result['model']['id'] == 'test-id'
        mock_model_repository.get_by_id.assert_called_once_with(
            'test-id', include_providers=False, library_user_id=None
        )

    def test_get_model_found_with_user_passes_library_user_id(
        self, manager, mock_model_repository, mock_regular_user
    ):
        """The per-user overlay (custom_name/is_favorite) needs the caller's id threaded through."""
        mock_model = Mock()
        mock_model.id = 'test-id'
        mock_model.to_dict.return_value = {'id': 'test-id', 'filename': 'model.safetensors'}
        mock_model_repository.get_by_id.return_value = mock_model

        manager.get_model_by_id('test-id', user=mock_regular_user)

        mock_model_repository.get_by_id.assert_called_once_with(
            'test-id', include_providers=False, library_user_id=mock_regular_user.id
        )

    def test_get_model_not_found(self, manager, mock_model_repository):
        """Test getting a model that doesn't exist."""
        mock_model_repository.get_by_id.return_value = None

        with pytest.raises(ModelNotFoundException):
            manager.get_model_by_id('nonexistent-id')


class TestGetModelByHash:
    """Tests for get_model_by_hash method."""

    def test_get_model_by_hash_found(self, manager, mock_model_repository):
        """Test getting a model by hash that exists."""
        mock_model = Mock()
        mock_model.id = 'test-id'
        mock_model.to_dict.return_value = {'id': 'test-id', 'sha256': 'abc123'}
        mock_model_repository.get_by_sha256.return_value = mock_model

        result = manager.get_model_by_hash('abc123')

        assert result['model']['sha256'] == 'abc123'
        mock_model_repository.get_by_sha256.assert_called_once_with('abc123', include_providers=False)

    def test_get_model_by_hash_not_found(self, manager, mock_model_repository):
        """Test getting a model by hash that doesn't exist."""
        mock_model_repository.get_by_sha256.return_value = None

        with pytest.raises(ModelNotFoundException):
            manager.get_model_by_hash('nonexistent-hash')


class TestGetModelGenerations:
    """Tests for get_model_generations method."""

    def test_get_generations_success(self, manager, mock_model_repository, mock_admin_user):
        """Test getting generations for a model."""
        mock_model = Mock()
        mock_model.id = 'model-id'
        mock_model_repository.get_by_id.return_value = mock_model

        mock_generation = Mock()
        mock_generation.to_dict.return_value = {'id': 'gen-id', 'status': 'completed'}

        with patch('src.features.generation.model_repository.generation_model_repo') as mock_gen_repo, \
             patch('src.features.models.catalog.tag_repo') as mock_tag_repo:
            mock_gen_repo.get_generations_by_model.return_value = ([mock_generation], 1)
            mock_tag_repo.get_generation_tags.return_value = []

            result = manager.get_model_generations('model-id', mock_admin_user)

        assert len(result['generations']) == 1
        assert result['total'] == 1

    def test_get_generations_model_not_found(self, manager, mock_model_repository, mock_admin_user):
        """Test getting generations for non-existent model."""
        mock_model_repository.get_by_id.return_value = None

        with pytest.raises(ModelNotFoundException):
            manager.get_model_generations('nonexistent-id', mock_admin_user)

    def test_get_generations_access_denied(self, manager, mock_model_repository, mock_regular_user):
        """Test getting generations without access."""
        mock_model = Mock()
        mock_model.id = 'model-id'
        mock_model_repository.get_by_id.return_value = mock_model
        mock_model_repository.get_available_model_ids_for_user.return_value = []  # No access

        with pytest.raises(ModelAccessDeniedException):
            manager.get_model_generations('model-id', mock_regular_user)


class TestIndexing:
    """Tests for indexing operations."""

    def test_start_indexing(self, manager, mock_plugin_registry):
        """Test starting indexing returns proper status."""
        result = manager.start_indexing()

        assert result['status'] == 'running'
        assert 'indexing started' in result['message'].lower()

    def test_start_indexing_blocked_by_hook(self, manager, mock_plugin_registry):
        """Test that a blocking hook prevents indexing."""
        mock_context = Mock()
        mock_context.data = {'blocked': True, 'block_reason': 'Test block'}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        with pytest.raises(ModelIndexingException) as exc_info:
            manager.start_indexing()

        assert 'Test block' in str(exc_info.value)

    def test_delete_model_success(self, manager, mock_model_repository, mock_plugin_registry):
        """Test deleting a model from index."""
        mock_model = Mock()
        mock_model.id = 'test-id'
        mock_model.filename = 'model.safetensors'
        mock_model_repository.get_by_id.return_value = mock_model
        mock_model_repository.delete.return_value = True

        result = manager.delete_model('test-id')

        assert 'removed from index' in result['message'].lower()
        mock_model_repository.delete.assert_called_once_with('test-id')

    def test_delete_model_not_found(self, manager, mock_model_repository):
        """Test deleting a model that doesn't exist."""
        mock_model_repository.get_by_id.return_value = None

        with pytest.raises(ModelNotFoundException):
            manager.delete_model('nonexistent-id')

    def test_delete_model_blocked_by_hook(self, manager, mock_model_repository, mock_plugin_registry):
        """Test that a blocking hook prevents deletion."""
        mock_model = Mock()
        mock_model.id = 'test-id'
        mock_model.filename = 'model.safetensors'
        mock_model_repository.get_by_id.return_value = mock_model

        mock_context = Mock()
        mock_context.data = {'blocked': True, 'block_reason': 'Cannot delete'}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        with pytest.raises(ModelIndexingException) as exc_info:
            manager.delete_model('test-id')

        assert 'Cannot delete' in str(exc_info.value)


class TestProviderFetch:
    """Tests for provider fetch operations."""

    def test_fetch_provider_info_success(self, manager, mock_plugin_registry):
        """Test starting provider fetch."""
        with patch('src.features.providers.registry.get_provider_registry') as mock_svc:
            mock_provider_svc = Mock()
            mock_provider_svc.get_provider.return_value = Mock()
            mock_provider_svc.is_provider_initialized.return_value = True
            mock_svc.return_value = mock_provider_svc

            result = manager.fetch_provider_info('civitai')

        assert result['status'] == 'running'
        assert result['provider'] == 'civitai'

    def test_fetch_provider_info_provider_not_found(self, manager):
        """Test fetch with non-existent provider."""
        with patch('src.features.providers.registry.get_provider_registry') as mock_svc:
            mock_provider_svc = Mock()
            mock_provider_svc.get_provider.return_value = None
            mock_svc.return_value = mock_provider_svc

            with pytest.raises(ProviderFetchException) as exc_info:
                manager.fetch_provider_info('nonexistent')

        assert 'not found' in str(exc_info.value).lower()

    def test_fetch_provider_info_provider_not_initialized(self, manager):
        """Test fetch with uninitialized provider."""
        with patch('src.features.providers.registry.get_provider_registry') as mock_svc:
            mock_provider_svc = Mock()
            mock_provider_svc.get_provider.return_value = Mock()
            mock_provider_svc.is_provider_initialized.return_value = False
            mock_svc.return_value = mock_provider_svc

            with pytest.raises(ProviderFetchException) as exc_info:
                manager.fetch_provider_info('civitai')

        assert 'not initialized' in str(exc_info.value).lower()


class TestTagOperations:
    """Tests for tag operations."""

    def test_update_model_tags_success(self, manager, mock_model_repository, mock_tag_repository):
        """Test updating model tags."""
        mock_model = Mock()
        mock_model.id = 'model-id'
        mock_model.to_dict.return_value = {'id': 'model-id', 'tags': []}
        mock_model_repository.get_by_id.return_value = mock_model

        mock_tag = Mock()
        mock_tag.type = 'MODEL'
        mock_tag_repository.get_tag_by_id.return_value = mock_tag
        mock_tag_repository.set_model_tags.return_value = True

        result = manager.update_model_tags('model-id', ['tag-1', 'tag-2'])

        assert 'updated successfully' in result['message'].lower()
        mock_tag_repository.set_model_tags.assert_called_once_with('model-id', ['tag-1', 'tag-2'])

    def test_update_model_tags_model_not_found(self, manager, mock_model_repository):
        """Test updating tags for non-existent model."""
        mock_model_repository.get_by_id.return_value = None

        with pytest.raises(ModelNotFoundException):
            manager.update_model_tags('nonexistent-id', ['tag-1'])

    def test_update_model_tags_invalid_tag(self, manager, mock_model_repository, mock_tag_repository):
        """Test updating tags with invalid tag ID."""
        mock_model = Mock()
        mock_model_repository.get_by_id.return_value = mock_model
        mock_tag_repository.get_tag_by_id.return_value = None

        with pytest.raises(InvalidTagException):
            manager.update_model_tags('model-id', ['invalid-tag-id'])

    def test_update_model_description_success(self, manager, mock_model_repository):
        """Test updating model description."""
        mock_model = Mock()
        mock_model.id = 'model-id'
        mock_model.to_dict.return_value = {'id': 'model-id', 'description': 'New description'}
        mock_model_repository.get_by_id.return_value = mock_model
        mock_model_repository.update_description.return_value = True

        result = manager.update_model_description('model-id', 'New description')

        assert 'updated successfully' in result['message'].lower()

    def test_update_model_prompting_guidance_success(self, manager, mock_model_repository):
        """Test updating model prompting guidance."""
        mock_model = Mock()
        mock_model.id = 'model-id'
        mock_model.to_dict.return_value = {'id': 'model-id', 'prompting_guidance': 'Use short tags'}
        mock_model_repository.get_by_id.return_value = mock_model
        mock_model_repository.update_prompting_guidance.return_value = True

        result = manager.update_model_prompting_guidance('model-id', 'Use short tags')

        assert 'updated successfully' in result['message'].lower()
        mock_model_repository.update_prompting_guidance.assert_called_once_with('model-id', 'Use short tags')

    def test_update_model_prompting_guidance_not_found(self, manager, mock_model_repository):
        """Test updating prompting guidance for a non-existent model."""
        mock_model_repository.get_by_id.return_value = None

        with pytest.raises(ModelNotFoundException):
            manager.update_model_prompting_guidance('nonexistent-id', 'Use short tags')

    def test_update_model_preview_registers_files_row_and_stores_files_url(
        self, manager, mock_model_repository, tmp_path, monkeypatch
    ):
        """Setting a preview registers the source as a `files` row and stores the
        auth-exempt /api/media/files/<id> URL (never the bearer-gated /uploads URL)."""
        import json as _json

        (tmp_path / 'uploads').mkdir()
        (tmp_path / 'uploads' / 'abc.png').write_bytes(b'fake-image-bytes')
        manager._metadata.settings.get_file_storage_directory.return_value = str(tmp_path)

        mock_model = Mock()
        mock_model.id = 'model-id'
        mock_model.preview_media = None
        mock_model.to_dict.return_value = {'id': 'model-id'}
        mock_model_repository.get_by_id.return_value = mock_model
        mock_model_repository.update_preview_media.return_value = True

        created = Mock()
        created.id = 'file-123'
        fake_repo = Mock()
        fake_repo.create.return_value = created
        monkeypatch.setattr('src.features.generation.file_repository.file_repo', fake_repo)

        result = manager.update_model_preview(
            'model-id',
            {'source_path': 'uploads/abc.png', 'type': 'image', 'name': 'abc.png'},
            user_id='admin-1',
        )

        assert 'updated successfully' in result['message'].lower()
        fake_repo.create.assert_called_once()
        created_file = fake_repo.create.call_args[0][0]
        assert created_file.file_path == 'uploads/abc.png'
        assert created_file.file_type == 'IMAGE'
        assert created_file.user_id == 'admin-1'

        stored = _json.loads(mock_model_repository.update_preview_media.call_args[0][1])
        assert stored['url'] == '/api/media/files/file-123'
        assert stored['file_id'] == 'file-123'
        assert stored['type'] == 'image'

    def test_update_model_preview_rejects_path_traversal(
        self, manager, mock_model_repository, tmp_path
    ):
        """A source_path escaping the storage root is refused (no files row created)."""
        manager._metadata.settings.get_file_storage_directory.return_value = str(tmp_path)

        mock_model = Mock()
        mock_model.id = 'model-id'
        mock_model.preview_media = None
        mock_model_repository.get_by_id.return_value = mock_model

        with pytest.raises(ModelIndexingException):
            manager.update_model_preview(
                'model-id',
                {'source_path': '../../etc/passwd', 'type': 'image'},
                user_id='admin-1',
            )
        mock_model_repository.update_preview_media.assert_not_called()

    def test_update_model_preview_clear_deletes_previous_file(
        self, manager, mock_model_repository, monkeypatch
    ):
        """Clearing persists NULL and drops the previous preview's files row."""
        mock_model = Mock()
        mock_model.id = 'model-id'
        mock_model.preview_media = {'file_id': 'old-file', 'url': '/api/media/files/old-file', 'type': 'image'}
        mock_model.to_dict.return_value = {'id': 'model-id'}
        mock_model_repository.get_by_id.return_value = mock_model
        mock_model_repository.update_preview_media.return_value = True

        fake_repo = Mock()
        monkeypatch.setattr('src.features.generation.file_repository.file_repo', fake_repo)

        result = manager.update_model_preview('model-id', None, user_id='admin-1')

        assert 'updated successfully' in result['message'].lower()
        mock_model_repository.update_preview_media.assert_called_once_with('model-id', None)
        fake_repo.delete.assert_called_once_with('old-file')

    def test_update_model_preview_not_found(self, manager, mock_model_repository):
        """Test setting preview for a non-existent model."""
        mock_model_repository.get_by_id.return_value = None

        with pytest.raises(ModelNotFoundException):
            manager.update_model_preview(
                'nonexistent-id', {'source_path': 'uploads/x.png', 'type': 'image'}, user_id='admin-1'
            )

class TestUserAssignments:
    """Tests for user assignment operations."""

    def test_get_user_model_assignments(self, manager, mock_model_repository):
        """Test getting user model assignments."""
        mock_assignment = Mock()
        mock_assignment.to_dict.return_value = {'id': 'assign-1', 'model_id': 'model-1', 'user_id': 'user-1'}
        mock_model_repository.get_user_models.return_value = [mock_assignment]

        result = manager.get_user_model_assignments('user-1')

        assert result['user_id'] == 'user-1'
        assert len(result['assignments']) == 1

    def test_assign_model_to_user_success(self, manager, mock_model_repository, mock_plugin_registry):
        """Test assigning a model to a user."""
        mock_assignment = Mock()
        mock_assignment.id = 'assign-1'
        mock_assignment.to_dict.return_value = {'id': 'assign-1'}
        mock_model_repository.assign_model_to_user.return_value = mock_assignment

        result = manager.assign_model_to_user('model-1', 'user-1')

        assert 'assigned' in result['message'].lower()
        mock_model_repository.assign_model_to_user.assert_called_once_with('model-1', 'user-1')

    def test_assign_model_to_user_fails(self, manager, mock_model_repository, mock_plugin_registry):
        """Test assigning a model that's already assigned."""
        mock_model_repository.assign_model_to_user.return_value = None

        with pytest.raises(ModelAssignmentException):
            manager.assign_model_to_user('model-1', 'user-1')

    def test_unassign_model_from_user_success(self, manager, mock_model_repository, mock_plugin_registry):
        """Test unassigning a model from a user."""
        mock_model_repository.unassign_model_from_user.return_value = True

        result = manager.unassign_model_from_user('model-1', 'user-1')

        assert 'unassigned' in result['message'].lower()

    def test_unassign_model_from_user_not_found(self, manager, mock_model_repository, mock_plugin_registry):
        """Test unassigning a model that's not assigned."""
        mock_model_repository.unassign_model_from_user.return_value = False

        with pytest.raises(ModelAssignmentException):
            manager.unassign_model_from_user('model-1', 'user-1')


class TestDownloadAndIndex:
    """Tests for download and index operations."""

    def test_start_download_and_index(self, manager, mock_plugin_registry):
        """Test starting download and index."""
        result = manager.start_download_and_index(
            name='test-model',
            link='https://example.com/model.safetensors',
            size='1GB',
            sha256='abc123'
        )

        assert result['status'] == 'started'
        assert result['model_name'] == 'test-model'

    def test_start_download_and_index_blocked(self, manager, mock_plugin_registry):
        """Test that a blocking hook prevents download."""
        mock_context = Mock()
        mock_context.data = {'blocked': True, 'block_reason': 'Download blocked'}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        with pytest.raises(ModelDownloadException) as exc_info:
            manager.start_download_and_index(
                name='test-model',
                link='https://example.com/model.safetensors',
                size='1GB',
                sha256='abc123'
            )

        assert 'Download blocked' in str(exc_info.value)


class TestModelTypes:
    """Tests for get_model_types method."""

    def test_get_model_types_admin(self, manager, mock_model_repository, mock_admin_user):
        """Test getting model types as admin."""
        mock_model_repository.count_by_type.return_value = {'checkpoint': 5, 'lora': 3}
        mock_model_repository.get_total_size_by_type.return_value = {'checkpoint': 1000000, 'lora': 500000}

        result = manager.get_model_types(mock_admin_user)

        assert result['total_types'] == 2
        assert len(result['types']) == 2

    def test_get_model_types_user_scoped(self, manager, mock_model_repository, mock_regular_user):
        """Test getting model types with user scope."""
        mock_model_repository.get_available_model_ids_for_user.return_value = ['model-1']
        mock_model_repository.count_by_type.return_value = {'checkpoint': 1}
        mock_model_repository.get_total_size_by_type.return_value = {'checkpoint': 100000}

        result = manager.get_model_types(mock_regular_user, user_scoped=True)

        # Verify user-scoped filtering was applied
        mock_model_repository.get_available_model_ids_for_user.assert_called_once_with(mock_regular_user.id)


class TestCleanupDeletedModels:
    """Tests for cleanup_deleted_models method."""

    def test_cleanup_deleted_models(self, manager, mock_model_repository):
        """Test cleanup of deleted models."""
        mock_model1 = Mock()
        mock_model1.id = 'model-1'
        mock_model1.file_path = '/existing/path.safetensors'

        mock_model2 = Mock()
        mock_model2.id = 'model-2'
        mock_model2.file_path = '/deleted/path.safetensors'

        mock_model_repository.get_all.return_value = [mock_model1, mock_model2]

        with patch('src.features.models.indexing_coordinator.Path') as mock_path:
            mock_path.return_value.exists.side_effect = [True, False]  # First exists, second doesn't

            result = manager.cleanup_deleted_models()

        assert result['deleted_from_index'] == 1
        assert result['total_checked'] == 2
        mock_model_repository.delete.assert_called_once_with('model-2')


class TestAssignmentFailureIsExplained:
    """
    `user_models` enforces UNIQUE(user_id, model_id) and foreign keys onto both
    `users` and `models`. SQLite collapses all of them into one IntegrityError,
    and the manager used to relay that as "Model already assigned to user or
    invalid IDs" - four causes, none of them identified. Each must now be told apart.
    """

    def _fail_insert(self, repo):
        repo.assign_model_to_user.return_value = None

    def test_blank_ids_are_rejected_before_touching_the_database(self, manager, mock_model_repository):
        with pytest.raises(ModelAssignmentException) as exc:
            manager.assign_model_to_user('', 'user-1')

        assert 'model_id' in str(exc.value) and 'user_id' in str(exc.value)
        mock_model_repository.assign_model_to_user.assert_not_called()

    def test_already_assigned_raises_the_idempotency_signal(self, manager, mock_model_repository):
        self._fail_insert(mock_model_repository)
        existing = Mock()
        mock_model_repository.find_user_model_assignment.return_value = existing

        with pytest.raises(ModelAlreadyAssignedException) as exc:
            manager.assign_model_to_user('model-1', 'user-1')

        assert exc.value.assignment is existing
        assert 'already assigned' in str(exc.value)
        # Still a ModelAssignmentException, so the REST layer is unaffected.
        assert isinstance(exc.value, ModelAssignmentException)

    def test_unknown_model_is_named(self, manager, mock_model_repository):
        self._fail_insert(mock_model_repository)
        mock_model_repository.find_user_model_assignment.return_value = None
        mock_model_repository.get_by_id.return_value = None

        with pytest.raises(ModelAssignmentException) as exc:
            manager.assign_model_to_user('ghost-model', 'user-1')

        assert "No model with id 'ghost-model'" in str(exc.value)
        assert not isinstance(exc.value, ModelAlreadyAssignedException)

    def test_unknown_user_is_named(self, manager, mock_model_repository):
        self._fail_insert(mock_model_repository)
        mock_model_repository.find_user_model_assignment.return_value = None
        mock_model_repository.get_by_id.return_value = Mock()  # model exists

        with pytest.raises(ModelAssignmentException) as exc:
            manager.assign_model_to_user('model-1', 'ghost-user')

        assert 'no such user' in str(exc.value)
        assert 'ghost-user' in str(exc.value)
        assert not isinstance(exc.value, ModelAlreadyAssignedException)


class TestGetModelAvailability:
    """Where a model can be loaded, and under what name on each backend."""

    def _availability(self, manager, rows, backends, has_any=True):
        with patch('src.features.models.catalog.model_availability_repo') as mock_avail, \
             patch('src.features.backends.repository.backend_repo') as mock_be:
            mock_avail.get_for_model.return_value = rows
            mock_avail.has_any.return_value = has_any
            mock_be.get_all.return_value = backends
            return manager.get_model_availability('m1')

    @staticmethod
    def _backend(bid, name, engine):
        """`Mock(name=...)` sets the mock's own name, not the attribute."""
        backend = Mock(id=bid, engine=engine)
        backend.name = name
        return backend

    def _row(self, backend_id, ref, size, confidence='reported'):
        row = Mock()
        row.size = size
        row.to_dict.return_value = {
            'id': 'a1', 'model_id': 'm1', 'backend_id': backend_id,
            'ref': ref, 'size': size, 'confidence': confidence, 'indexed_at': None,
        }
        row.backend_id = backend_id
        return row

    def test_each_backend_reports_its_own_engine_native_ref(self, manager):
        """The ref differs per engine: a path natively, a bare name on ComfyUI."""
        rows = [
            self._row('local', 'models/loras/detail.safetensors', 100, 'verified'),
            self._row('comfy', 'style/detail.safetensors', 100),
        ]
        backends = [
            self._backend('local', 'Local Generation', 'native'),
            self._backend('comfy', 'ComfyUI', 'comfyui'),
        ]

        result = self._availability(manager, rows, backends)

        by_backend = {e['backend_id']: e for e in result['availability']}
        assert by_backend['local']['ref'] == 'models/loras/detail.safetensors'
        assert by_backend['local']['engine'] == 'native'
        assert by_backend['comfy']['ref'] == 'style/detail.safetensors'
        assert by_backend['comfy']['backend_name'] == 'ComfyUI'
        assert result['size_conflict'] is False

    def test_differing_sizes_across_backends_raise_a_size_conflict(self, manager):
        """Same filename, different byte counts: the backends hold different weights."""
        rows = [
            self._row('local', 'models/checkpoints/a.safetensors', 23_000_000_000),
            self._row('comfy', 'a.safetensors', 11_000_000_000),
        ]
        backends = [
            self._backend('local', 'Local', 'native'),
            self._backend('comfy', 'ComfyUI', 'comfyui'),
        ]

        assert self._availability(manager, rows, backends)['size_conflict'] is True

    def test_missing_sizes_are_not_a_conflict(self, manager):
        """`name_only` confidence carries no size; absence is not disagreement."""
        rows = [
            self._row('comfy_a', 'a.safetensors', None, 'name_only'),
            self._row('comfy_b', 'a.safetensors', 100),
        ]
        backends = [
            self._backend('comfy_a', 'A', 'comfyui'),
            self._backend('comfy_b', 'B', 'comfyui'),
        ]

        assert self._availability(manager, rows, backends)['size_conflict'] is False

    def test_model_on_no_backend_reports_whether_anything_was_indexed(self, manager):
        """Empty availability means "nowhere" only once something has been indexed."""
        unindexed = self._availability(manager, [], [], has_any=False)
        assert unindexed['availability'] == [] and unindexed['indexed'] is False

        indexed = self._availability(manager, [], [], has_any=True)
        assert indexed['availability'] == [] and indexed['indexed'] is True

    def test_deleted_backend_falls_back_to_its_id(self, manager):
        """Availability rows cascade on backend delete, but never render a crash."""
        rows = [self._row('ghost', 'a.safetensors', 100)]

        result = self._availability(manager, rows, [])

        assert result['availability'][0]['backend_name'] == 'ghost'
        assert result['availability'][0]['engine'] is None


class TestUserFacingListModels:
    """A generating user gets no backend topology and no operational fields."""

    def _list(self, manager, mock_model_repository, user):
        m = Mock(id='m1')
        m.to_dict.return_value = {'id': 'm1', 'name': 'detail'}
        mock_model_repository.get_all.return_value = [m]
        mock_model_repository.count_total.return_value = 1

        with patch('src.features.models.catalog.model_availability_repo') as mock_avail:
            manager._catalog.scanner.get_indexing_status.return_value = {'total_models_db': 1}
            mock_avail.backend_ids_by_model.return_value = {'m1': ['local']}
            mock_avail.has_any.return_value = True
            result = manager.list_models(ListModelsParams(), user)
        return result, m, mock_avail

    def test_regular_user_gets_no_backend_ids_and_no_indexed_flag(
        self, manager, mock_model_repository, mock_regular_user
    ):
        result, _, mock_avail = self._list(manager, mock_model_repository, mock_regular_user)

        assert 'backend_ids' not in result['models'][0]
        assert 'availability_indexed' not in result
        mock_avail.backend_ids_by_model.assert_not_called()

    def test_regular_user_gets_the_restricted_serialization(
        self, manager, mock_model_repository, mock_regular_user
    ):
        _, model, _ = self._list(manager, mock_model_repository, mock_regular_user)

        assert model.to_dict.call_args.kwargs['admin'] is False

    def test_admin_gets_backend_ids_and_the_full_serialization(
        self, manager, mock_model_repository, mock_admin_user
    ):
        result, model, _ = self._list(manager, mock_model_repository, mock_admin_user)

        assert result['models'][0]['backend_ids'] == ['local']
        assert result['availability_indexed'] is True
        assert model.to_dict.call_args.kwargs['admin'] is True


class TestGenerationsCarryTheirTags:
    """The generation details modal renders and can edit tags."""

    def test_tags_are_loaded_not_left_empty(self, manager, mock_model_repository, mock_admin_user):
        """`Generation.tags` defaults to []. Serializing that for a generation that has
        tags would tell the modal there are none — and a save would then wipe them."""
        mock_model_repository.get_by_id.return_value = Mock(id='model-id')

        generation = Mock(id='gen-1')
        generation.to_dict.return_value = {'id': 'gen-1'}

        tag = Mock()
        with patch('src.features.generation.model_repository.generation_model_repo') as gen_repo, \
             patch('src.features.models.catalog.tag_repo') as mock_tag_repo:
            gen_repo.get_generations_by_model.return_value = ([generation], 1)
            mock_tag_repo.get_generation_tags.return_value = [tag]

            manager.get_model_generations('model-id', mock_admin_user)

        mock_tag_repo.get_generation_tags.assert_called_once_with('gen-1')
        assert generation.tags == [tag]
        assert generation.to_dict.call_args.kwargs == {'include_files': True, 'include_tags': True}
