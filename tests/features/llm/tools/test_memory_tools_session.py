import json

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.memory_tool import ReadMemoryTool, UpdateMemoryTool, WriteMemoryTool
from src.features.llm_memory.records import LLMMemoryNote

NO_SESSION = "No saved session in the active tab — save the session first."


class FakeMemoryRepository:
    def __init__(self, notes=None):
        self.notes = list(notes or [])

    def upsert(self, note):
        self.notes.append(note)
        return note

    def list_notes(self, user_id, scope=None, scope_ref=None):
        return [
            n for n in self.notes
            if n.user_id == user_id
            and (scope is None or n.scope == scope)
            and (scope_ref is None or n.scope_ref == scope_ref)
        ]

    def get_by_key(self, user_id, key, scope, scope_ref=None):
        matches = [n for n in self.list_notes(user_id, scope, scope_ref) if n.key == key]
        return matches[0] if matches else None


def _ctx(repo, session_id=None):
    form_state = {"preset": "preset-1", "form_data": {}, "session_id": session_id}
    return ToolContext(user_id="u1", llm_memory_repository=repo, session_metadata={"form_state": form_state})


def _note(key, ref):
    return LLMMemoryNote(id=f"id-{key}-{ref}", user_id="u1", key=key, content=f"{key} text", scope="session", scope_ref=ref)


@pytest.mark.parametrize("tool_cls", [WriteMemoryTool, ReadMemoryTool, UpdateMemoryTool])
def test_session_in_scope_enum(tool_cls):
    assert "session" in tool_cls().parameters["properties"]["scope"]["enum"]


@pytest.mark.asyncio
async def test_write_session_scope_resolves_active_session_and_ignores_passed_ref():
    repo = FakeMemoryRepository()

    result = await WriteMemoryTool().execute(
        _ctx(repo, "sess-A"), key="palette", content="keeps a warm dusk palette", scope="session", scope_ref="sess-Z",
    )

    assert result.success, result.error
    assert [(n.scope, n.scope_ref) for n in repo.notes] == [("session", "sess-A")]


@pytest.mark.asyncio
async def test_write_session_scope_without_saved_session_asks_to_save_first():
    repo = FakeMemoryRepository()

    result = await WriteMemoryTool().execute(_ctx(repo, None), key="palette", content="keeps warm tones", scope="session")

    assert not result.success
    assert result.error == NO_SESSION
    assert repo.notes == []


@pytest.mark.asyncio
async def test_read_session_scope_filters_to_active_session():
    repo = FakeMemoryRepository([_note("a", "sess-A"), _note("b", "sess-B")])

    result = await ReadMemoryTool().execute(_ctx(repo, "sess-A"), scope="session")

    assert result.success
    assert [n["key"] for n in json.loads(result.data)["notes"]] == ["a"]


@pytest.mark.asyncio
async def test_read_all_includes_only_active_session_notes():
    repo = FakeMemoryRepository([_note("a", "sess-A"), _note("b", "sess-B")])

    result = await ReadMemoryTool().execute(_ctx(repo, "sess-B"), scope="all")

    assert [n["key"] for n in json.loads(result.data)["notes"]] == ["b"]


@pytest.mark.asyncio
async def test_read_session_scope_without_session_never_lists_every_session():
    repo = FakeMemoryRepository([_note("a", "sess-A"), _note("b", "sess-B")])

    result = await ReadMemoryTool().execute(_ctx(repo, None), scope="session")

    assert not result.success
    assert result.error == NO_SESSION


@pytest.mark.asyncio
async def test_update_session_scope_without_session_asks_to_save_first():
    repo = FakeMemoryRepository([_note("a", "sess-A")])

    result = await UpdateMemoryTool().execute(_ctx(repo, None), scope="session", key="a", content="new text")

    assert not result.success
    assert result.error == NO_SESSION


@pytest.mark.asyncio
async def test_update_session_scope_addresses_note_in_active_session():
    repo = FakeMemoryRepository([_note("a", "sess-A"), _note("a", "sess-B")])

    result = await UpdateMemoryTool().execute(_ctx(repo, "sess-B"), scope="session", key="a", content="new text")

    assert result.success, result.error
    assert json.loads(result.data)["note_id"] == "id-a-sess-B"
