"""Tests for the phrasebook search_phrasebook operation."""
import pytest
from datetime import datetime
from unittest.mock import Mock

from src.features.phrasebook import operations
from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookStateFilter
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)


@pytest.fixture
def mock_category_repository():
    return Mock(spec=PhrasebookCategoryRepository)


@pytest.fixture
def mock_value_repository():
    return Mock(spec=PhrasebookValueRepository)


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


@pytest.fixture
def sample_value():
    from src.features.phrasebook.dto import PhrasebookValue
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


def test_search_phrasebook_with_state_filter(mock_category_repository, mock_value_repository, sample_category, sample_value):
    mock_category_repository.get_by_path.return_value = sample_category
    mock_category_repository.get_children.return_value = []
    mock_value_repository.get_by_category.return_value = [sample_value]

    result = operations.search_phrasebook(
        mock_category_repository, mock_value_repository,
        "test.category", "user-123", limit=50, state_filter=PhrasebookStateFilter.ACTIVE,
    )

    mock_value_repository.get_by_category.assert_called_once_with(
        sample_category.id, "user-123", PhrasebookStateFilter.ACTIVE
    )
    assert result["current_category"] is not None
    assert len(result["values"]) == 1


def test_search_phrasebook_filters_inactive_exact_category(mock_category_repository, mock_value_repository, sample_category):
    inactive_category = PhrasebookCategory(
        id=sample_category.id,
        name=sample_category.name,
        path=sample_category.path,
        parent_id=sample_category.parent_id,
        description=sample_category.description,
        is_active=False,
        user_id=sample_category.user_id,
        created_at=sample_category.created_at,
        updated_at=sample_category.updated_at
    )
    mock_category_repository.get_by_path.return_value = inactive_category
    mock_category_repository.search_by_path_prefix.return_value = []
    mock_value_repository.search_by_path_prefix.return_value = []

    result = operations.search_phrasebook(
        mock_category_repository, mock_value_repository,
        "test.category", "user-123", state_filter=PhrasebookStateFilter.ACTIVE,
    )

    # When the exact category is inactive with ACTIVE filter, it should be treated as no match
    assert result["current_category"] is None


def test_search_phrasebook_filters_inactive_children(mock_category_repository, mock_value_repository, sample_category):
    mock_category_repository.get_by_path.return_value = sample_category

    active_child = PhrasebookCategory(
        id="child-active",
        name="Active Child",
        path="test.category.active",
        parent_id=sample_category.id,
        description="Active",
        is_active=True,
        user_id="user-123",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    inactive_child = PhrasebookCategory(
        id="child-inactive",
        name="Inactive Child",
        path="test.category.inactive",
        parent_id=sample_category.id,
        description="Inactive",
        is_active=False,
        user_id="user-123",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    mock_category_repository.get_children.return_value = [active_child, inactive_child]
    mock_value_repository.get_by_category.return_value = []

    result = operations.search_phrasebook(
        mock_category_repository, mock_value_repository,
        "test.category", "user-123", state_filter=PhrasebookStateFilter.ACTIVE,
    )

    # Only active child should be in results
    assert len(result["child_categories"]) == 1
    assert result["child_categories"][0]["id"] == "child-active"


def test_search_phrasebook_default_state_filter(mock_category_repository, mock_value_repository, sample_category, sample_value):
    mock_category_repository.get_by_path.return_value = sample_category
    mock_category_repository.get_children.return_value = []
    mock_value_repository.get_by_category.return_value = [sample_value]

    operations.search_phrasebook(mock_category_repository, mock_value_repository, "test.category", "user-123")

    # Default should be ACTIVE
    mock_value_repository.get_by_category.assert_called_once_with(
        sample_category.id, "user-123", PhrasebookStateFilter.ACTIVE
    )
