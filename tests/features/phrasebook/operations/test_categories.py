"""Tests for phrasebook Category operations."""
import pytest
from datetime import datetime
from unittest.mock import Mock

from src.features.phrasebook import operations
from src.features.phrasebook.dto import PhrasebookCategory
from src.features.phrasebook.repository import PhrasebookCategoryRepository


@pytest.fixture
def mock_category_repository():
    return Mock(spec=PhrasebookCategoryRepository)


@pytest.fixture
def sample_category():
    return PhrasebookCategory(
        id="cat-123",
        name="Test Category",
        path="test.category",
        parent_id=None,
        description="Test description",
        is_active=True,
        user_id="user-123",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )


class TestToggleCategoryActive:
    def test_deactivate(self, mock_category_repository, sample_category):
        mock_category_repository.get_by_id.return_value = sample_category
        mock_category_repository.update_active_state.return_value = True

        deactivated = PhrasebookCategory(
            id=sample_category.id,
            name=sample_category.name,
            path=sample_category.path,
            parent_id=sample_category.parent_id,
            description=sample_category.description,
            is_active=False,
            user_id=sample_category.user_id,
            created_at=sample_category.created_at,
            updated_at=datetime.utcnow()
        )
        mock_category_repository.get_by_id.side_effect = [sample_category, deactivated]

        result = operations.toggle_category_active(mock_category_repository, "cat-123", False, "user-123")

        mock_category_repository.update_active_state.assert_called_once_with(
            "cat-123", "user-123", False
        )
        assert result.is_active is False

    def test_activate(self, mock_category_repository, sample_category):
        inactive_category = PhrasebookCategory(
            id=sample_category.id,
            name=sample_category.name,
            path=sample_category.path,
            parent_id=sample_category.parent_id,
            description=sample_category.description,
            is_active=False,
            user_id=sample_category.user_id,
            created_at=sample_category.created_at,
            updated_at=datetime.utcnow()
        )
        mock_category_repository.get_by_id.return_value = inactive_category
        mock_category_repository.update_active_state.return_value = True
        mock_category_repository.get_by_id.side_effect = [inactive_category, sample_category]

        result = operations.toggle_category_active(mock_category_repository, "cat-123", True, "user-123")

        mock_category_repository.update_active_state.assert_called_once_with(
            "cat-123", "user-123", True
        )
        assert result.is_active is True

    def test_not_found(self, mock_category_repository):
        mock_category_repository.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            operations.toggle_category_active(mock_category_repository, "nonexistent", True, "user-123")

        assert "Category not found" in str(exc_info.value)

    def test_update_fails(self, mock_category_repository, sample_category):
        mock_category_repository.get_by_id.return_value = sample_category
        mock_category_repository.update_active_state.return_value = False

        with pytest.raises(ValueError) as exc_info:
            operations.toggle_category_active(mock_category_repository, "cat-123", False, "user-123")

        assert "Failed to update category active state" in str(exc_info.value)
