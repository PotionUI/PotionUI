"""Tests for ManageCollectionsTool."""

import json
from unittest.mock import MagicMock

import pytest

from src.features.collections.manager import CollectionManager
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.manage_collections_tool import ManageCollectionsTool


def make_manager():
    """A MagicMock bound to CollectionManager's real signature - if the
    manager's methods drift (e.g. gain/lose a `scope` parameter), calling
    them with the wrong arity raises here instead of silently passing."""
    return MagicMock(spec=CollectionManager)


def make_collection(id="col-1", name="Favorites", parent_id=None, item_count=3):
    collection = MagicMock()
    collection.id = id
    collection.name = name
    collection.parent_id = parent_id
    collection.item_count = item_count
    collection.to_dict.return_value = {
        "id": id, "name": name, "parent_id": parent_id, "item_count": item_count,
    }
    return collection


def make_context(manager=None, user_id="user-1"):
    return ToolContext(user_id=user_id, collection_manager=manager)


class TestSchema:
    def test_identity(self):
        tool = ManageCollectionsTool()
        assert tool.name == "manage_collections"
        assert tool.requires_approval is True
        assert set(tool.parameters["required"]) == {"operation", "scope"}
        assert set(tool.parameters["properties"]["operation"]["enum"]) == {
            "list", "create", "rename", "delete", "add_items", "remove_items",
        }
        assert set(tool.parameters["properties"]["scope"]["enum"]) == {"history", "library", "prompts"}
        assert "prompt_ids" in tool.parameters["properties"]


class TestExecutePreviewDoesNotMutate:
    @pytest.mark.asyncio
    async def test_create_preview_does_not_create(self):
        manager = make_manager()
        result = await ManageCollectionsTool().execute(
            make_context(manager), operation="create", scope="history", name="New",
        )
        assert result.success is True
        manager.create_collection.assert_not_called()
        assert json.loads(result.data)["proposal"]["name"] == "New"

    @pytest.mark.asyncio
    async def test_delete_preview_does_not_delete(self):
        manager = make_manager()
        manager.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(manager), operation="delete", scope="history", collection_id="col-1",
        )
        assert result.success is True
        manager.delete_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_rename_preview_does_not_rename(self):
        manager = make_manager()
        manager.get_collection.return_value = make_collection(name="Old")
        result = await ManageCollectionsTool().execute(
            make_context(manager), operation="rename", scope="history", collection_id="col-1", name="New",
        )
        assert result.success is True
        manager.rename_collection.assert_not_called()
        proposal = json.loads(result.data)["proposal"]
        assert proposal["old_name"] == "Old"
        assert proposal["new_name"] == "New"

    @pytest.mark.asyncio
    async def test_add_items_preview_does_not_add(self):
        manager = make_manager()
        manager.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(manager), operation="add_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1"],
        )
        assert result.success is True
        manager.add_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_returns_real_data(self):
        manager = make_manager()
        manager.list_collections.return_value = [make_collection()]
        result = await ManageCollectionsTool().execute(make_context(manager), operation="list", scope="history")
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["total"] == 1
        assert payload["collections"][0]["name"] == "Favorites"
        manager.list_collections.assert_called_once_with("user-1", "history")


class TestExecuteValidation:
    @pytest.mark.asyncio
    async def test_no_manager_errors(self):
        result = await ManageCollectionsTool().execute(make_context(None), operation="list", scope="history")
        assert result.success is False
        assert "not available" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unknown_operation_errors(self):
        result = await ManageCollectionsTool().execute(make_context(make_manager()), operation="destroy_everything", scope="history")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_missing_scope_errors(self):
        result = await ManageCollectionsTool().execute(make_context(make_manager()), operation="list")
        assert result.success is False
        assert "scope" in result.error.lower()

    @pytest.mark.asyncio
    async def test_invalid_scope_errors(self):
        result = await ManageCollectionsTool().execute(make_context(make_manager()), operation="list", scope="nonsense")
        assert result.success is False
        assert "scope" in result.error.lower()

    @pytest.mark.asyncio
    async def test_create_without_name_errors(self):
        result = await ManageCollectionsTool().execute(make_context(make_manager()), operation="create", scope="history")
        assert result.success is False
        assert "name" in result.error.lower()

    @pytest.mark.asyncio
    async def test_add_items_without_any_ids_errors(self):
        manager = make_manager()
        manager.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(manager), operation="add_items", scope="history", collection_id="col-1",
        )
        assert result.success is False
        assert "generation_ids" in result.error.lower()

    @pytest.mark.asyncio
    async def test_add_items_both_kinds_rejected_with_teaching_error(self):
        """Collections no longer mix membership kinds - passing two kinds in
        one call must be rejected with an error that teaches the model to
        split the call by surface, not just "invalid input"."""
        manager = make_manager()
        manager.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(manager), operation="add_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1"], upload_ids=["up-1"],
        )
        assert result.success is False
        assert "one call per surface" in result.error
        assert "history" in result.error and "library" in result.error and "prompts" in result.error
        manager.add_members.assert_not_called()
        manager.add_upload_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_items_wrong_kind_for_scope_names_kind_and_expected_scope(self):
        manager = make_manager()
        manager.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(manager), operation="add_items", scope="library",
            collection_id="col-1", generation_ids=["gen-1"],
        )
        assert result.success is False
        assert "generation_ids" in result.error
        assert "scope='history'" in result.error
        assert "scope='library'" in result.error
        manager.add_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_items_prompt_ids_wrong_scope_names_kind_and_expected_scope(self):
        manager = make_manager()
        manager.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(manager), operation="add_items", scope="history",
            collection_id="col-1", prompt_ids=["prompt-1"],
        )
        assert result.success is False
        assert "prompt_ids" in result.error
        assert "scope='prompts'" in result.error
        manager.add_prompt_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_found_surfaces_manager_value_error(self):
        manager = make_manager()
        manager.get_collection.side_effect = ValueError("Collection not found or access denied")
        result = await ManageCollectionsTool().execute(
            make_context(manager), operation="delete", scope="history", collection_id="missing",
        )
        assert result.success is False
        assert "not found" in result.error.lower()


class TestExecuteConfirmedMutates:
    @pytest.mark.asyncio
    async def test_create_calls_manager(self):
        manager = make_manager()
        manager.create_collection.return_value = make_collection(name="New")
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager, user_id="user-7"), operation="create", scope="history",
            name="New", parent_id="parent-1",
        )
        assert result.success is True
        manager.create_collection.assert_called_once_with("New", "user-7", "history", "parent-1")

    @pytest.mark.asyncio
    async def test_rename_calls_manager(self):
        manager = make_manager()
        manager.rename_collection.return_value = make_collection(name="Renamed")
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="rename", scope="history", collection_id="col-1", name="Renamed",
        )
        assert result.success is True
        manager.rename_collection.assert_called_once_with("col-1", "Renamed", "user-1", "history")

    @pytest.mark.asyncio
    async def test_delete_calls_manager(self):
        manager = make_manager()
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="delete", scope="history", collection_id="col-1",
        )
        assert result.success is True
        manager.delete_collection.assert_called_once_with("col-1", "user-1", "history")

    @pytest.mark.asyncio
    async def test_add_items_calls_manager_for_generations(self):
        manager = make_manager()
        manager.add_members.return_value = 2
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="add_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1", "gen-2"],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["changed"] == 2
        manager.add_members.assert_called_once_with("col-1", ["gen-1", "gen-2"], "user-1", "history")
        manager.add_upload_members.assert_not_called()
        manager.add_prompt_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_items_calls_manager_for_uploads(self):
        """Collections hold library items as a separate membership kind from
        generations - add_items must reach add_upload_members, not just add_members."""
        manager = make_manager()
        manager.add_upload_members.return_value = 3
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="add_items", scope="library",
            collection_id="col-1", upload_ids=["up-1", "up-2", "up-3"],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["changed"] == 3
        manager.add_upload_members.assert_called_once_with("col-1", ["up-1", "up-2", "up-3"], "user-1", "library")
        manager.add_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_items_calls_manager_for_prompts(self):
        manager = make_manager()
        manager.add_prompt_members.return_value = 1
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="add_items", scope="prompts",
            collection_id="col-1", prompt_ids=["prompt-1"],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["changed"] == 1
        manager.add_prompt_members.assert_called_once_with("col-1", ["prompt-1"], "user-1", "prompts")

    @pytest.mark.asyncio
    async def test_add_items_both_kinds_rejected_without_mutating(self):
        manager = make_manager()
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="add_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1"], upload_ids=["up-1"],
        )
        assert result.success is False
        assert "one call per surface" in result.error
        manager.add_members.assert_not_called()
        manager.add_upload_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_items_calls_manager_for_generations(self):
        manager = make_manager()
        manager.remove_members.return_value = 1
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="remove_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1"],
        )
        assert result.success is True
        manager.remove_members.assert_called_once_with("col-1", ["gen-1"], "user-1", "history")

    @pytest.mark.asyncio
    async def test_remove_items_calls_manager_for_uploads(self):
        manager = make_manager()
        manager.remove_upload_members.return_value = 1
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="remove_items", scope="library",
            collection_id="col-1", upload_ids=["up-1"],
        )
        assert result.success is True
        manager.remove_upload_members.assert_called_once_with("col-1", ["up-1"], "user-1", "library")

    @pytest.mark.asyncio
    async def test_remove_items_calls_manager_for_prompts(self):
        manager = make_manager()
        manager.remove_prompt_members.return_value = 1
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="remove_items", scope="prompts",
            collection_id="col-1", prompt_ids=["prompt-1"],
        )
        assert result.success is True
        manager.remove_prompt_members.assert_called_once_with("col-1", ["prompt-1"], "user-1", "prompts")

    @pytest.mark.asyncio
    async def test_list_via_confirmed_works_for_mcp_direct_path(self):
        """MCP's requires_approval short-circuit calls execute_confirmed directly,
        never execute() - so 'list' must return real data from execute_confirmed too."""
        manager = make_manager()
        manager.list_collections.return_value = [make_collection()]
        result = await ManageCollectionsTool().execute_confirmed(make_context(manager), operation="list", scope="history")
        assert result.success is True
        assert json.loads(result.data)["total"] == 1


class TestExecuteConfirmedUnexpectedError:
    @pytest.mark.asyncio
    async def test_unexpected_exception_names_tool_and_operation_not_bare_message(self):
        manager = make_manager()
        manager.delete_collection.side_effect = RuntimeError("db exploded")
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(manager), operation="delete", scope="history", collection_id="col-1",
        )
        assert result.success is False
        assert "manage_collections" in result.error
        assert "delete" in result.error
        assert "db exploded" in result.error
        assert result.error != "Failed: db exploded"
