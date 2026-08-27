"""
Tests for GenerationHistoryQuery.count_generations_by_tags and
GenerationHistoryFacade.bulk_delete_by_tags.

Both methods delegate to tag_repo.get_generations_by_tags (imported inline)
and then operate on the resulting generation-ID list.
"""

import pytest
from unittest.mock import Mock, patch

from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.exceptions import InvalidTagException


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_manager():
    """Return a GenerationHistoryFacade with all deps mocked."""
    mock_repo = Mock()
    mock_file_service = Mock()
    mock_file_service.delete_generation_outputs.return_value = (0, 0)
    mock_plugins = Mock()

    # Default hook execution: not blocked
    mock_context = Mock()
    mock_context.data = {"blocked": False}
    mock_plugins.execute_hook.return_value = (mock_context, [])

    manager = GenerationHistoryFacade(
        generation_repo=mock_repo,
        file_service=mock_file_service,
        plugin_registry=mock_plugins,
    )
    return manager, mock_repo, mock_file_service, mock_plugins


# ---------------------------------------------------------------------------
# count_generations_by_tags
# ---------------------------------------------------------------------------

class TestCountGenerationsByTags:
    """Tests for count_generations_by_tags."""

    def test_count_empty_tag_ids_returns_zero(self):
        """Empty tag_ids list must return 0 without hitting the repository."""
        manager, mock_repo, _, _ = _make_manager()

        result = manager._query.count_generations_by_tags([], 'user-1')

        assert result == 0

    @patch('src.features.tags.repository.tag_repo')
    def test_count_with_matching_generations(self, mock_tag_repo):
        """Returns the count of IDs returned by get_generations_by_tags."""
        manager, _, _, _ = _make_manager()

        # Patch the inline tag_repo validation lookup (used by _validate_tag_ids)
        valid_tag = Mock()
        valid_tag.type = 'GENERATION'
        valid_tag.user_id = 'user-1'
        mock_tag_repo.get_tag_by_id.return_value = valid_tag

        # Patch the inline tag_repo inside count_generations_by_tags
        mock_tag_repo.get_generations_by_tags.return_value = ['gen-a', 'gen-b', 'gen-c']

        result = manager._query.count_generations_by_tags(['tag-1'], 'user-1')

        assert result == 3
        mock_tag_repo.get_generations_by_tags.assert_called_once_with(['tag-1'], 'user-1')

    @patch('src.features.tags.repository.tag_repo')
    def test_count_with_no_matching_generations(self, mock_tag_repo):
        """Returns 0 when get_generations_by_tags returns an empty list."""
        manager, _, _, _ = _make_manager()

        valid_tag = Mock()
        valid_tag.type = 'GENERATION'
        valid_tag.user_id = 'user-1'
        mock_tag_repo.get_tag_by_id.return_value = valid_tag

        mock_tag_repo.get_generations_by_tags.return_value = []

        result = manager._query.count_generations_by_tags(['tag-no-match'], 'user-1')

        assert result == 0

    @patch('src.features.tags.repository.tag_repo')
    def test_count_raises_invalid_tag_exception(self, mock_tag_repo):
        """Propagates InvalidTagException from _validate_tag_ids."""
        manager, _, _, _ = _make_manager()

        # Simulate an invalid tag (wrong type triggers InvalidTagException)
        mock_tag_repo.get_tag_by_id.return_value = None  # not found → invalid

        with pytest.raises(InvalidTagException):
            manager._query.count_generations_by_tags(['bad-tag'], 'user-1')

    @patch('src.features.tags.repository.tag_repo')
    def test_count_multiple_tags_passes_all_to_repo(self, mock_tag_repo):
        """All tag IDs are forwarded to get_generations_by_tags."""
        manager, _, _, _ = _make_manager()

        valid_tag = Mock()
        valid_tag.type = 'GENERATION'
        valid_tag.user_id = 'user-1'
        mock_tag_repo.get_tag_by_id.return_value = valid_tag

        mock_tag_repo.get_generations_by_tags.return_value = ['gen-x']

        manager._query.count_generations_by_tags(['tag-1', 'tag-2'], 'user-1')

        mock_tag_repo.get_generations_by_tags.assert_called_once_with(['tag-1', 'tag-2'], 'user-1')


# ---------------------------------------------------------------------------
# bulk_delete_by_tags
# ---------------------------------------------------------------------------

class TestBulkDeleteByTags:
    """Tests for bulk_delete_by_tags."""

    def test_bulk_delete_empty_tag_ids_returns_zeros(self):
        """Empty tag list short-circuits and returns all-zero result."""
        manager, _, _, _ = _make_manager()

        result = manager.bulk_delete_by_tags([], 'user-1')

        assert result['deleted_count'] == 0
        assert result['failed_count'] == 0
        assert result['failed_ids'] == []
        assert result['total_files_deleted'] == 0

    @patch('src.features.tags.repository.tag_repo')
    def test_bulk_delete_no_matching_generations_skips_delete(self, mock_tag_repo):
        """When get_generations_by_tags returns empty, bulk_delete is not called."""
        manager, mock_repo, _, _ = _make_manager()

        valid_tag = Mock()
        valid_tag.type = 'GENERATION'
        valid_tag.user_id = 'user-1'
        mock_tag_repo.get_tag_by_id.return_value = valid_tag

        mock_tag_repo.get_generations_by_tags.return_value = []

        result = manager.bulk_delete_by_tags(['tag-empty'], 'user-1')

        # DB delete must never be called
        mock_repo.delete.assert_not_called()

        assert result['deleted_count'] == 0
        assert result['failed_count'] == 0

    @patch('src.features.tags.repository.tag_repo')
    def test_bulk_delete_with_matching_generations_calls_bulk_delete(self, mock_tag_repo):
        """When matching IDs exist, bulk_delete is invoked with those IDs."""
        manager, mock_repo, _, _ = _make_manager()

        valid_tag = Mock()
        valid_tag.type = 'GENERATION'
        valid_tag.user_id = 'user-1'
        mock_tag_repo.get_tag_by_id.return_value = valid_tag

        mock_tag_repo.get_generations_by_tags.return_value = ['gen-1', 'gen-2']

        # Make the bulk_delete path succeed for both generations
        mock_gen = Mock()
        mock_repo.get_by_id.return_value = mock_gen
        mock_repo.get_files.return_value = []
        mock_repo.delete.return_value = True

        result = manager.bulk_delete_by_tags(['tag-x'], 'user-1')

        assert result['deleted_count'] == 2
        assert result['failed_count'] == 0
        # delete was called for each generation
        assert mock_repo.delete.call_count == 2

    @patch('src.features.tags.repository.tag_repo')
    def test_bulk_delete_raises_invalid_tag_exception_on_bad_tag(self, mock_tag_repo):
        """Propagates InvalidTagException before hitting the repository."""
        manager, mock_repo, _, _ = _make_manager()

        mock_tag_repo.get_tag_by_id.return_value = None  # invalid tag

        with pytest.raises(InvalidTagException):
            manager.bulk_delete_by_tags(['invalid-tag'], 'user-1')

        mock_repo.delete.assert_not_called()

    @patch('src.features.tags.repository.tag_repo')
    def test_bulk_delete_returns_correct_generation_ids_to_bulk_delete(self, mock_tag_repo):
        """Exactly the IDs from get_generations_by_tags are passed to bulk_delete."""
        manager, mock_repo, _, _ = _make_manager()

        valid_tag = Mock()
        valid_tag.type = 'GENERATION'
        valid_tag.user_id = 'user-1'
        mock_tag_repo.get_tag_by_id.return_value = valid_tag

        expected_ids = ['gen-alpha', 'gen-beta', 'gen-gamma']
        mock_tag_repo.get_generations_by_tags.return_value = expected_ids

        mock_gen = Mock()
        mock_repo.get_by_id.return_value = mock_gen
        mock_repo.get_files.return_value = []
        mock_repo.delete.return_value = True

        result = manager.bulk_delete_by_tags(['tag-full'], 'user-1')

        assert result['deleted_count'] == 3

        # Verify repo.delete was called with each expected id
        deleted_ids = [c.args[0] for c in mock_repo.delete.call_args_list]
        assert set(deleted_ids) == set(expected_ids)
