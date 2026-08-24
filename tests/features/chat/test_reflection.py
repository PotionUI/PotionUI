"""Tests for ChatReflectionGenerator: trigger gating, tolerant JSON parsing,
validation pass-through, and reflected-up-to bookkeeping."""

import pytest
from unittest.mock import Mock, AsyncMock

from src.features.chat.reflection import (
    ChatReflectionGenerator,
    MIN_UNREFLECTED_USER_MESSAGES,
)


def _message(role: str, content: str, msg_id: str = "m") -> Mock:
    msg = Mock()
    msg.id = msg_id
    msg.role = role
    msg.content = content
    return msg


def _messages(user_count: int, reflected_up_to: str = None) -> list:
    """A user/assistant transcript with `user_count` user turns, IDs 'm0'.. in order."""
    out = []
    idx = 0
    if reflected_up_to:
        out.append(_message("assistant", "old context", msg_id=reflected_up_to))
        idx += 1
    for i in range(user_count):
        out.append(_message("user", f"question {i}", msg_id=f"u{i}"))
        out.append(_message("assistant", f"answer {i}", msg_id=f"a{i}"))
    return out


def _session(llm_config_id="llm-1", metadata=None) -> Mock:
    session = Mock()
    session.id = "session-1"
    session.user_id = "user-1"
    session.llm_config_id = llm_config_id
    session.metadata = metadata or {}
    return session


def _manager(config_memory_reflection=True):
    """A minimal stand-in for ChatManager: llm_service, chat_repository, llm_memory_manager."""
    manager = Mock()
    manager.llm_service = Mock()
    manager.llm_service.repository = Mock()
    config = Mock()
    config.memory_reflection = config_memory_reflection
    manager.llm_service.repository.get_configuration.return_value = config
    manager.chat_repository = Mock()
    manager.llm_memory_manager = Mock()
    return manager


class TestShouldReflect:
    def test_false_below_message_threshold(self):
        manager = _manager()
        generator = ChatReflectionGenerator(manager)
        session = _session()
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES - 1)) is False

    def test_true_at_message_threshold(self):
        manager = _manager()
        generator = ChatReflectionGenerator(manager)
        session = _session()
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES)) is True

    def test_false_when_toggle_off(self):
        manager = _manager(config_memory_reflection=False)
        generator = ChatReflectionGenerator(manager)
        session = _session()
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES)) is False

    def test_false_when_no_memory_manager(self):
        manager = _manager()
        manager.llm_memory_manager = None
        generator = ChatReflectionGenerator(manager)
        session = _session()
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES)) is False

    def test_false_when_no_llm_config(self):
        manager = _manager()
        generator = ChatReflectionGenerator(manager)
        session = _session(llm_config_id=None)
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES)) is False

    def test_only_counts_messages_after_last_reflection(self):
        manager = _manager()
        generator = ChatReflectionGenerator(manager)
        session = _session(metadata={"memory_reflection": {"reflected_up_to_message_id": "a1"}})
        # 3 user turns total, but the reflection marker sits after the 2nd -
        # only the 3rd is unreflected.
        messages = _messages(3)
        marker_index = next(i for i, m in enumerate(messages) if m.id == "a1")
        assert marker_index >= 0
        assert generator.should_reflect(session, messages) is False


class TestParseItems:
    def test_parses_clean_json_array(self):
        items = ChatReflectionGenerator._parse_items(
            '[{"scope": "global", "key": "likes_anime", "content": "prefers anime style"}]'
        )
        assert items == [{"scope": "global", "key": "likes_anime", "content": "prefers anime style"}]

    def test_tolerates_surrounding_prose_and_think_blocks(self):
        raw = (
            "<think>let me consider this</think>Sure, here you go:\n"
            '[{"scope": "global", "key": "k", "content": "c"}]\n'
            "Hope that helps!"
        )
        items = ChatReflectionGenerator._parse_items(raw)
        assert items == [{"scope": "global", "key": "k", "content": "c"}]

    def test_empty_array_returns_empty_list(self):
        assert ChatReflectionGenerator._parse_items("[]") == []

    def test_malformed_json_returns_empty_list(self):
        assert ChatReflectionGenerator._parse_items("[{not json}]") == []

    def test_non_array_json_returns_empty_list(self):
        assert ChatReflectionGenerator._parse_items('{"scope": "global"}') == []

    def test_none_input_returns_empty_list(self):
        assert ChatReflectionGenerator._parse_items(None) == []

    def test_drops_non_dict_array_entries(self):
        items = ChatReflectionGenerator._parse_items('["not a dict", {"key": "k", "content": "c"}]')
        assert items == [{"key": "k", "content": "c"}]


class TestReflect:
    def _setup(self, response_content, memory_reflection=True):
        manager = _manager(config_memory_reflection=memory_reflection)
        session = _session()
        messages = _messages(MIN_UNREFLECTED_USER_MESSAGES)
        manager.chat_repository.get_session.return_value = session
        manager.chat_repository.get_messages.return_value = messages
        manager.chat_repository.record_memory_reflection.return_value = True

        response = Mock()
        response.content = response_content
        manager.llm_service.generate_with_history = AsyncMock(return_value=response)

        saved_note = Mock()
        saved_note.to_dict.return_value = {"key": "saved"}
        manager.llm_memory_manager.write_note.return_value = saved_note

        return manager, ChatReflectionGenerator(manager), messages

    @pytest.mark.asyncio
    async def test_happy_path_persists_items_and_records_bookkeeping(self):
        manager, generator, messages = self._setup(
            '[{"scope": "global", "key": "likes anime", "content": "prefers anime style over realism"}]'
        )

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "saved"}]
        manager.llm_memory_manager.write_note.assert_called_once_with(
            user_id="user-1", key="likes_anime",
            content="prefers anime style over realism",
            scope="global", scope_ref=None,
        )
        manager.chat_repository.record_memory_reflection.assert_called_once_with(
            "session-1", messages[-1].id,
        )

    @pytest.mark.asyncio
    async def test_invalid_items_are_dropped_not_fatal(self):
        """A seed-tainted item is rejected by validation but doesn't blow up the pass."""
        manager, generator, messages = self._setup(
            '[{"scope": "global", "key": "seed_note", "content": "castle at seed 1234"}, '
            '{"scope": "global", "key": "good", "content": "prefers moody lighting"}]'
        )

        def write_note(user_id, key, content, scope, scope_ref=None):
            if "seed" in content:
                raise ValueError("Memory note rejected: one generation")
            note = Mock()
            note.to_dict.return_value = {"key": key}
            return note

        manager.llm_memory_manager.write_note.side_effect = write_note

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "good"}]

    @pytest.mark.asyncio
    async def test_sloppy_json_is_tolerated(self):
        manager, generator, messages = self._setup(
            'Sure! [{"scope": "global", "key": "k", "content": "prefers dark fantasy over anime"}] done.'
        )

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "saved"}]

    @pytest.mark.asyncio
    async def test_empty_array_saves_nothing_but_still_records_bookkeeping(self):
        manager, generator, messages = self._setup("[]")

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.llm_memory_manager.write_note.assert_not_called()
        manager.chat_repository.record_memory_reflection.assert_called_once()

    @pytest.mark.asyncio
    async def test_toggle_off_makes_no_llm_call(self):
        manager, generator, messages = self._setup(
            '[{"scope": "global", "key": "k", "content": "c"}]', memory_reflection=False,
        )

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.llm_service.generate_with_history.assert_not_called()
        manager.chat_repository.record_memory_reflection.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_threshold_makes_no_llm_call(self):
        manager = _manager()
        session = _session()
        manager.chat_repository.get_session.return_value = session
        manager.chat_repository.get_messages.return_value = _messages(MIN_UNREFLECTED_USER_MESSAGES - 1)
        generator = ChatReflectionGenerator(manager)

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.llm_service.generate_with_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty_and_does_not_raise(self):
        manager, generator, messages = self._setup("irrelevant")
        manager.llm_service.generate_with_history = AsyncMock(side_effect=RuntimeError("provider down"))

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.chat_repository.record_memory_reflection.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_session_returns_empty(self):
        manager = _manager()
        manager.chat_repository.get_session.return_value = None
        generator = ChatReflectionGenerator(manager)

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.llm_service.generate_with_history.assert_not_called()


class TestReflectScoping:
    """Scope/scope_ref validation: only the exact id resolved from this turn's
    form state is ever honored for a 'preset'/'model' note."""

    def _setup(self, response_content):
        manager = _manager()
        session = _session()
        messages = _messages(MIN_UNREFLECTED_USER_MESSAGES)
        manager.chat_repository.get_session.return_value = session
        manager.chat_repository.get_messages.return_value = messages
        manager.chat_repository.record_memory_reflection.return_value = True

        response = Mock()
        response.content = response_content
        manager.llm_service.generate_with_history = AsyncMock(return_value=response)

        manager.preset_manager.get_preset.return_value = {"name": "My Preset"}
        model = Mock()
        model.filename = "my_model.safetensors"
        manager.model_index_manager.model_repo.get_by_id.return_value = model

        saved_note = Mock()
        saved_note.to_dict.return_value = {"key": "saved"}
        manager.llm_memory_manager.write_note.return_value = saved_note
        # Compaction rides along after a persist; keep it a no-op so these
        # tests only exercise scoping, not compaction (read_notes returns []).
        manager.llm_memory_manager.read_notes.return_value = []

        return manager, ChatReflectionGenerator(manager), messages

    @pytest.mark.asyncio
    async def test_valid_preset_scope_ref_accepted(self):
        manager, generator, messages = self._setup(
            '[{"scope": "preset", "scope_ref": "preset-123", "key": "k", '
            '"content": "always uses this preset for portraits"}]'
        )

        await generator.reflect("session-1", form_state={"preset": "preset-123", "form_data": {}})

        manager.llm_memory_manager.write_note.assert_called_once_with(
            user_id="user-1", key="k",
            content="always uses this preset for portraits",
            scope="preset", scope_ref="preset-123",
        )

    @pytest.mark.asyncio
    async def test_valid_model_scope_ref_accepted(self):
        manager, generator, messages = self._setup(
            '[{"scope": "model", "scope_ref": "model-1", "key": "k", '
            '"content": "always adds a LoRA with this model"}]'
        )

        await generator.reflect(
            "session-1",
            form_state={"form_data": {"checkpoint": "model:model-1"}},
        )

        manager.llm_memory_manager.write_note.assert_called_once_with(
            user_id="user-1", key="k",
            content="always adds a LoRA with this model",
            scope="model", scope_ref="model-1",
        )

    @pytest.mark.asyncio
    async def test_hallucinated_scope_ref_falls_back_to_global(self):
        manager, generator, messages = self._setup(
            '[{"scope": "preset", "scope_ref": "preset-999", "key": "k", '
            '"content": "made up preference for a preset never active here"}]'
        )

        await generator.reflect("session-1", form_state={"preset": "preset-123", "form_data": {}})

        manager.llm_memory_manager.write_note.assert_called_once_with(
            user_id="user-1", key="k",
            content="made up preference for a preset never active here",
            scope="global", scope_ref=None,
        )

    @pytest.mark.asyncio
    async def test_no_form_state_everything_lands_global(self):
        manager, generator, messages = self._setup(
            '[{"scope": "preset", "scope_ref": "preset-123", "key": "k", '
            '"content": "a preference reported with no active context at all"}]'
        )

        await generator.reflect("session-1")

        manager.llm_memory_manager.write_note.assert_called_once_with(
            user_id="user-1", key="k",
            content="a preference reported with no active context at all",
            scope="global", scope_ref=None,
        )


class TestValidateScope:
    def test_matching_preset_ref_kept(self):
        assert ChatReflectionGenerator._validate_scope("preset", "p1", "p1", None) == ("preset", "p1")

    def test_mismatched_preset_ref_falls_back(self):
        assert ChatReflectionGenerator._validate_scope("preset", "p2", "p1", None) == ("global", None)

    def test_matching_model_ref_kept(self):
        assert ChatReflectionGenerator._validate_scope("model", "m1", None, "m1") == ("model", "m1")

    def test_preset_scope_with_no_active_preset_falls_back(self):
        assert ChatReflectionGenerator._validate_scope("preset", "p1", None, None) == ("global", None)

    def test_invalid_scope_name_falls_back(self):
        assert ChatReflectionGenerator._validate_scope("banana", "p1", "p1", None) == ("global", None)
