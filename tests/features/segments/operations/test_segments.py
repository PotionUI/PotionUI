"""Business-logic tests for saved Segment operations."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from src.features.segments import operations
from src.features.segments.dto import (
    SavedSegment,
    SavedSegmentRequest,
    SegmentCategory,
)


@pytest.fixture
def categories():
    return Mock(name="categories")


@pytest.fixture
def segments():
    return Mock(name="segments")


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


@pytest.fixture
def saved_segment(category):
    return SavedSegment(
        id="segment-1",
        user_id="user-1",
        category_id=category.id,
        name="Hero",
        content="a hero",
        effective_color=category.color,
    )


def test_saved_segment_create_uses_effective_category_color(categories, segments, plugins, category):
    categories.get_by_id.return_value = category
    segments.get_by_name.return_value = None
    segments.create.side_effect = lambda item: item
    request = SavedSegmentRequest(
        name="Hero",
        category_id=category.id,
        content="a hero",
        enabled=False,
        tags=["subject"],
    )

    created = operations.create_segment(segments, categories, plugins, request, "user-1")
    assert created.effective_color == category.color
    assert created.color is None
    assert created.enabled is False
    assert created.tags == ["subject"]


def test_saved_segment_override_color_wins(categories, segments, plugins, category):
    categories.get_by_id.return_value = category
    segments.get_by_name.return_value = None
    segments.create.side_effect = lambda item: item

    created = operations.create_segment(
        segments, categories, plugins,
        SavedSegmentRequest(
            name="Hero",
            category_id=category.id,
            color="#fedcba",
        ),
        "user-1",
    )
    assert created.color == "#fedcba"
    assert created.effective_color == "#fedcba"


def test_saved_segment_rejects_another_users_category(categories, segments, plugins):
    categories.get_by_id.return_value = None

    with pytest.raises(ValueError, match="Category not found"):
        operations.create_segment(
            segments, categories, plugins,
            SavedSegmentRequest(name="Hero", category_id="foreign-category"),
            "user-1",
        )
    segments.create.assert_not_called()


def test_saved_segment_update_replaces_complete_rich_state(categories, segments, plugins, category, saved_segment):
    categories.get_by_id.return_value = category
    segments.get_by_id.return_value = saved_segment
    segments.get_by_name.return_value = saved_segment
    segments.update.side_effect = lambda _id, item, _user: item
    request = SavedSegmentRequest(
        name="Hero",
        category_id=category.id,
        type="break",
        content="",
        enabled=False,
        description="pause",
    )

    updated = operations.update_segment(segments, categories, plugins, saved_segment.id, request, "user-1")
    assert updated.type == "break"
    assert updated.enabled is False
    assert updated.description == "pause"
    segments.update.assert_called_once()


def test_before_hooks_can_block_saved_segments(categories, segments, plugins, category):
    categories.get_by_id.return_value = category
    blocked = Mock()
    blocked.data = {"blocked": True, "block_reason": "policy says no"}
    plugins.execute_hook.return_value = (blocked, [])

    with pytest.raises(ValueError, match="policy says no"):
        operations.create_segment(
            segments, categories, plugins,
            SavedSegmentRequest(name="Hero", category_id=category.id), "user-1",
        )
    segments.create.assert_not_called()
