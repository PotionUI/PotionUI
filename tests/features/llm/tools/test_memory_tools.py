"""Tests for LLM memory tools (write, read, delete)."""

import json
import pytest
from unittest.mock import MagicMock

from src.features.llm.tools.base import ToolApprovalPreview, ToolContext
from src.features.llm.tools.builtin.memory_tool import (
    WriteMemoryTool,
    ReadMemoryTool,
    UpdateMemoryTool,
    DeleteMemoryTool,
)
from src.features.llm_memory.records import LLMMemoryNote


@pytest.fixture
def mock_ops(monkeypatch):
    """`memory_operations` (as imported into memory_tool.py) patched to a
    Mock, so tests assert on write_note/read_notes/etc. calls without
    exercising the real validation logic - covered separately by
    tests/features/llm_memory/test_operations.py. `mm` (the ToolContext's
    `llm_memory_repository`) is passed through as the operations' leading
    repository argument, so it still identifies which repository a call
    was made against."""
    mock = MagicMock()
    monkeypatch.setattr("src.features.llm.tools.builtin.memory_tool.memory_operations", mock)
    return mock


def make_context(**kwargs) -> ToolContext:
    return ToolContext(user_id="user-test", **kwargs)


def make_note(**overrides):
    defaults = {
        "id": "note-1",
        "user_id": "user-test",
        "key": "pref_style",
        "content": "User prefers cinematic lighting",
        "scope": "global",
        "scope_ref": None,
    }
    defaults.update(overrides)
    return LLMMemoryNote(**defaults)


# ---------------------------------------------------------------------------
# WriteMemoryTool
# ---------------------------------------------------------------------------

class TestWriteMemoryTool:
    def _tool(self):
        return WriteMemoryTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "write_memory"
        assert tool.requires_approval is False
        schema = tool.to_schema()
        assert "key" in schema["function"]["parameters"]["properties"]
        assert "content" in schema["function"]["parameters"]["properties"]
        assert "scope" in schema["function"]["parameters"]["properties"]
        assert "key" in schema["function"]["parameters"]["required"]
        assert "content" in schema["function"]["parameters"]["required"]
        assert "scope" in schema["function"]["parameters"]["required"]
        assert "default" not in schema["function"]["parameters"]["properties"]["scope"]

    def test_has_hint(self):
        tool = self._tool()
        assert tool.hint
        assert "remember" in tool.hint.lower()

    @pytest.mark.asyncio
    async def test_execute_persists_immediately(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.write_note.return_value = note
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, key="pref_style", content="likes anime", scope="global")

        assert result.success is True
        data = json.loads(result.data)
        assert data["action"] == "write_memory"
        assert data["success"] is True
        assert data["note_id"] == "note-1"
        assert data["scope"] == "global"
        assert "scope_hint" not in data
        mock_ops.write_note.assert_called_once_with(
            mm, user_id="user-test",
            key="pref_style",
            content="likes anime",
            scope="global",
            scope_ref=None,
        )

    @pytest.mark.asyncio
    async def test_execute_requires_scope(self, mock_ops):
        mm = MagicMock()
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, key="pref_style", content="likes anime")

        assert result.success is False
        assert "scope is required" in result.error
        mock_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_global_scope_nudges_when_content_names_active_preset(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.write_note.return_value = note
        preset_manager = MagicMock()
        preset_manager.get_preset.return_value = {"preset": {"id": "preset-1", "name": "Krea-2 Turbo"}}
        ctx = make_context(
            llm_memory_repository=mm,
            preset_manager=preset_manager,
            session_metadata={"form_state": {"preset": "preset-1", "form_data": {}}},
        )

        result = await self._tool().execute(
            ctx, key="quirk", content="on Krea-2 Turbo, cfg 1 washes out reds", scope="global",
        )

        assert result.success is True
        data = json.loads(result.data)
        assert "scope_hint" in data
        assert "Krea-2 Turbo" in data["scope_hint"]
        assert "scope='preset'" in data["scope_hint"]

    @pytest.mark.asyncio
    async def test_execute_global_scope_no_nudge_for_genuinely_global_note(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.write_note.return_value = note
        preset_manager = MagicMock()
        preset_manager.get_preset.return_value = {"preset": {"id": "preset-1", "name": "Krea-2 Turbo"}}
        ctx = make_context(
            llm_memory_repository=mm,
            preset_manager=preset_manager,
            session_metadata={"form_state": {"preset": "preset-1", "form_data": {}}},
        )

        result = await self._tool().execute(
            ctx, key="pref_style", content="prefers painterly fantasy scenes, dislikes photorealism",
            scope="global",
        )

        assert result.success is True
        data = json.loads(result.data)
        assert "scope_hint" not in data

    @pytest.mark.asyncio
    async def test_execute_model_scope_auto_resolves(self, mock_ops):
        note = make_note(scope="model", scope_ref="model-1")
        mm = MagicMock()
        mock_ops.write_note.return_value = note
        mim = MagicMock()
        model = MagicMock()
        model.id = "model-1"
        mim.model_repo.get_by_file_path.return_value = model
        ctx = make_context(
            llm_memory_repository=mm,
            model_index_manager=mim,
            session_metadata={
                "form_state": {
                    "form_data": {"checkpoint": "models/checkpoints/sdxl.safetensors"}
                }
            },
        )

        result = await self._tool().execute(ctx, key="quirk", content="low cfg", scope="model")

        assert result.success is True
        mock_ops.write_note.assert_called_once_with(
            mm, user_id="user-test",
            key="quirk",
            content="low cfg",
            scope="model",
            scope_ref="model-1",
        )

    @pytest.mark.asyncio
    async def test_execute_preset_scope_auto_resolves(self, mock_ops):
        note = make_note(scope="preset", scope_ref="preset-1")
        mm = MagicMock()
        mock_ops.write_note.return_value = note
        ctx = make_context(
            llm_memory_repository=mm,
            session_metadata={"form_state": {"preset": "preset-1", "form_data": {}}},
        )

        result = await self._tool().execute(ctx, key="quirk", content="use 30 steps", scope="preset")

        assert result.success is True
        mock_ops.write_note.assert_called_once_with(
            mm, user_id="user-test",
            key="quirk",
            content="use 30 steps",
            scope="preset",
            scope_ref="preset-1",
        )

    @pytest.mark.asyncio
    async def test_execute_model_scope_fails_without_scope_ref(self, mock_ops):
        mm = MagicMock()
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, key="test", content="test", scope="model")

        assert result.success is False
        assert "scope_ref is required" in result.error
        mock_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_no_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, key="k", content="c")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_execute_missing_key(self):
        mm = MagicMock()
        ctx = make_context(llm_memory_repository=mm)
        result = await self._tool().execute(ctx, content="c")
        assert result.success is False
        assert "key is required" in result.error

    @pytest.mark.asyncio
    async def test_execute_missing_content(self):
        mm = MagicMock()
        ctx = make_context(llm_memory_repository=mm)
        result = await self._tool().execute(ctx, key="k")
        assert result.success is False
        assert "content is required" in result.error

    @pytest.mark.asyncio
    async def test_execute_handles_error(self, mock_ops):
        mm = MagicMock()
        mock_ops.write_note.side_effect = RuntimeError("db error")
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, key="k", content="c", scope="global")

        assert result.success is False
        assert "db error" in result.error


# ---------------------------------------------------------------------------
# ReadMemoryTool
# ---------------------------------------------------------------------------

class TestReadMemoryTool:
    def _tool(self):
        return ReadMemoryTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "read_memory"
        assert tool.requires_approval is False
        schema = tool.to_schema()
        assert "scope" in schema["function"]["parameters"]["properties"]
        assert schema["function"]["parameters"]["required"] == []

    def test_has_hint(self):
        tool = self._tool()
        assert tool.hint
        assert "memory" in tool.hint.lower()

    @pytest.mark.asyncio
    async def test_execute_returns_all_notes(self, mock_ops):
        global_notes = [make_note(id="g1", scope="global")]
        preset_notes = [make_note(id="p1", scope="preset", scope_ref="preset-1")]
        model_notes = [make_note(id="m1", scope="model", scope_ref="model-1")]
        mm = MagicMock()
        mock_ops.read_notes.side_effect = [global_notes, preset_notes, model_notes]

        mim = MagicMock()
        model = MagicMock()
        model.id = "model-1"
        mim.model_repo.get_by_file_path.return_value = model

        ctx = make_context(
            llm_memory_repository=mm,
            model_index_manager=mim,
            session_metadata={
                "form_state": {
                    "preset": "preset-1",
                    "form_data": {"checkpoint": "models/checkpoints/sdxl.safetensors"},
                }
            },
        )

        result = await self._tool().execute(ctx, scope="all")

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 3
        assert data["scope_filter"] == "all"

    @pytest.mark.asyncio
    async def test_execute_global_scope(self, mock_ops):
        notes = [make_note(id="g1")]
        mm = MagicMock()
        mock_ops.read_notes.return_value = notes
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, scope="global")

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        mock_ops.read_notes.assert_called_once_with(
            mm, user_id="user-test", scope="global")

    @pytest.mark.asyncio
    async def test_execute_no_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_execute_handles_error(self, mock_ops):
        mm = MagicMock()
        mock_ops.read_notes.side_effect = RuntimeError("db error")
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, scope="global")

        assert result.success is False
        assert "db error" in result.error

    @pytest.mark.asyncio
    async def test_execute_all_without_model_or_preset(self, mock_ops):
        """scope=all without a preset/model returns only global notes."""
        global_notes = [make_note(id="g1")]
        mm = MagicMock()
        mock_ops.read_notes.return_value = global_notes
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, scope="all")

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        # Only one call (global), no preset/model calls since refs are None
        assert mock_ops.read_notes.call_count == 1


# ---------------------------------------------------------------------------
# DeleteMemoryTool
# ---------------------------------------------------------------------------

class TestDeleteMemoryTool:
    def _tool(self):
        return DeleteMemoryTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "delete_memory"
        assert tool.requires_approval is True
        schema = tool.to_schema()
        assert "note_id" in schema["function"]["parameters"]["properties"]
        assert "note_id" in schema["function"]["parameters"]["required"]

    def test_has_hint(self):
        tool = self._tool()
        assert tool.hint
        assert "memory" in tool.hint.lower()

    @pytest.mark.asyncio
    async def test_execute_returns_preview(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.get_note.return_value = note
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, note_id="note-1")

        assert result.success is True
        data = json.loads(result.data)
        assert data["action"] == "delete_memory"
        assert data["proposal"]["note_id"] == "note-1"
        assert data["proposal"]["key"] == "pref_style"

    @pytest.mark.asyncio
    async def test_execute_note_not_found(self, mock_ops):
        mm = MagicMock()
        mock_ops.get_note.return_value = None
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, note_id="missing")

        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_execute_no_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, note_id="x")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_execute_missing_note_id(self):
        mm = MagicMock()
        ctx = make_context(llm_memory_repository=mm)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "note_id is required" in result.error

    @pytest.mark.asyncio
    async def test_execute_confirmed_deletes(self, mock_ops):
        mm = MagicMock()
        mock_ops.delete_note.return_value = True
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(ctx, note_id="note-1")

        assert result.success is True
        data = json.loads(result.data)
        assert data["success"] is True
        assert data["note_id"] == "note-1"
        mock_ops.delete_note.assert_called_once_with(
            mm, user_id="user-test", note_id="note-1")

    @pytest.mark.asyncio
    async def test_execute_confirmed_not_found(self, mock_ops):
        mm = MagicMock()
        mock_ops.delete_note.return_value = False
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(ctx, note_id="missing")

        assert result.success is False
        assert "could not be deleted" in result.error

    @pytest.mark.asyncio
    async def test_execute_confirmed_handles_error(self, mock_ops):
        mm = MagicMock()
        mock_ops.delete_note.side_effect = RuntimeError("db error")
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(ctx, note_id="note-1")

        assert result.success is False
        assert "db error" in result.error


# ---------------------------------------------------------------------------
# UpdateMemoryTool
# ---------------------------------------------------------------------------

class TestUpdateMemoryTool:
    def _tool(self):
        return UpdateMemoryTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "update_memory"
        assert tool.requires_approval is True
        schema = tool.to_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "note_id" in props
        assert "scope" in props
        assert "key" in props
        assert "scope_ref" in props
        assert "new_key" in props
        assert "content" in props
        assert schema["function"]["parameters"]["required"] == []

    def test_has_hint(self):
        tool = self._tool()
        assert tool.hint
        assert "scope" in tool.hint.lower()
        assert "key" in tool.hint.lower()

    @pytest.mark.asyncio
    async def test_execute_returns_preview_for_content_change(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.get_note.return_value = note
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, note_id="note-1", content="new content")

        assert result.success is True
        data = json.loads(result.data)
        assert data["action"] == "update_memory"
        assert data["old"]["content"] == "User prefers cinematic lighting"
        assert data["new"]["content"] == "new content"
        assert data["old"]["key"] == data["new"]["key"] == "pref_style"

        assert isinstance(result.preview, ToolApprovalPreview)
        assert result.preview.action == "Edit memory note"
        assert any("new content" in item for item in result.preview.items)
        mock_ops.get_note.assert_called_once_with(
            mm, user_id="user-test", note_id="note-1")

    @pytest.mark.asyncio
    async def test_execute_returns_preview_for_key_change(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.get_note.return_value = note
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, note_id="note-1", new_key="renamed_key")

        assert result.success is True
        data = json.loads(result.data)
        assert data["old"]["key"] == "pref_style"
        assert data["new"]["key"] == "renamed_key"
        assert data["new"]["content"] == data["old"]["content"]
        assert any("renamed_key" in item for item in result.preview.items)

    @pytest.mark.asyncio
    async def test_execute_unknown_note_id(self, mock_ops):
        mm = MagicMock()
        mock_ops.get_note.return_value = None
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, note_id="missing", content="new content")

        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_execute_no_addressing_given(self, mock_ops):
        mm = MagicMock()
        ctx = make_context(llm_memory_repository=mm)
        result = await self._tool().execute(ctx, content="new content")
        assert result.success is False
        assert "note_id" in result.error
        assert "scope" in result.error
        mock_ops.get_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_resolves_by_scope_and_key(self, mock_ops):
        note = make_note(id="note-9", scope="global")
        mm = MagicMock()
        mock_ops.get_note_by_key.return_value = note
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, scope="global", key="pref_style", content="new content")

        assert result.success is True
        data = json.loads(result.data)
        assert data["note_id"] == "note-9"
        mock_ops.get_note_by_key.assert_called_once_with(
            mm, user_id="user-test", key="pref_style", scope="global", scope_ref=None,
        )
        mock_ops.get_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_scope_and_key_auto_resolves_scope_ref(self, mock_ops):
        note = make_note(id="note-9", scope="preset", scope_ref="preset-1")
        mm = MagicMock()
        mock_ops.get_note_by_key.return_value = note
        ctx = make_context(
            llm_memory_repository=mm,
            session_metadata={"form_state": {"preset": "preset-1", "form_data": {}}},
        )

        result = await self._tool().execute(ctx, scope="preset", key="quirk", content="new content")

        assert result.success is True
        mock_ops.get_note_by_key.assert_called_once_with(
            mm, user_id="user-test", key="quirk", scope="preset", scope_ref="preset-1",
        )

    @pytest.mark.asyncio
    async def test_execute_scope_and_key_not_found(self, mock_ops):
        mm = MagicMock()
        mock_ops.get_note_by_key.return_value = None
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, scope="global", key="missing_key", content="x")

        assert result.success is False
        assert "missing_key" in result.error
        assert "global" in result.error

    @pytest.mark.asyncio
    async def test_execute_scope_and_key_invalid_scope_propagates_manager_error(self, mock_ops):
        mm = MagicMock()
        mock_ops.get_note_by_key.side_effect = ValueError("Invalid scope 'bogus'. Must be one of: global, model, preset")
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, scope="bogus", key="k", content="x")

        assert result.success is False
        assert "Invalid scope" in result.error

    @pytest.mark.asyncio
    async def test_execute_no_changes_given(self, mock_ops):
        mm = MagicMock()
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, note_id="note-1")

        assert result.success is False
        assert "at least one" in result.error.lower()
        mock_ops.get_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_no_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, note_id="note-1", content="x")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_execute_fetch_error(self, mock_ops):
        mm = MagicMock()
        mock_ops.get_note.side_effect = RuntimeError("db error")
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute(ctx, note_id="note-1", content="x")

        assert result.success is False
        assert "db error" in result.error

    @pytest.mark.asyncio
    async def test_execute_confirmed_updates(self, mock_ops):
        note = make_note()
        updated_note = make_note(key="pref_style", content="new content")
        mm = MagicMock()
        mock_ops.get_note.return_value = note
        mock_ops.update_note.return_value = updated_note
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(ctx, note_id="note-1", content="new content")

        assert result.success is True
        data = json.loads(result.data)
        assert data["action"] == "update_memory"
        assert data["success"] is True
        assert data["note_id"] == "note-1"
        mock_ops.update_note.assert_called_once_with(
            mm, user_id="user-test",
            note_id="note-1",
            key="pref_style",
            content="new content",
        )

    @pytest.mark.asyncio
    async def test_execute_confirmed_keeps_unspecified_fields(self, mock_ops):
        note = make_note()
        updated_note = make_note(key="renamed_key")
        mm = MagicMock()
        mock_ops.get_note.return_value = note
        mock_ops.update_note.return_value = updated_note
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(ctx, note_id="note-1", new_key="renamed_key")

        assert result.success is True
        mock_ops.update_note.assert_called_once_with(
            mm, user_id="user-test",
            note_id="note-1",
            key="renamed_key",
            content=note.content,
        )

    @pytest.mark.asyncio
    async def test_execute_confirmed_resolves_by_scope_and_key(self, mock_ops):
        note = make_note(id="note-9", scope="global")
        updated_note = make_note(id="note-9", content="new content")
        mm = MagicMock()
        mock_ops.get_note_by_key.return_value = note
        mock_ops.update_note.return_value = updated_note
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(
            ctx, scope="global", key="pref_style", content="new content",
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["note_id"] == "note-9"
        mock_ops.update_note.assert_called_once_with(
            mm, user_id="user-test", note_id="note-9", key="pref_style", content="new content",
        )

    @pytest.mark.asyncio
    async def test_execute_confirmed_unknown_note_id(self, mock_ops):
        mm = MagicMock()
        mock_ops.get_note.return_value = None
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(ctx, note_id="missing", content="x")

        assert result.success is False
        assert "not found" in result.error
        mock_ops.update_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_confirmed_not_found_by_manager(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.get_note.return_value = note
        mock_ops.update_note.return_value = None
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(ctx, note_id="note-1", content="x")

        assert result.success is False
        assert "could not be updated" in result.error

    @pytest.mark.asyncio
    async def test_execute_confirmed_handles_error(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.get_note.return_value = note
        mock_ops.update_note.side_effect = RuntimeError("db error")
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(ctx, note_id="note-1", content="x")

        assert result.success is False
        assert "db error" in result.error

    @pytest.mark.asyncio
    async def test_execute_confirmed_content_over_limit_raises_teaching_error(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.get_note.return_value = note
        mock_ops.update_note.side_effect = ValueError(
            "Memory content is limited to 500 characters - distill the durable fact; "
            "details belong in the conversation."
        )
        ctx = make_context(llm_memory_repository=mm)

        result = await self._tool().execute_confirmed(ctx, note_id="note-1", content="x" * 501)

        assert result.success is False
        assert "500 characters" in result.error

    @pytest.mark.asyncio
    async def test_execute_scopes_fetch_by_user_id(self, mock_ops):
        note = make_note()
        mm = MagicMock()
        mock_ops.get_note.return_value = note
        ctx = ToolContext(user_id="another-user", llm_memory_repository=mm)

        await self._tool().execute(ctx, note_id="note-1", content="x")

        mock_ops.get_note.assert_called_once_with(
            mm, user_id="another-user", note_id="note-1")
