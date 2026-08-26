"""Tests for ManageCollectionsTool.

The tool calls `src.features.collections.operations` functions directly
(module-level, no injected manager), against a `context.collection_repository`
collaborator. `mock_operations` patches the `operations` module as imported
into the tool module, so tests assert against it exactly like the previous
manager mock, without the tool holding a stateful collaborator it doesn't need.
"""

import json
from unittest.mock import MagicMock, Mock

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin import manage_collections_tool as tool_module
from src.features.llm.tools.builtin.manage_collections_tool import ManageCollectionsTool


@pytest.fixture
def mock_operations(monkeypatch):
    """Patch the `operations` module as seen by manage_collections_tool.py."""
    mock = Mock()
    monkeypatch.setattr(tool_module, "operations", mock)
    return mock


def make_repository():
    """A mock CollectionRepository (used directly for the pure `list` read)."""
    return MagicMock()


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


def make_context(repository=None, user_id="user-1"):
    return ToolContext(user_id=user_id, collection_repository=repository)


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
    async def test_create_preview_does_not_create(self, mock_operations):
        repository = make_repository()
        result = await ManageCollectionsTool().execute(
            make_context(repository), operation="create", scope="history", name="New",
        )
        assert result.success is True
        mock_operations.create_collection.assert_not_called()
        assert json.loads(result.data)["proposal"]["name"] == "New"

    @pytest.mark.asyncio
    async def test_delete_preview_does_not_delete(self, mock_operations):
        repository = make_repository()
        mock_operations.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(repository), operation="delete", scope="history", collection_id="col-1",
        )
        assert result.success is True
        mock_operations.delete_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_rename_preview_does_not_rename(self, mock_operations):
        repository = make_repository()
        mock_operations.get_collection.return_value = make_collection(name="Old")
        result = await ManageCollectionsTool().execute(
            make_context(repository), operation="rename", scope="history", collection_id="col-1", name="New",
        )
        assert result.success is True
        mock_operations.rename_collection.assert_not_called()
        proposal = json.loads(result.data)["proposal"]
        assert proposal["old_name"] == "Old"
        assert proposal["new_name"] == "New"

    @pytest.mark.asyncio
    async def test_add_items_preview_does_not_add(self, mock_operations):
        repository = make_repository()
        mock_operations.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(repository), operation="add_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1"],
        )
        assert result.success is True
        mock_operations.add_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_returns_real_data(self, mock_operations):
        repository = make_repository()
        repository.list.return_value = [make_collection()]
        result = await ManageCollectionsTool().execute(make_context(repository), operation="list", scope="history")
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["total"] == 1
        assert payload["collections"][0]["name"] == "Favorites"
        repository.list.assert_called_once_with("user-1", "history")


class TestExecuteValidation:
    @pytest.mark.asyncio
    async def test_no_repository_errors(self):
        result = await ManageCollectionsTool().execute(make_context(None), operation="list", scope="history")
        assert result.success is False
        assert "not available" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unknown_operation_errors(self, mock_operations):
        result = await ManageCollectionsTool().execute(make_context(make_repository()), operation="destroy_everything", scope="history")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_missing_scope_errors(self, mock_operations):
        result = await ManageCollectionsTool().execute(make_context(make_repository()), operation="list")
        assert result.success is False
        assert "scope" in result.error.lower()

    @pytest.mark.asyncio
    async def test_invalid_scope_errors(self, mock_operations):
        result = await ManageCollectionsTool().execute(make_context(make_repository()), operation="list", scope="nonsense")
        assert result.success is False
        assert "scope" in result.error.lower()

    @pytest.mark.asyncio
    async def test_create_without_name_errors(self, mock_operations):
        result = await ManageCollectionsTool().execute(make_context(make_repository()), operation="create", scope="history")
        assert result.success is False
        assert "name" in result.error.lower()

    @pytest.mark.asyncio
    async def test_add_items_without_any_ids_errors(self, mock_operations):
        repository = make_repository()
        mock_operations.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(repository), operation="add_items", scope="history", collection_id="col-1",
        )
        assert result.success is False
        assert "generation_ids" in result.error.lower()

    @pytest.mark.asyncio
    async def test_add_items_both_kinds_rejected_with_teaching_error(self, mock_operations):
        """Collections no longer mix membership kinds - passing two kinds in
        one call must be rejected with an error that teaches the model to
        split the call by surface, not just "invalid input"."""
        repository = make_repository()
        mock_operations.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(repository), operation="add_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1"], upload_ids=["up-1"],
        )
        assert result.success is False
        assert "one call per surface" in result.error
        assert "history" in result.error and "library" in result.error and "prompts" in result.error
        mock_operations.add_members.assert_not_called()
        mock_operations.add_upload_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_items_wrong_kind_for_scope_names_kind_and_expected_scope(self, mock_operations):
        repository = make_repository()
        mock_operations.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(repository), operation="add_items", scope="library",
            collection_id="col-1", generation_ids=["gen-1"],
        )
        assert result.success is False
        assert "generation_ids" in result.error
        assert "scope='history'" in result.error
        assert "scope='library'" in result.error
        mock_operations.add_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_items_prompt_ids_wrong_scope_names_kind_and_expected_scope(self, mock_operations):
        repository = make_repository()
        mock_operations.get_collection.return_value = make_collection()
        result = await ManageCollectionsTool().execute(
            make_context(repository), operation="add_items", scope="history",
            collection_id="col-1", prompt_ids=["prompt-1"],
        )
        assert result.success is False
        assert "prompt_ids" in result.error
        assert "scope='prompts'" in result.error
        mock_operations.add_prompt_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_found_surfaces_operations_value_error(self, mock_operations):
        repository = make_repository()
        mock_operations.get_collection.side_effect = ValueError("Collection not found or access denied")
        result = await ManageCollectionsTool().execute(
            make_context(repository), operation="delete", scope="history", collection_id="missing",
        )
        assert result.success is False
        assert "not found" in result.error.lower()


class TestExecuteConfirmedMutates:
    @pytest.mark.asyncio
    async def test_create_calls_operations(self, mock_operations):
        repository = make_repository()
        mock_operations.create_collection.return_value = make_collection(name="New")
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository, user_id="user-7"), operation="create", scope="history",
            name="New", parent_id="parent-1",
        )
        assert result.success is True
        mock_operations.create_collection.assert_called_once_with(repository, "New", "user-7", "history", "parent-1")

    @pytest.mark.asyncio
    async def test_rename_calls_operations(self, mock_operations):
        repository = make_repository()
        mock_operations.rename_collection.return_value = make_collection(name="Renamed")
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="rename", scope="history", collection_id="col-1", name="Renamed",
        )
        assert result.success is True
        mock_operations.rename_collection.assert_called_once_with(repository, "col-1", "Renamed", "user-1", "history")

    @pytest.mark.asyncio
    async def test_delete_calls_operations(self, mock_operations):
        repository = make_repository()
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="delete", scope="history", collection_id="col-1",
        )
        assert result.success is True
        mock_operations.delete_collection.assert_called_once_with(repository, "col-1", "user-1", "history")

    @pytest.mark.asyncio
    async def test_add_items_calls_operations_for_generations(self, mock_operations):
        repository = make_repository()
        mock_operations.add_members.return_value = 2
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="add_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1", "gen-2"],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["changed"] == 2
        mock_operations.add_members.assert_called_once_with(repository, "col-1", ["gen-1", "gen-2"], "user-1", "history")
        mock_operations.add_upload_members.assert_not_called()
        mock_operations.add_prompt_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_items_calls_operations_for_uploads(self, mock_operations):
        """Collections hold library items as a separate membership kind from
        generations - add_items must reach add_upload_members, not just add_members."""
        repository = make_repository()
        mock_operations.add_upload_members.return_value = 3
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="add_items", scope="library",
            collection_id="col-1", upload_ids=["up-1", "up-2", "up-3"],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["changed"] == 3
        mock_operations.add_upload_members.assert_called_once_with(repository, "col-1", ["up-1", "up-2", "up-3"], "user-1", "library")
        mock_operations.add_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_items_calls_operations_for_prompts(self, mock_operations):
        repository = make_repository()
        mock_operations.add_prompt_members.return_value = 1
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="add_items", scope="prompts",
            collection_id="col-1", prompt_ids=["prompt-1"],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["changed"] == 1
        mock_operations.add_prompt_members.assert_called_once_with(repository, "col-1", ["prompt-1"], "user-1", "prompts")

    @pytest.mark.asyncio
    async def test_add_items_both_kinds_rejected_without_mutating(self, mock_operations):
        repository = make_repository()
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="add_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1"], upload_ids=["up-1"],
        )
        assert result.success is False
        assert "one call per surface" in result.error
        mock_operations.add_members.assert_not_called()
        mock_operations.add_upload_members.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_items_calls_operations_for_generations(self, mock_operations):
        repository = make_repository()
        mock_operations.remove_members.return_value = 1
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="remove_items", scope="history",
            collection_id="col-1", generation_ids=["gen-1"],
        )
        assert result.success is True
        mock_operations.remove_members.assert_called_once_with(repository, "col-1", ["gen-1"], "user-1", "history")

    @pytest.mark.asyncio
    async def test_remove_items_calls_operations_for_uploads(self, mock_operations):
        repository = make_repository()
        mock_operations.remove_upload_members.return_value = 1
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="remove_items", scope="library",
            collection_id="col-1", upload_ids=["up-1"],
        )
        assert result.success is True
        mock_operations.remove_upload_members.assert_called_once_with(repository, "col-1", ["up-1"], "user-1", "library")

    @pytest.mark.asyncio
    async def test_remove_items_calls_operations_for_prompts(self, mock_operations):
        repository = make_repository()
        mock_operations.remove_prompt_members.return_value = 1
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="remove_items", scope="prompts",
            collection_id="col-1", prompt_ids=["prompt-1"],
        )
        assert result.success is True
        mock_operations.remove_prompt_members.assert_called_once_with(repository, "col-1", ["prompt-1"], "user-1", "prompts")

    @pytest.mark.asyncio
    async def test_list_via_confirmed_works_for_mcp_direct_path(self, mock_operations):
        """MCP's requires_approval short-circuit calls execute_confirmed directly,
        never execute() - so 'list' must return real data from execute_confirmed too."""
        repository = make_repository()
        repository.list.return_value = [make_collection()]
        result = await ManageCollectionsTool().execute_confirmed(make_context(repository), operation="list", scope="history")
        assert result.success is True
        assert json.loads(result.data)["total"] == 1


class TestExecuteConfirmedUnexpectedError:
    @pytest.mark.asyncio
    async def test_unexpected_exception_names_tool_and_operation_not_bare_message(self, mock_operations):
        repository = make_repository()
        mock_operations.delete_collection.side_effect = RuntimeError("db exploded")
        result = await ManageCollectionsTool().execute_confirmed(
            make_context(repository), operation="delete", scope="history", collection_id="col-1",
        )
        assert result.success is False
        assert "manage_collections" in result.error
        assert "delete" in result.error
        assert "db exploded" in result.error
        assert result.error != "Failed: db exploded"
