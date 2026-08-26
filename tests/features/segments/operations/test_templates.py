"""Business-logic tests for Segment Template operations."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from src.features.segments import operations
from src.features.segments.dto import RichSegment, SegmentTemplate, SegmentTemplateRequest


@pytest.fixture
def templates():
    return Mock(name="templates")


@pytest.fixture
def plugins():
    registry = Mock()
    context = Mock()
    context.data = {}
    registry.execute_hook.return_value = (context, [])
    return registry


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


def test_template_create_passes_complete_ordered_aggregate(templates, plugins):
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

    created = operations.create_template(templates, plugins, request, "user-1")
    assert [item.name for item in created.segments] == ["A", "B"]
    assert created.segments[1].type == "break"
    assert created.segments[1].enabled is False


def test_template_update_is_a_full_child_replacement(templates, plugins, template):
    templates.get_by_id.return_value = template
    templates.get_by_name.return_value = template
    templates.update.side_effect = lambda _id, item, _user: item
    request = SegmentTemplateRequest(
        name="Sequence",
        segments=[RichSegment(content="only replacement")],
    )

    updated = operations.update_template(templates, plugins, template.id, request, "user-1")
    assert [item.content for item in updated.segments] == ["only replacement"]
    passed = templates.update.call_args.args[1]
    assert len(passed.segments) == 1


def test_before_hooks_can_block_templates(templates, plugins):
    blocked = Mock()
    blocked.data = {"blocked": True, "block_reason": "policy says no"}
    plugins.execute_hook.return_value = (blocked, [])

    with pytest.raises(ValueError, match="policy says no"):
        operations.create_template(
            templates, plugins,
            SegmentTemplateRequest(name="Sequence", segments=[RichSegment(content="x")]),
            "user-1",
        )
    templates.create.assert_not_called()
