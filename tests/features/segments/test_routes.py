"""Controller tests for the three clean Segment-library APIs.

The controller calls `src.features.segments.operations` functions directly
(module-level, no injected manager) plus raw repository reads for plain
listings. `mock_operations` patches the `operations` module as imported into
`routes.py`, so tests assert against it exactly like the previous manager
mock, without the controller holding a stateful collaborator it doesn't need.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.segments import routes as routes_module
from src.features.segments.routes import SegmentController, build_router
from src.features.segments.dto import (
    RichSegment,
    SavedSegment,
    SavedSegmentRequest,
    SegmentCategory,
    SegmentCategoryRequest,
    SegmentTemplate,
    SegmentTemplateRequest,
)


@pytest.fixture
def mock_operations(monkeypatch):
    """Patch the `operations` module as seen by routes.py."""
    mock = Mock()
    monkeypatch.setattr(routes_module, "operations", mock)
    return mock


@pytest.fixture
def categories():
    return Mock(name="categories")


@pytest.fixture
def segments():
    return Mock(name="segments")


@pytest.fixture
def templates():
    return Mock(name="templates")


@pytest.fixture
def plugins():
    return Mock(name="plugins")


@pytest.fixture
def controller(categories, segments, templates, plugins):
    return SegmentController(
        category_repository=categories,
        segment_repository=segments,
        template_repository=templates,
        plugin_registry=plugins,
    )


@pytest.fixture
def category():
    return SegmentCategory(
        id="category-1",
        user_id="user-1",
        name="Subjects",
        description="People and creatures",
        color="#123456",
        created_at=datetime.now(),
    )


@pytest.fixture
def saved_segment():
    return SavedSegment(
        id="segment-1",
        user_id="user-1",
        category_id="category-1",
        name="Hero",
        content="a hero",
        effective_color="#123456",
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


def test_router_exposes_clean_resource_prefixes_only(controller):
    router = build_router(SimpleNamespace(segment_controller=controller))
    paths = {route.path for route in router.routes}
    assert "/api/segments" in paths
    assert "/api/segments/{segment_id}" in paths
    assert "/api/segment-templates" in paths
    assert "/api/segment-templates/{template_id}" in paths
    assert "/api/segment-categories" in paths
    assert "/api/segment-categories/{category_id}" in paths
    assert "/api/segments/templates" not in paths
    assert "/api/segments/categories" not in paths


def test_category_list_and_create(controller, categories, mock_operations, category):
    categories.get_all.return_value = [category]
    listed = controller.get_categories("user-1")
    assert listed.success
    assert listed.data["categories"][0]["id"] == category.id
    categories.get_all.assert_called_once_with("user-1")

    request = SegmentCategoryRequest(name="Subjects", color="#123456")
    mock_operations.create_category.return_value = category
    created = controller.create_category(request, "user-1")
    assert created.success
    assert created.data["name"] == "Subjects"
    mock_operations.create_category.assert_called_once_with(categories, controller.plugins, request, "user-1")


def test_category_delete_block_is_returned_as_api_error(controller, mock_operations):
    mock_operations.delete_category.side_effect = ValueError(
        "Cannot delete category with existing saved segments"
    )
    result = controller.delete_category("category-1", "user-1")
    assert not result.success
    assert result.error == "delete_category_failed"
    assert "saved segments" in result.message


def test_saved_segment_crud(controller, categories, segments, mock_operations, saved_segment, category):
    mock_operations.get_category.return_value = category
    segments.get_all.return_value = [saved_segment]
    listed = controller.get_segments("user-1", "category-1")
    assert listed.success
    assert listed.data["segments"][0]["effective_color"] == "#123456"
    segments.get_all.assert_called_once_with("user-1", "category-1")

    mock_operations.get_segment.return_value = saved_segment
    fetched = controller.get_segment_by_id(saved_segment.id, "user-1")
    assert fetched.data["id"] == saved_segment.id

    request = SavedSegmentRequest(
        name="Hero", category_id="category-1", content="a hero"
    )
    mock_operations.create_segment.return_value = saved_segment
    assert controller.create_segment(request, "user-1").success
    mock_operations.update_segment.return_value = saved_segment
    assert controller.update_segment(saved_segment.id, request, "user-1").success
    mock_operations.delete_segment.return_value = True
    deleted = controller.delete_segment(saved_segment.id, "user-1")
    assert deleted.success
    assert deleted.message == "Saved Segment deleted"


def test_saved_segment_error_mapping(controller, mock_operations):
    mock_operations.get_segment.side_effect = ValueError("Saved Segment not found")
    result = controller.get_segment_by_id("missing", "user-1")
    assert not result.success
    assert result.error == "get_segment_failed"


def test_template_crud_uses_aggregate_contract(controller, templates, mock_operations, template):
    templates.get_all.return_value = [template]
    listed = controller.get_templates("user-1")
    assert listed.success
    assert [
        item["type"] for item in listed.data["templates"][0]["segments"]
    ] == ["content", "break"]

    request = SegmentTemplateRequest(
        name="Sequence",
        segments=[RichSegment(content="opening"), RichSegment(type="break")],
    )
    mock_operations.create_template.return_value = template
    assert controller.create_template(request, "user-1").success
    mock_operations.update_template.return_value = template
    assert controller.update_template(template.id, request, "user-1").success
    mock_operations.delete_template.return_value = True
    deleted = controller.delete_template(template.id, "user-1")
    assert deleted.success
    assert deleted.message == "Segment Template deleted"


def test_template_error_mapping(controller, mock_operations, template):
    mock_operations.update_template.side_effect = ValueError(
        "Segment Template with this name already exists"
    )
    request = SegmentTemplateRequest(
        name="Sequence", segments=[RichSegment(content="replacement")]
    )
    result = controller.update_template(template.id, request, "user-1")
    assert not result.success
    assert result.error == "update_template_failed"
