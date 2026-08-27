"""Tests for OrganizeGalleryTool."""

import json
from unittest.mock import MagicMock, Mock

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin import organize_gallery_tool as tool_module
from src.features.llm.tools.builtin.organize_gallery_tool import OrganizeGalleryTool


def make_history_facade(current_tags=None, history=None):
    manager = MagicMock()
    manager.get_tags.return_value = current_tags or []
    manager.get_history.return_value = history or {"generations": [], "total": 0}
    return manager


def make_tag_repository():
    return MagicMock()


def make_context(history_facade=None, tag_repository=None, user_id="user-1"):
    return ToolContext(
        user_id=user_id, generation_history_facade=history_facade,
        tag_repository=tag_repository, plugin_registry=Mock(),
    )


@pytest.fixture
def mock_tag_operations(monkeypatch):
    """Patch the `tag_operations` module as seen by organize_gallery_tool.py."""
    mock = Mock()
    monkeypatch.setattr(tool_module, "tag_operations", mock)
    return mock


class TestSchema:
    def test_identity(self):
        tool = OrganizeGalleryTool()
        assert tool.name == "organize_gallery"
        assert tool.requires_approval is True
        assert tool.parameters["required"] == ["operation"]

    def test_modes_include_generation_and_history(self):
        assert OrganizeGalleryTool().modes == ["generation", "history"]


class TestListRecentFilters:
    """list_recent doubles as the History scope's search: its optional filters
    must reach GenerationHistoryFacade.get_history, not just `limit`."""

    @pytest.mark.asyncio
    async def test_filters_are_threaded_to_get_history(self):
        history_facade = make_history_facade(history={"generations": [], "total": 0})
        await OrganizeGalleryTool().execute(
            make_context(history_facade),
            operation="list_recent",
            text="a fox",
            preset_id="preset-1",
            model_name="sdxl.safetensors",
            min_rating=4,
            created_from="2026-08-01T00:00:00",
            created_to="2026-08-19T00:00:00",
        )
        _, kwargs = history_facade.get_history.call_args
        assert kwargs["search"] == "a fox"
        assert kwargs["preset_id"] == "preset-1"
        assert kwargs["model_name"] == "sdxl.safetensors"
        assert kwargs["min_rating"] == 4
        assert kwargs["created_from"] == "2026-08-01T00:00:00"
        assert kwargs["created_to"] == "2026-08-19T00:00:00"

    @pytest.mark.asyncio
    async def test_omitted_filters_pass_none_not_missing_generations(self):
        """Without filters, list_recent must still return everything - a stray
        empty-string filter must not narrow the query to nothing."""
        history_facade = make_history_facade(history={"generations": [], "total": 0})
        await OrganizeGalleryTool().execute(make_context(history_facade), operation="list_recent")
        _, kwargs = history_facade.get_history.call_args
        assert kwargs["search"] is None
        assert kwargs["preset_id"] is None
        assert kwargs["model_name"] is None
        assert kwargs["min_rating"] is None


class TestExecuteValidation:
    @pytest.mark.asyncio
    async def test_no_history_facade_errors(self):
        result = await OrganizeGalleryTool().execute(make_context(None), operation="list_recent")
        assert result.success is False
        assert "not available" in result.error.lower()

    @pytest.mark.asyncio
    async def test_tag_without_tags_errors(self):
        history_facade = make_history_facade()
        result = await OrganizeGalleryTool().execute(
            make_context(history_facade), operation="tag", generation_id="gen-1",
        )
        assert result.success is False
        assert "tags" in result.error.lower()

    @pytest.mark.asyncio
    async def test_rate_out_of_range_errors(self):
        history_facade = make_history_facade()
        result = await OrganizeGalleryTool().execute(
            make_context(history_facade), operation="rate", generation_id="gen-1", rating=9,
        )
        assert result.success is False
        assert "0 and 5" in result.error


class TestExecutePreviewDoesNotMutate:
    @pytest.mark.asyncio
    async def test_tag_preview_does_not_update_tags(self):
        history_facade = make_history_facade()
        result = await OrganizeGalleryTool().execute(
            make_context(history_facade), operation="tag", generation_id="gen-1", tags=["favorite"],
        )
        assert result.success is True
        history_facade.update_tags.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_preview_does_not_set_rating(self):
        history_facade = make_history_facade()
        result = await OrganizeGalleryTool().execute(
            make_context(history_facade), operation="rate", generation_id="gen-1", rating=4,
        )
        assert result.success is True
        history_facade.set_rating.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_recent_returns_real_data(self):
        history_facade = make_history_facade(history={
            "generations": [{
                "id": "gen-1", "status": "completed", "created_at": "2026-08-18T00:00:00",
                "rating": 3, "is_favorite": False, "form_data": {"prompt": "a fox"},
                "preset_name": "SDXL", "tags": [{"name": "favorite"}],
                "files": [{"file_path": "generations/2026-08-18/gen-1/1.png"}],
            }],
            "total": 1,
        })
        result = await OrganizeGalleryTool().execute(make_context(history_facade), operation="list_recent")
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["generations"][0]["prompt"] == "a fox"
        assert payload["generations"][0]["tags"] == ["favorite"]
        assert payload["generations"][0]["paths"] == ["generations/2026-08-18/gen-1/1.png"]


class TestExecuteConfirmedTag:
    @pytest.mark.asyncio
    async def test_tag_merges_with_existing_tags_not_replaces(self, mock_tag_operations):
        """update_tags REPLACES the full set - tagging must merge with what's
        already there, not clobber existing tags the model doesn't know about."""
        history_facade = make_history_facade(current_tags=[{"id": "tag-existing", "name": "old"}])
        history_facade.update_tags.return_value = [
            {"id": "tag-existing", "name": "old"}, {"id": "tag-new", "name": "favorite"},
        ]
        tag_repository = make_tag_repository()
        tag_repository.get_tag_by_name.return_value = None
        created = MagicMock()
        created.id = "tag-new"
        mock_tag_operations.create_tag.return_value = created

        result = await OrganizeGalleryTool().execute_confirmed(
            make_context(history_facade, tag_repository), operation="tag", generation_id="gen-1", tags=["favorite"],
        )
        assert result.success is True
        call_ids = set(history_facade.update_tags.call_args[0][1])
        assert call_ids == {"tag-existing", "tag-new"}

    @pytest.mark.asyncio
    async def test_tag_reuses_existing_named_tag_instead_of_creating(self, mock_tag_operations):
        history_facade = make_history_facade(current_tags=[])
        history_facade.update_tags.return_value = [{"id": "tag-1", "name": "favorite"}]
        tag_repository = make_tag_repository()
        existing_tag = MagicMock()
        existing_tag.id = "tag-1"
        tag_repository.get_tag_by_name.return_value = existing_tag

        await OrganizeGalleryTool().execute_confirmed(
            make_context(history_facade, tag_repository), operation="tag", generation_id="gen-1", tags=["favorite"],
        )
        mock_tag_operations.create_tag.assert_not_called()

    @pytest.mark.asyncio
    async def test_untag_removes_only_resolved_names(self):
        history_facade = make_history_facade()
        history_facade.remove_tag.return_value = True
        tag_repository = make_tag_repository()
        known = MagicMock()
        known.id = "tag-1"

        def by_name(name, type=None, user_id=None):
            return known if name == "known" else None

        tag_repository.get_tag_by_name.side_effect = by_name

        result = await OrganizeGalleryTool().execute_confirmed(
            make_context(history_facade, tag_repository), operation="untag", generation_id="gen-1",
            tags=["known", "nonexistent"],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["removed"] == ["known"]
        assert payload["skipped"] == ["nonexistent"]
        history_facade.remove_tag.assert_called_once_with("gen-1", "tag-1", "user-1")

    @pytest.mark.asyncio
    async def test_tag_without_tag_repository_teaches_instead_of_dead_ending(self):
        history_facade = make_history_facade()
        result = await OrganizeGalleryTool().execute_confirmed(
            make_context(history_facade, tag_repository=None), operation="tag", generation_id="gen-1", tags=["x"],
        )
        assert result.success is False
        assert "tag" in result.error.lower()
        assert "not" in result.error.lower()

    @pytest.mark.asyncio
    async def test_untag_without_tag_repository_teaches_instead_of_dead_ending(self):
        history_facade = make_history_facade()
        result = await OrganizeGalleryTool().execute_confirmed(
            make_context(history_facade, tag_repository=None), operation="untag", generation_id="gen-1", tags=["x"],
        )
        assert result.success is False
        assert "tag" in result.error.lower()


class TestExecuteConfirmedUnexpectedError:
    @pytest.mark.asyncio
    async def test_unexpected_exception_names_tool_and_operation_not_bare_message(self):
        history_facade = make_history_facade()
        history_facade.set_rating.side_effect = RuntimeError("db exploded")
        result = await OrganizeGalleryTool().execute_confirmed(
            make_context(history_facade), operation="rate", generation_id="gen-1", rating=5,
        )
        assert result.success is False
        assert "organize_gallery" in result.error
        assert "rate" in result.error
        assert "db exploded" in result.error
        assert result.error != "Failed: db exploded"


class TestListRecentError:
    @pytest.mark.asyncio
    async def test_failed_generation_surfaces_truncated_error(self):
        history_facade = make_history_facade(history={
            "generations": [{
                "id": "gen-1", "status": "failed", "created_at": "2026-08-18T00:00:00",
                "rating": 0, "is_favorite": False, "form_data": {"prompt": "a fox"},
                "preset_name": "SDXL", "tags": [], "files": [],
                "error_message": "x" * 600,
            }],
            "total": 1,
        })
        result = await OrganizeGalleryTool().execute(make_context(history_facade), operation="list_recent")
        payload = json.loads(result.data)
        error = payload["generations"][0]["error"]
        assert error is not None
        assert len(error) <= 503
        assert error.endswith("x" * 500)

    @pytest.mark.asyncio
    async def test_successful_generation_has_no_error(self):
        history_facade = make_history_facade(history={
            "generations": [{
                "id": "gen-1", "status": "completed", "created_at": "2026-08-18T00:00:00",
                "rating": 0, "is_favorite": False, "form_data": {"prompt": "a fox"},
                "preset_name": "SDXL", "tags": [], "files": [],
                "error_message": None,
            }],
            "total": 1,
        })
        result = await OrganizeGalleryTool().execute(make_context(history_facade), operation="list_recent")
        payload = json.loads(result.data)
        assert payload["generations"][0]["error"] is None


class TestGetOperation:
    @pytest.mark.asyncio
    async def test_get_returns_full_untruncated_error(self):
        history_facade = make_history_facade()
        history_facade.get_by_id.return_value = {
            "id": "gen-1", "status": "failed", "error_message": "y" * 600,
            "form_data": {"prompt": "a fox"}, "preset_name": "SDXL",
            "created_at": "2026-08-18T00:00:00", "started_at": None, "completed_at": None,
            "rating": 0, "is_favorite": False, "tags": [], "files": [],
        }
        result = await OrganizeGalleryTool().execute(
            make_context(history_facade), operation="get", generation_id="gen-1",
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["error"] == "y" * 600
        history_facade.get_by_id.assert_called_once_with("gen-1", "user-1")

    @pytest.mark.asyncio
    async def test_get_via_confirmed_works_for_mcp_direct_path(self):
        history_facade = make_history_facade()
        history_facade.get_by_id.return_value = {
            "id": "gen-1", "status": "completed", "error_message": None,
            "form_data": {}, "preset_name": "SDXL", "created_at": None,
            "started_at": None, "completed_at": None, "rating": 0, "is_favorite": False,
            "tags": [], "files": [],
        }
        result = await OrganizeGalleryTool().execute_confirmed(
            make_context(history_facade), operation="get", generation_id="gen-1",
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["error"] is None

    @pytest.mark.asyncio
    async def test_get_without_generation_id_errors(self):
        history_facade = make_history_facade()
        result = await OrganizeGalleryTool().execute(make_context(history_facade), operation="get")
        assert result.success is False
        assert "generation_id" in result.error.lower()


class TestExecuteConfirmedRate:
    @pytest.mark.asyncio
    async def test_rate_calls_manager(self):
        history_facade = make_history_facade()
        history_facade.set_rating.return_value = 4
        result = await OrganizeGalleryTool().execute_confirmed(
            make_context(history_facade), operation="rate", generation_id="gen-1", rating=4,
        )
        assert result.success is True
        history_facade.set_rating.assert_called_once_with("gen-1", 4, "user-1")

    @pytest.mark.asyncio
    async def test_list_recent_via_confirmed_works_for_mcp_direct_path(self):
        """MCP's requires_approval short-circuit calls execute_confirmed directly."""
        history_facade = make_history_facade(history={"generations": [], "total": 0})
        result = await OrganizeGalleryTool().execute_confirmed(make_context(history_facade), operation="list_recent")
        assert result.success is True
