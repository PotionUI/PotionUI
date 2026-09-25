from unittest.mock import Mock

from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.chat.runtime import ChatRuntime
from src.features.llm_memory.records import LLMMemoryNote


class FakeMemoryRepository:
    def __init__(self, notes):
        self.notes = notes

    def list_notes(self, user_id, scope=None, scope_ref=None):
        return [
            n for n in self.notes
            if n.user_id == user_id
            and (scope is None or n.scope == scope)
            and (scope_ref is None or n.scope_ref == scope_ref)
        ]


def _note(key, content, scope="global", scope_ref=None, user_id="user-1"):
    return LLMMemoryNote(id=f"id-{key}", user_id=user_id, key=key, content=content, scope=scope, scope_ref=scope_ref)


def _runtime(notes):
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return ChatRuntime(
        chat_repository=Mock(),
        llm_service=Mock(),
        response_processor=Mock(),
        plugin_registry=Mock(),
        chat_mode_registry=registry,
        llm_memory_repository=FakeMemoryRepository(notes),
        model_index_manager=Mock(),
    )


NOTES = [
    _note("g", "global fact"),
    _note("alpha", "castle series uses warm dusk palette", scope="session", scope_ref="sess-A"),
    _note("beta", "portrait set keeps shallow focus", scope="session", scope_ref="sess-B"),
]


def _inject(runtime, form_state):
    history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
    metadata = {"form_state": form_state} if form_state is not None else None
    result = runtime._context.inject_memory_block(history, metadata, user_id="user-1")
    return history, result


def test_session_notes_injected_only_with_session_id():
    runtime = _runtime(NOTES)

    history, result = _inject(runtime, {"session_id": None})

    block = history[-1]["content"]
    assert "[this session]" not in block
    assert "warm dusk" not in block
    assert result["by_scope"]["session"] == 0


def test_session_group_rendered_in_user_turn_and_counted():
    runtime = _runtime(NOTES)

    history, result = _inject(runtime, {"session_id": "sess-A"})

    assert history[0] == {"role": "system", "content": "sys"}
    assert len(history) == 2
    user_turn = history[-1]
    assert user_turn["role"] == "user"
    assert user_turn["content"].startswith("hello")
    assert "[this session]" in user_turn["content"]
    assert "alpha: castle series uses warm dusk palette" in user_turn["content"]
    assert "shallow focus" not in user_turn["content"]
    assert result["by_scope"]["session"] == 1
    assert "id-alpha" in result["note_ids"]


def test_different_sessions_surface_different_notes():
    runtime = _runtime(NOTES)

    history_b, result_b = _inject(runtime, {"session_id": "sess-B"})

    block = history_b[-1]["content"]
    assert "shallow focus" in block
    assert "warm dusk" not in block
    assert result_b["note_ids"] == ["id-g", "id-beta"]


def test_session_group_follows_mode_group_and_reports_dropped():
    many = [_note(f"s{i}", f"session note {i}", scope="session", scope_ref="sess-A") for i in range(25)]
    runtime = _runtime([_note("g", "global fact"), *many])

    history, result = _inject(runtime, {"session_id": "sess-A"})

    block = history[-1]["content"]
    assert block.index("[global]") < block.index("[this session]")
    assert result["by_scope_dropped"]["session"] == 5
