import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.segments_tool import (
    CreateSavedSegmentTool,
    CreateSegmentCategoryTool,
    CreateSegmentTemplateTool,
    DeleteSavedSegmentTool,
    DeleteSegmentCategoryTool,
    DeleteSegmentTemplateTool,
    UpdateSavedSegmentTool,
    UpdateSegmentCategoryTool,
    UpdateSegmentTemplateTool,
)
from src.features.segments.repository import (
    SavedSegmentRepository,
    SegmentCategoryRepository,
    SegmentTemplateRepository,
)
from tests.conftest import TestDatabase


class _Registry:
    def execute_hook(self, hook, initial_data=None):
        return SimpleNamespace(data=dict(initial_data or {})), None


@pytest.fixture
def scratch_db():
    test_database = TestDatabase.from_template()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database):
        with test_database.get_cursor() as cursor:
            for user_id in ("owner", "other"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
                    (user_id, user_id, f"{user_id}@example.test"),
                )
        yield test_database
    test_database.close()


def _context(user_id):
    return ToolContext(
        user_id=user_id,
        saved_segment_repository=SavedSegmentRepository(),
        segment_template_repository=SegmentTemplateRepository(),
        segment_category_repository=SegmentCategoryRepository(),
        plugin_registry=_Registry(),
    )


@pytest.fixture
def owner(scratch_db):
    return _context("owner")


@pytest.fixture
def other(scratch_db):
    return _context("other")


async def _category(owner_ctx, name="Moods"):
    result = await CreateSegmentCategoryTool().execute_confirmed(owner_ctx, name=name, description="d", color="#112233")
    assert result.success, result.error
    return json.loads(result.data)


async def _segment(owner_ctx, category_id, **extra):
    fields = {"name": "Golden hour", "category_id": category_id, "content": "warm light", "tags": ["lit"], "prefix": "pre"}
    fields.update(extra)
    result = await CreateSavedSegmentTool().execute_confirmed(owner_ctx, **fields)
    assert result.success, result.error
    return json.loads(result.data)


async def _template(owner_ctx, name="Portrait"):
    result = await CreateSegmentTemplateTool().execute_confirmed(
        owner_ctx, name=name, description="d", tags=["t"],
        segments=[{"content": "a"}, {"content": "b", "prefix": "x"}],
    )
    assert result.success, result.error
    return json.loads(result.data)


@pytest.mark.asyncio
async def test_category_lifecycle_without_preview(owner):
    created = await _category(owner)
    assert set(created) == {"id", "name", "description", "color"}
    assert created["color"] == "#112233"

    updated = await UpdateSegmentCategoryTool().execute_confirmed(owner, category_id=created["id"], name="Feelings")
    assert json.loads(updated.data) == {**created, "name": "Feelings"}

    deleted = await DeleteSegmentCategoryTool().execute_confirmed(owner, category_id=created["id"])
    assert deleted.success
    assert owner.segment_category_repository.get_by_id(created["id"], "owner") is None


@pytest.mark.asyncio
async def test_category_with_segments_is_refused_plainly(owner):
    category = await _category(owner)
    await _segment(owner, category["id"])
    result = await DeleteSegmentCategoryTool().execute_confirmed(owner, category_id=category["id"])
    assert not result.success
    assert result.error == "Cannot delete category with existing saved segments"
    assert owner.segment_category_repository.get_by_id(category["id"], "owner") is not None


@pytest.mark.asyncio
async def test_saved_segment_lifecycle_and_partial_update(owner):
    category = await _category(owner)
    created = await _segment(owner, category["id"])
    assert created["content"] == "warm light"

    result = await UpdateSavedSegmentTool().execute_confirmed(owner, segment_id=created["id"], content="cold light")
    assert result.success, result.error
    updated = json.loads(result.data)
    assert updated["content"] == "cold light"
    assert updated["name"] == "Golden hour"
    assert updated["prefix"] == "pre"
    assert updated["tags"] == ["lit"]
    assert updated["category_id"] == category["id"]

    deleted = await DeleteSavedSegmentTool().execute_confirmed(owner, segment_id=created["id"])
    assert deleted.success
    assert owner.saved_segment_repository.get_by_id(created["id"], "owner") is None


@pytest.mark.asyncio
async def test_saved_segment_read_shape_round_trips_into_update(owner):
    category = await _category(owner)
    created = await _segment(owner, category["id"])
    result = await UpdateSavedSegmentTool().execute_confirmed(owner, segment_id=created["id"], **{
        key: created[key] for key in ("name", "category_id", "content", "chips", "resources", "enabled", "color",
                                      "description", "prefix", "suffix", "tags")
    })
    assert result.success, result.error


@pytest.mark.asyncio
async def test_saved_segment_create_requires_existing_owned_category(owner, other):
    category = await _category(owner)
    result = await CreateSavedSegmentTool().execute_confirmed(other, name="x", category_id=category["id"])
    assert not result.success
    assert result.error == "Category not found"
    assert other.saved_segment_repository.get_all("other") == []


@pytest.mark.asyncio
async def test_template_lifecycle_and_partial_update(owner):
    created = await _template(owner)
    assert [segment["content"] for segment in created["segments"]] == ["a", "b"]

    result = await UpdateSegmentTemplateTool().execute_confirmed(owner, template_id=created["id"], description="new")
    assert result.success, result.error
    updated = json.loads(result.data)
    assert updated["description"] == "new"
    assert updated["name"] == "Portrait"
    assert updated["tags"] == ["t"]
    assert [segment["content"] for segment in updated["segments"]] == ["a", "b"]
    assert updated["segments"][1]["prefix"] == "x"

    deleted = await DeleteSegmentTemplateTool().execute_confirmed(owner, template_id=created["id"])
    assert deleted.success
    assert owner.segment_template_repository.get_by_id(created["id"], "owner") is None


@pytest.mark.asyncio
async def test_other_users_records_are_not_found_and_not_modified(owner, other):
    category = await _category(owner)
    segment = await _segment(owner, category["id"])
    template = await _template(owner)

    attempts = [
        (UpdateSegmentCategoryTool(), {"category_id": category["id"], "name": "hijack"}),
        (DeleteSegmentCategoryTool(), {"category_id": category["id"]}),
        (UpdateSavedSegmentTool(), {"segment_id": segment["id"], "content": "hijack"}),
        (DeleteSavedSegmentTool(), {"segment_id": segment["id"]}),
        (UpdateSegmentTemplateTool(), {"template_id": template["id"], "name": "hijack"}),
        (DeleteSegmentTemplateTool(), {"template_id": template["id"]}),
    ]
    for tool, kwargs in attempts:
        confirmed = await tool.execute_confirmed(other, **kwargs)
        assert not confirmed.success, tool.name
        assert "not found" in confirmed.error, tool.name
        previewed = await tool.execute(other, **kwargs)
        assert not previewed.success, tool.name

    assert owner.segment_category_repository.get_by_id(category["id"], "owner").name == "Moods"
    assert owner.saved_segment_repository.get_by_id(segment["id"], "owner").content == "warm light"
    assert owner.segment_template_repository.get_by_id(template["id"], "owner").name == "Portrait"


@pytest.mark.asyncio
async def test_preview_changes_nothing(owner):
    category = await _category(owner)
    segment = await _segment(owner, category["id"])
    template = await _template(owner)

    previews = [
        (CreateSegmentCategoryTool(), {"name": "New"}),
        (UpdateSegmentCategoryTool(), {"category_id": category["id"], "name": "Renamed"}),
        (CreateSavedSegmentTool(), {"name": "Fresh", "category_id": category["id"], "content": "z"}),
        (UpdateSavedSegmentTool(), {"segment_id": segment["id"], "content": "changed"}),
        (DeleteSavedSegmentTool(), {"segment_id": segment["id"]}),
        (CreateSegmentTemplateTool(), {"name": "Fresh", "segments": [{"content": "q"}]}),
        (UpdateSegmentTemplateTool(), {"template_id": template["id"], "name": "Renamed"}),
        (DeleteSegmentTemplateTool(), {"template_id": template["id"]}),
        (DeleteSegmentCategoryTool(), {"category_id": category["id"]}),
    ]
    for tool, kwargs in previews:
        result = await tool.execute(owner, **kwargs)
        assert result.success, (tool.name, result.error)
        assert result.preview is not None
        assert json.loads(result.data)["action"] == tool.name

    names = {c.name for c in owner.segment_category_repository.get_all("owner")}
    assert "New" not in names and "Renamed" not in names and "Moods" in names
    assert [s.name for s in owner.saved_segment_repository.get_all("owner")] == ["Golden hour"]
    assert owner.saved_segment_repository.get_by_id(segment["id"], "owner").content == "warm light"
    assert [t.name for t in owner.segment_template_repository.get_all("owner")] == ["Portrait"]


@pytest.mark.asyncio
async def test_confirmed_revalidates_required_fields(owner):
    missing = await CreateSavedSegmentTool().execute_confirmed(owner, content="x")
    assert not missing.success
    empty_template = await CreateSegmentTemplateTool().execute_confirmed(owner, name="t", segments=[])
    assert not empty_template.success
    no_id = await DeleteSavedSegmentTool().execute_confirmed(owner)
    assert no_id.error == "segment_id is required"


@pytest.mark.asyncio
async def test_duplicate_name_is_a_plain_error(owner):
    await _category(owner, "Dup")
    result = await CreateSegmentCategoryTool().execute_confirmed(owner, name="Dup")
    assert not result.success
    assert "already exists" in result.error
