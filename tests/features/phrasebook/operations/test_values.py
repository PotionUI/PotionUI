"""Tests for phrasebook Value operations."""
import pytest
from datetime import datetime
from unittest.mock import Mock

from src.features.phrasebook import operations
from src.features.phrasebook.dto import PhrasebookValue
from src.features.phrasebook.repository import PhrasebookValueRepository


@pytest.fixture
def mock_value_repository():
    return Mock(spec=PhrasebookValueRepository)


@pytest.fixture
def sample_value():
    return PhrasebookValue(
        id="val-123",
        category_id="cat-123",
        label="Test Value",
        value="test value content",
        sort_order=0,
        is_active=True,
        preview_file_id=None,
        preview_generation_id=None,
        user_id="user-123",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )


class TestToggleValueActive:
    def test_deactivate(self, mock_value_repository, sample_value):
        mock_value_repository.get_by_id.return_value = sample_value
        mock_value_repository.update_active_state.return_value = True

        deactivated = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=False,
            preview_file_id=sample_value.preview_file_id,
            preview_generation_id=sample_value.preview_generation_id,
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=datetime.utcnow()
        )
        mock_value_repository.get_by_id.side_effect = [sample_value, deactivated]

        result = operations.toggle_value_active(mock_value_repository, "val-123", False, "user-123")

        mock_value_repository.update_active_state.assert_called_once_with(
            "val-123", "user-123", False
        )
        assert result.is_active is False

    def test_activate(self, mock_value_repository, sample_value):
        inactive_value = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=False,
            preview_file_id=sample_value.preview_file_id,
            preview_generation_id=sample_value.preview_generation_id,
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=datetime.utcnow()
        )
        mock_value_repository.get_by_id.return_value = inactive_value
        mock_value_repository.update_active_state.return_value = True
        mock_value_repository.get_by_id.side_effect = [inactive_value, sample_value]

        result = operations.toggle_value_active(mock_value_repository, "val-123", True, "user-123")

        mock_value_repository.update_active_state.assert_called_once_with(
            "val-123", "user-123", True
        )
        assert result.is_active is True

    def test_not_found(self, mock_value_repository):
        mock_value_repository.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            operations.toggle_value_active(mock_value_repository, "nonexistent", True, "user-123")

        assert "Value not found" in str(exc_info.value)

    def test_update_fails(self, mock_value_repository, sample_value):
        mock_value_repository.get_by_id.return_value = sample_value
        mock_value_repository.update_active_state.return_value = False

        with pytest.raises(ValueError) as exc_info:
            operations.toggle_value_active(mock_value_repository, "val-123", False, "user-123")

        assert "Failed to update value active state" in str(exc_info.value)


class TestAttachPreviewImage:
    def test_attach(self, mock_value_repository, sample_value):
        mock_value_repository.get_by_id.return_value = sample_value
        mock_value_repository.update_preview_file.return_value = True

        updated = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=sample_value.is_active,
            preview_file_id="file-123",
            preview_generation_id="gen-123",
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=datetime.utcnow()
        )
        mock_value_repository.get_by_id.side_effect = [sample_value, updated]

        result = operations.attach_preview_image(
            mock_value_repository, "val-123", "user-123", "file-123", "gen-123"
        )

        mock_value_repository.update_preview_file.assert_called_once_with(
            "val-123", "user-123", "file-123", "gen-123"
        )
        assert result.preview_file_id == "file-123"
        assert result.preview_generation_id == "gen-123"

    def test_clear(self, mock_value_repository, sample_value):
        with_preview = PhrasebookValue(
            id=sample_value.id,
            category_id=sample_value.category_id,
            label=sample_value.label,
            value=sample_value.value,
            sort_order=sample_value.sort_order,
            is_active=sample_value.is_active,
            preview_file_id="file-123",
            preview_generation_id="gen-123",
            user_id=sample_value.user_id,
            created_at=sample_value.created_at,
            updated_at=datetime.utcnow()
        )
        mock_value_repository.get_by_id.return_value = with_preview
        mock_value_repository.update_preview_file.return_value = True
        mock_value_repository.get_by_id.side_effect = [with_preview, sample_value]

        result = operations.attach_preview_image(mock_value_repository, "val-123", "user-123", None, None)

        mock_value_repository.update_preview_file.assert_called_once_with(
            "val-123", "user-123", None, None
        )
        assert result.preview_file_id is None
        assert result.preview_generation_id is None

    def test_not_found(self, mock_value_repository):
        mock_value_repository.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            operations.attach_preview_image(mock_value_repository, "nonexistent", "user-123", "file-123", "gen-123")

        assert "Value not found" in str(exc_info.value)

    def test_update_fails(self, mock_value_repository, sample_value):
        mock_value_repository.get_by_id.return_value = sample_value
        mock_value_repository.update_preview_file.return_value = False

        with pytest.raises(ValueError) as exc_info:
            operations.attach_preview_image(mock_value_repository, "val-123", "user-123", "file-123", "gen-123")

        assert "Failed to update value preview image" in str(exc_info.value)
