"""Business-logic tests for the reset Segment library domain."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from src.features.segments.dto import (
    RichSegment,
    SavedSegment,
    SavedSegmentRequest,
    SegmentCategory,
    SegmentCategoryRequest,
    SegmentTemplate,
    SegmentTemplateRequest,
)
from src.features.segments.manager import SegmentManager


@pytest.fixture
def repos():
    return Mock(name="categories"), Mock(name="segments"), Mock(name="templates")


@pytest.fixture
def plugins():
    registry = Mock()
    context = Mock()
    context.data = {}
    registry.execute_hook.return_value = (context, [])
    return registry


@pytest.fixture
def manager(repos, plugins):
    categories, segments, templates = repos
    return SegmentManager(
        category_repository=categories,
        saved_segment_repository=segments,
        template_repository=templates,
        plugin_registry=plugins,
    )


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


@pytest.fixture
def template():
    return SegmentTemplate(
        id="template-1",
        user_id="user-1",
        name="Sequence",
        segments=[RichSegment(content="opening"), RichSegment(type="break")],
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def test_get_category_is_strictly_user_scoped(manager, repos, category):
    categories, _, _ = repos
    categories.get_by_id.return_value = category

    assert manager.get_category_by_id(category.id, "user-1") == category
    categories.get_by_id.assert_called_once_with(category.id, "user-1")

    categories.get_by_id.return_value = None
    with pytest.raises(ValueError, match="Category not found"):
        manager.get_category_by_id(category.id, "other-user")


def test_create_category_enforces_per_user_name_uniqueness(manager, repos, category):
    categories, _, _ = repos
    request = SegmentCategoryRequest(name="People", color="#abcdef")
    categories.get_by_name.return_value = None
    categories.create.side_effect = lambda item: item

    created = manager.create_category(request, "user-1")
    assert created.name == "People"
    assert created.user_id == "user-1"

    categories.get_by_name.return_value = category
    with pytest.raises(ValueError, match="already exists"):
        manager.create_category(request, "user-1")


def test_delete_category_is_blocked_while_saved_segments_reference_it(
    manager, repos, category
):
    categories, _, _ = repos
    categories.get_by_id.return_value = category
    categories.has_saved_segments.return_value = True

    with pytest.raises(ValueError, match="existing saved segments"):
        manager.delete_category(category.id, "user-1")
    categories.delete.assert_not_called()


def test_saved_segment_create_uses_effective_category_color(
    manager, repos, category
):
    categories, segments, _ = repos
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

    created = manager.create_segment(request, "user-1")
    assert created.effective_color == category.color
    assert created.color is None
    assert created.enabled is False
    assert created.tags == ["subject"]


def test_saved_segment_override_color_wins(manager, repos, category):
    categories, segments, _ = repos
    categories.get_by_id.return_value = category
    segments.get_by_name.return_value = None
    segments.create.side_effect = lambda item: item

    created = manager.create_segment(
        SavedSegmentRequest(
            name="Hero",
            category_id=category.id,
            color="#fedcba",
        ),
        "user-1",
    )
    assert created.color == "#fedcba"
    assert created.effective_color == "#fedcba"


def test_saved_segment_rejects_another_users_category(manager, repos):
    categories, segments, _ = repos
    categories.get_by_id.return_value = None

    with pytest.raises(ValueError, match="Category not found"):
        manager.create_segment(
            SavedSegmentRequest(name="Hero", category_id="foreign-category"),
            "user-1",
        )
    segments.create.assert_not_called()


def test_saved_segment_update_replaces_complete_rich_state(
    manager, repos, category, saved_segment
):
    categories, segments, _ = repos
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

    updated = manager.update_segment(saved_segment.id, request, "user-1")
    assert updated.type == "break"
    assert updated.enabled is False
    assert updated.description == "pause"
    segments.update.assert_called_once()


def test_template_create_passes_complete_ordered_aggregate(manager, repos):
    _, _, templates = repos
    templates.get_by_name.return_value = None
    templates.create.side_effect = lambda item: item
    request = SegmentTemplateRequest(
        name="Sequence",
        tags=["video"],
        segments=[
            RichSegment(content="opening", name="A"),
            RichSegment(type="break", enabled=False, name="B"),
        ],
    )

    created = manager.create_template(request, "user-1")
    assert [item.name for item in created.segments] == ["A", "B"]
    assert created.segments[1].type == "break"
    assert created.segments[1].enabled is False


def test_template_update_is_a_full_child_replacement(manager, repos, template):
    _, _, templates = repos
    templates.get_by_id.return_value = template
    templates.get_by_name.return_value = template
    templates.update.side_effect = lambda _id, item, _user: item
    request = SegmentTemplateRequest(
        name="Sequence",
        segments=[RichSegment(content="only replacement")],
    )

    updated = manager.update_template(template.id, request, "user-1")
    assert [item.content for item in updated.segments] == ["only replacement"]
    passed = templates.update.call_args.args[1]
    assert len(passed.segments) == 1


def test_before_hooks_can_block_saved_segments_and_templates(
    manager, plugins, repos, category
):
    categories, segments, templates = repos
    categories.get_by_id.return_value = category
    blocked = Mock()
    blocked.data = {"blocked": True, "block_reason": "policy says no"}
    plugins.execute_hook.return_value = (blocked, [])

    with pytest.raises(ValueError, match="policy says no"):
        manager.create_segment(
            SavedSegmentRequest(name="Hero", category_id=category.id), "user-1"
        )
    with pytest.raises(ValueError, match="policy says no"):
        manager.create_template(
            SegmentTemplateRequest(
                name="Sequence", segments=[RichSegment(content="x")]
            ),
            "user-1",
        )
    segments.create.assert_not_called()
    templates.create.assert_not_called()
