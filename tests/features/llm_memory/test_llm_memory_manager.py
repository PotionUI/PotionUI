"""Tests for LLMMemoryManager."""

import pytest
from unittest.mock import MagicMock

from src.features.llm_memory.manager import LLMMemoryManager
from src.features.llm_memory.records import LLMMemoryNote


def make_note(**overrides):
    defaults = {
        "id": "note-1",
        "user_id": "user-1",
        "key": "test_key",
        "content": "test content",
        "scope": "global",
        "scope_ref": None,
    }
    defaults.update(overrides)
    return LLMMemoryNote(**defaults)


class TestWriteNote:
    def test_writes_global_note(self):
        repo = MagicMock()
        note = make_note()
        repo.upsert.return_value = note
        manager = LLMMemoryManager(repository=repo)

        result = manager.write_note(
            user_id="user-1",
            key="test_key",
            content="test content",
        )

        assert result == note
        repo.upsert.assert_called_once()
        call_arg = repo.upsert.call_args[0][0]
        assert call_arg.user_id == "user-1"
        assert call_arg.key == "test_key"
        assert call_arg.content == "test content"
        assert call_arg.scope == "global"
        assert call_arg.scope_ref is None

    def test_writes_model_scoped_note(self):
        repo = MagicMock()
        note = make_note(scope="model", scope_ref="model-1")
        repo.upsert.return_value = note
        manager = LLMMemoryManager(repository=repo)

        result = manager.write_note(
            user_id="user-1",
            key="model_quirk",
            content="Needs low CFG",
            scope="model",
            scope_ref="model-1",
        )

        assert result == note
        call_arg = repo.upsert.call_args[0][0]
        assert call_arg.scope == "model"
        assert call_arg.scope_ref == "model-1"

    def test_writes_preset_scoped_note(self):
        repo = MagicMock()
        note = make_note(scope="preset", scope_ref="preset-1")
        repo.upsert.return_value = note
        manager = LLMMemoryManager(repository=repo)

        result = manager.write_note(
            user_id="user-1",
            key="preset_quirk",
            content="Prefers 30 steps",
            scope="preset",
            scope_ref="preset-1",
        )

        assert result == note
        call_arg = repo.upsert.call_args[0][0]
        assert call_arg.scope == "preset"
        assert call_arg.scope_ref == "preset-1"

    def test_model_scope_requires_scope_ref(self):
        repo = MagicMock()
        manager = LLMMemoryManager(repository=repo)

        with pytest.raises(ValueError, match="scope_ref is required"):
            manager.write_note(
                user_id="user-1",
                key="test",
                content="test",
                scope="model",
            )

        repo.upsert.assert_not_called()

    def test_preset_scope_requires_scope_ref(self):
        repo = MagicMock()
        manager = LLMMemoryManager(repository=repo)

        with pytest.raises(ValueError, match="scope_ref is required"):
            manager.write_note(
                user_id="user-1",
                key="test",
                content="test",
                scope="preset",
            )

        repo.upsert.assert_not_called()

    def test_invalid_scope_raises_error(self):
        repo = MagicMock()
        manager = LLMMemoryManager(repository=repo)

        with pytest.raises(ValueError, match="Invalid scope"):
            manager.write_note(
                user_id="user-1",
                key="test",
                content="test",
                scope="invalid",
            )

        repo.upsert.assert_not_called()

    def test_content_over_limit_raises_teaching_error(self):
        repo = MagicMock()
        manager = LLMMemoryManager(repository=repo)

        with pytest.raises(ValueError, match="500 characters"):
            manager.write_note(
                user_id="user-1",
                key="test",
                content="x" * 501,
            )

        repo.upsert.assert_not_called()

    def test_content_at_limit_passes(self):
        repo = MagicMock()
        note = make_note(content="x" * 500)
        repo.upsert.return_value = note
        manager = LLMMemoryManager(repository=repo)

        result = manager.write_note(user_id="user-1", key="test", content="x" * 500)

        assert result == note
        repo.upsert.assert_called_once()

    def test_global_scope_clears_scope_ref(self):
        repo = MagicMock()
        note = make_note()
        repo.upsert.return_value = note
        manager = LLMMemoryManager(repository=repo)

        manager.write_note(
            user_id="user-1",
            key="test",
            content="test",
            scope="global",
            scope_ref="should-be-cleared",
        )

        call_arg = repo.upsert.call_args[0][0]
        assert call_arg.scope_ref is None


class TestReadNotes:
    def test_reads_all_notes(self):
        repo = MagicMock()
        notes = [make_note(id="n1"), make_note(id="n2")]
        repo.list_notes.return_value = notes
        manager = LLMMemoryManager(repository=repo)

        result = manager.read_notes(user_id="user-1")

        assert result == notes
        repo.list_notes.assert_called_once_with(
            user_id="user-1", scope=None, scope_ref=None
        )

    def test_reads_with_scope_filter(self):
        repo = MagicMock()
        repo.list_notes.return_value = []
        manager = LLMMemoryManager(repository=repo)

        manager.read_notes(user_id="user-1", scope="global")

        repo.list_notes.assert_called_once_with(
            user_id="user-1", scope="global", scope_ref=None
        )

    def test_reads_with_model_filter(self):
        repo = MagicMock()
        repo.list_notes.return_value = []
        manager = LLMMemoryManager(repository=repo)

        manager.read_notes(user_id="user-1", scope="model", scope_ref="m-1")

        repo.list_notes.assert_called_once_with(
            user_id="user-1", scope="model", scope_ref="m-1"
        )

    def test_reads_with_preset_filter(self):
        repo = MagicMock()
        repo.list_notes.return_value = []
        manager = LLMMemoryManager(repository=repo)

        manager.read_notes(user_id="user-1", scope="preset", scope_ref="p-1")

        repo.list_notes.assert_called_once_with(
            user_id="user-1", scope="preset", scope_ref="p-1"
        )


class TestGetNote:
    def test_returns_note(self):
        repo = MagicMock()
        note = make_note()
        repo.get_by_id.return_value = note
        manager = LLMMemoryManager(repository=repo)

        result = manager.get_note(user_id="user-1", note_id="note-1")

        assert result == note
        repo.get_by_id.assert_called_once_with("note-1", "user-1")

    def test_returns_none_when_not_found(self):
        repo = MagicMock()
        repo.get_by_id.return_value = None
        manager = LLMMemoryManager(repository=repo)

        result = manager.get_note(user_id="user-1", note_id="missing")

        assert result is None


class TestUpdateNote:
    def test_updates_note(self):
        repo = MagicMock()
        note = make_note(key="new_key", content="new content")
        repo.update.return_value = note
        manager = LLMMemoryManager(repository=repo)

        result = manager.update_note(user_id="user-1", note_id="note-1", key="new_key", content="new content")

        assert result == note
        repo.update.assert_called_once_with("note-1", "user-1", "new_key", "new content")

    def test_returns_none_when_not_found(self):
        repo = MagicMock()
        repo.update.return_value = None
        manager = LLMMemoryManager(repository=repo)

        result = manager.update_note(user_id="user-1", note_id="missing", key="k", content="c")

        assert result is None

    def test_content_over_limit_raises_teaching_error(self):
        repo = MagicMock()
        manager = LLMMemoryManager(repository=repo)

        with pytest.raises(ValueError, match="500 characters"):
            manager.update_note(user_id="user-1", note_id="note-1", key="k", content="x" * 501)

        repo.update.assert_not_called()


class TestGetNoteByKey:
    def test_resolves_global_note(self):
        repo = MagicMock()
        note = make_note()
        repo.get_by_key.return_value = note
        manager = LLMMemoryManager(repository=repo)

        result = manager.get_note_by_key(user_id="user-1", key="test_key", scope="global")

        assert result == note
        repo.get_by_key.assert_called_once_with("user-1", "test_key", "global", None)

    def test_resolves_scoped_note_with_scope_ref(self):
        repo = MagicMock()
        note = make_note(scope="model", scope_ref="model-1")
        repo.get_by_key.return_value = note
        manager = LLMMemoryManager(repository=repo)

        result = manager.get_note_by_key(
            user_id="user-1", key="quirk", scope="model", scope_ref="model-1",
        )

        assert result == note
        repo.get_by_key.assert_called_once_with("user-1", "quirk", "model", "model-1")

    def test_returns_none_on_miss(self):
        repo = MagicMock()
        repo.get_by_key.return_value = None
        manager = LLMMemoryManager(repository=repo)

        result = manager.get_note_by_key(user_id="user-1", key="missing", scope="global")

        assert result is None

    def test_invalid_scope_raises_error(self):
        repo = MagicMock()
        manager = LLMMemoryManager(repository=repo)

        with pytest.raises(ValueError, match="Invalid scope"):
            manager.get_note_by_key(user_id="user-1", key="k", scope="bogus")

        repo.get_by_key.assert_not_called()

    def test_preset_scope_requires_scope_ref(self):
        repo = MagicMock()
        manager = LLMMemoryManager(repository=repo)

        with pytest.raises(ValueError, match="scope_ref is required"):
            manager.get_note_by_key(user_id="user-1", key="k", scope="preset")

        repo.get_by_key.assert_not_called()

    def test_model_scope_rejects_empty_string_scope_ref(self):
        repo = MagicMock()
        manager = LLMMemoryManager(repository=repo)

        with pytest.raises(ValueError, match="scope_ref is required"):
            manager.get_note_by_key(user_id="user-1", key="k", scope="model", scope_ref="")

        repo.get_by_key.assert_not_called()


class TestDeleteNote:
    def test_deletes_note(self):
        repo = MagicMock()
        repo.delete.return_value = True
        manager = LLMMemoryManager(repository=repo)

        result = manager.delete_note(user_id="user-1", note_id="note-1")

        assert result is True
        repo.delete.assert_called_once_with("note-1", "user-1")

    def test_returns_false_when_not_found(self):
        repo = MagicMock()
        repo.delete.return_value = False
        manager = LLMMemoryManager(repository=repo)

        result = manager.delete_note(user_id="user-1", note_id="missing")

        assert result is False
