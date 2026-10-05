import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.phrasebook_tool import (
    DeletePhrasebookCategoryTool,
    UpdatePhrasebookCategoryTool,
)
from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookValue
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
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
            for user_id in ("alice", "bob"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, 'x')",
                    (user_id, user_id, f"{user_id}@example.com"),
                )
        yield test_database
    test_database.close()


@pytest.fixture
def repos(scratch_db):
    return PhrasebookCategoryRepository(), PhrasebookValueRepository()


def _context(repos, user_id="alice"):
    return ToolContext(
        user_id=user_id,
        phrasebook_category_repository=repos[0],
        phrasebook_value_repository=repos[1],
        plugin_registry=_Registry(),
    )


def _category(repos, cid, path, user_id="alice", parent_id=None, description="old"):
    assert repos[0].create(PhrasebookCategory(
        id=cid, name=path.rsplit(".", 1)[-1], path=path, parent_id=parent_id,
        description=description, user_id=user_id,
    ))


def _value(repos, vid, category_id, user_id="alice"):
    assert repos[1].create(PhrasebookValue(
        id=vid, category_id=category_id, label=vid, value=vid, user_id=user_id,
    ))


class TestUpdatePhrasebookCategory:
    @pytest.mark.asyncio
    async def test_confirmed_alone_updates_name_description_and_active(self, repos):
        _category(repos, "c1", "camera")
        result = await UpdatePhrasebookCategoryTool().execute_confirmed(
            _context(repos), category="c1", name="Cameras", description="new", is_active=False,
        )
        assert result.success, result.error
        row = repos[0].get_by_id("c1", "alice")
        assert (row.name, row.description, row.is_active) == ("Cameras", "new", False)

    @pytest.mark.asyncio
    async def test_omitted_fields_keep_current_values_and_path_is_unchanged(self, repos):
        _category(repos, "c1", "camera", description="keep me")
        result = await UpdatePhrasebookCategoryTool().execute_confirmed(
            _context(repos), category="c1", name="Cameras",
        )
        assert result.success, result.error
        row = repos[0].get_by_id("c1", "alice")
        assert row.name == "Cameras"
        assert row.description == "keep me"
        assert row.is_active is True
        assert row.path == "camera"

    @pytest.mark.asyncio
    async def test_by_path_addressing_keeps_parent(self, repos):
        _category(repos, "p", "camera")
        _category(repos, "c", "camera.angles", parent_id="p")
        result = await UpdatePhrasebookCategoryTool().execute_confirmed(
            _context(repos), category="camera.angles", description="lens angles",
        )
        assert result.success, result.error
        row = repos[0].get_by_id("c", "alice")
        assert row.description == "lens angles"
        assert row.parent_id == "p"
        assert row.path == "camera.angles"

    @pytest.mark.asyncio
    async def test_other_users_category_is_not_found_and_not_modified(self, repos):
        _category(repos, "b1", "styles", user_id="bob", description="bobs")
        tool = UpdatePhrasebookCategoryTool()
        for call in (tool.execute, tool.execute_confirmed):
            result = await call(_context(repos), category="b1", name="Hacked", is_active=False)
            assert not result.success
            assert "not found" in result.error
        row = repos[0].get_by_id("b1", "bob")
        assert (row.name, row.description, row.is_active) == ("styles", "bobs", True)

    @pytest.mark.asyncio
    async def test_preview_changes_nothing(self, repos):
        _category(repos, "c1", "camera")
        result = await UpdatePhrasebookCategoryTool().execute(
            _context(repos), category="c1", name="Cameras", is_active=False,
        )
        assert result.success, result.error
        assert json.loads(result.data)["changes"] == {"name": "Cameras", "is_active": False}
        row = repos[0].get_by_id("c1", "alice")
        assert (row.name, row.is_active) == ("camera", True)

    @pytest.mark.asyncio
    async def test_requires_at_least_one_change(self, repos):
        _category(repos, "c1", "camera")
        result = await UpdatePhrasebookCategoryTool().execute_confirmed(_context(repos), category="c1")
        assert not result.success


class TestDeletePhrasebookCategory:
    @pytest.mark.asyncio
    async def test_confirmed_alone_deletes_category_and_its_values(self, repos):
        _category(repos, "c1", "camera")
        _value(repos, "v1", "c1")
        _value(repos, "v2", "c1")
        result = await DeletePhrasebookCategoryTool().execute_confirmed(_context(repos), category="camera")
        assert result.success, result.error
        assert json.loads(result.data)["deleted_value_count"] == 2
        assert repos[0].get_by_id("c1", "alice") is None
        assert repos[1].get_by_id("v1", "alice") is None

    @pytest.mark.asyncio
    async def test_refused_with_sub_categories_on_preview_and_confirmed(self, repos):
        _category(repos, "p", "camera")
        _category(repos, "c", "camera.angles", parent_id="p")
        tool = DeletePhrasebookCategoryTool()
        for call in (tool.execute, tool.execute_confirmed):
            result = await call(_context(repos), category="p")
            assert not result.success
            assert "1 sub-category" in result.error
        assert repos[0].get_by_id("p", "alice") is not None
        assert repos[0].get_by_id("c", "alice") is not None

    @pytest.mark.asyncio
    async def test_other_users_category_is_not_found_and_not_deleted(self, repos):
        _category(repos, "b1", "styles", user_id="bob")
        _value(repos, "bv", "b1", user_id="bob")
        tool = DeletePhrasebookCategoryTool()
        for call in (tool.execute, tool.execute_confirmed):
            result = await call(_context(repos), category="b1")
            assert not result.success
            assert "not found" in result.error
        assert repos[0].get_by_id("b1", "bob") is not None
        assert repos[1].get_by_id("bv", "bob") is not None

    @pytest.mark.asyncio
    async def test_preview_reports_value_count_and_deletes_nothing(self, repos):
        _category(repos, "c1", "camera")
        _value(repos, "v1", "c1")
        result = await DeletePhrasebookCategoryTool().execute(_context(repos), category="c1")
        assert result.success, result.error
        assert json.loads(result.data)["value_count"] == 1
        assert "1 value" in result.preview.note
        assert repos[0].get_by_id("c1", "alice") is not None
        assert repos[1].get_by_id("v1", "alice") is not None
