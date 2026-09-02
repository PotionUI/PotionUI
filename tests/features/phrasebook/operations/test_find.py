"""Tests for the phrasebook find_phrasebook operation."""
from datetime import datetime
from unittest.mock import Mock

import pytest

from src.features.phrasebook import operations
from src.features.phrasebook.dto import PhrasebookCategory
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)


@pytest.fixture
def category_repository():
    return Mock(spec=PhrasebookCategoryRepository)


@pytest.fixture
def value_repository():
    return Mock(spec=PhrasebookValueRepository)


@pytest.fixture
def sample_category():
    return PhrasebookCategory(
        id="cat-1", name="Dogs", path="animals.dogs", parent_id=None,
        description="", is_active=False, user_id="user-1",
        created_at=datetime.now(), updated_at=datetime.now(),
    )


def test_blank_query_returns_empty_without_touching_repositories(category_repository, value_repository):
    result = operations.find_phrasebook(category_repository, value_repository, "user-1", "   ")

    assert result == {
        "query": "",
        "categories": [],
        "values": [],
        "total_categories": 0,
        "total_values": 0,
    }
    category_repository.find_by_text.assert_not_called()
    value_repository.find_by_text.assert_not_called()


def test_trims_query_and_returns_both_kinds_with_counts(category_repository, value_repository, sample_category):
    category_repository.find_by_text.return_value = [sample_category]
    value_hit = {"id": "val-1", "label": "Puppy", "value": "small dog", "category_id": "cat-1",
                 "category_path": "animals.dogs", "category_name": "Dogs", "is_active": True}
    value_repository.find_by_text.return_value = [value_hit]

    result = operations.find_phrasebook(category_repository, value_repository, "user-1", "  dog ", limit_per_kind=7)

    category_repository.find_by_text.assert_called_once_with("user-1", "dog", 7)
    value_repository.find_by_text.assert_called_once_with("user-1", "dog", 7)
    assert result["query"] == "dog"
    assert result["categories"][0]["id"] == "cat-1"
    assert result["categories"][0]["is_active"] is False
    assert result["values"] == [value_hit]
    assert result["total_categories"] == 1
    assert result["total_values"] == 1


def test_limit_floor_is_one(category_repository, value_repository):
    category_repository.find_by_text.return_value = []
    value_repository.find_by_text.return_value = []

    operations.find_phrasebook(category_repository, value_repository, "user-1", "dog", limit_per_kind=0)

    category_repository.find_by_text.assert_called_once_with("user-1", "dog", 1)
