"""Business-logic tests for Segment Category operations."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from src.features.segments import operations
from src.features.segments.dto import SegmentCategory, SegmentCategoryRequest
from src.features.segments.operations.reads import get_category


@pytest.fixture
def categories():
    return Mock(name="categories")


@pytest.fixture
def plugins():
    registry = Mock()
    context = Mock()
    context.data = {}
    registry.execute_hook.return_value = (context, [])
    return registry


@pytest.fixture
def category():
    return SegmentCategory(
        id="category-1",
        user_id="user-1",
        name="People",
        description="Subjects",
        color="#102030",
        created_at=datetime.now(),
    )


def test_get_category_is_strictly_user_scoped(categories, category):
    categories.get_by_id.return_value = category

    assert get_category(categories, category.id, "user-1") == category
    categories.get_by_id.assert_called_once_with(category.id, "user-1")

    categories.get_by_id.return_value = None
    with pytest.raises(ValueError, match="Category not found"):
        get_category(categories, category.id, "other-user")


def test_create_category_enforces_per_user_name_uniqueness(categories, plugins, category):
    request = SegmentCategoryRequest(name="People", color="#abcdef")
    categories.get_by_name.return_value = None
    categories.create.side_effect = lambda item: item

    created = operations.create_category(categories, plugins, request, "user-1")
    assert created.name == "People"
    assert created.user_id == "user-1"

    categories.get_by_name.return_value = category
    with pytest.raises(ValueError, match="already exists"):
        operations.create_category(categories, plugins, request, "user-1")


def test_delete_category_is_blocked_while_saved_segments_reference_it(categories, plugins, category):
    categories.get_by_id.return_value = category
    categories.has_saved_segments.return_value = True

    with pytest.raises(ValueError, match="existing saved segments"):
        operations.delete_category(categories, plugins, category.id, "user-1")
    categories.delete.assert_not_called()
