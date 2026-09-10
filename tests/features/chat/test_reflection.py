"""Tests for ChatReflectionGenerator: trigger gating, tolerant JSON parsing,
validation pass-through, and reflected-up-to bookkeeping."""

import pytest
from unittest.mock import Mock, AsyncMock

from src.features.chat.reflection import (
    ChatReflectionGenerator,
    MIN_UNREFLECTED_USER_MESSAGES,
)


def _message(role: str, content: str, msg_id: str = "m", metadata: dict = None) -> Mock:
    msg = Mock()
    msg.id = msg_id
    msg.role = role
    msg.content = content
    # Explicit `None` default (never an auto-generated Mock attribute) -
    # `_build_span` reads `m.metadata.get("preset_id")` on a generation
    # session's user turns, and a Mock's implicit attribute would silently
    # look like a real (bogus) preset id there.
    msg.metadata = metadata
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


def _session(llm_config_id="llm-1", metadata=None, mode=None) -> Mock:
    session = Mock()
    session.id = "session-1"
    session.user_id = "user-1"
    session.llm_config_id = llm_config_id
    session.metadata = metadata or {}
    # None unless a test opts in - keeps offered-scope resolution inert
    # (neither GENERATION_MODE_ID nor a truthy plugin-mode id) in
    # `_resolve_active_context` for tests that aren't exercising preset/mode
    # scoping. Pass mode="generation" to exercise preset scoping, or any
    # other id to exercise plugin-mode scoping.
    session.mode = mode
    return session


def _manager(monkeypatch, config_memory_reflection=True):
    """A minimal stand-in for ChatRuntime: llm_service, chat_repository,
    llm_memory_repository. `memory_operations` (as imported into
    `reflection.py`) is patched to a fresh Mock, exposed as
    `manager.memory_ops`, so tests can assert on write_note/read_notes calls
    without exercising the real validation logic (covered separately by
    `tests/features/llm_memory/test_operations.py`)."""
    manager = Mock()
    manager.llm_service = Mock()
    manager.llm_service.repository = Mock()
    config = Mock()
    config.memory_reflection = config_memory_reflection
    manager.llm_service.repository.get_configuration.return_value = config
    manager.chat_repository = Mock()
    manager.llm_memory_repository = Mock()
    mock_ops = Mock()
    monkeypatch.setattr("src.features.chat.reflection.memory_operations", mock_ops)
    manager.memory_ops = mock_ops
    return manager


# A "recurring" item needs no verbatim quote - just >= 2 distinct evidence
# turn numbers - so most fixtures below use it as the least fussy way to
# clear the kind/evidence grounding gate without also having to match
# `_messages()`'s literal "question N" turn text.
def _recurring_item(scope="global", scope_ref=None, key="k", content="c"):
    item = {"scope": scope, "key": key, "content": content, "kind": "recurring", "evidence": [1, 2]}
    if scope_ref is not None:
        item["scope_ref"] = scope_ref
    return item


class TestShouldReflect:
    def test_false_below_message_threshold(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session()
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES - 1)) is False

    def test_true_at_message_threshold(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session()
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES)) is True

    def test_false_when_toggle_off(self, monkeypatch):
        manager = _manager(monkeypatch, config_memory_reflection=False)
        generator = ChatReflectionGenerator(manager)
        session = _session()
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES)) is False

    def test_false_when_no_memory_manager(self, monkeypatch):
        manager = _manager(monkeypatch)
        manager.llm_memory_repository = None
        generator = ChatReflectionGenerator(manager)
        session = _session()
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES)) is False

    def test_false_when_no_llm_config(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session(llm_config_id=None)
        assert generator.should_reflect(session, _messages(MIN_UNREFLECTED_USER_MESSAGES)) is False

    def test_only_counts_messages_after_last_reflection(self, monkeypatch):
        manager = _manager(monkeypatch)
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

    def test_empty_array_is_a_valid_empty_extraction(self):
        """A genuine `[]` is a real answer (nothing durable found) - distinct
        from the malformed/missing cases below, which return `None`."""
        assert ChatReflectionGenerator._parse_items("[]") == []

    def test_malformed_json_returns_none(self):
        assert ChatReflectionGenerator._parse_items("[{not json}]") is None

    def test_non_array_json_returns_none(self):
        assert ChatReflectionGenerator._parse_items('{"scope": "global"}') is None

    def test_none_input_returns_none(self):
        assert ChatReflectionGenerator._parse_items(None) is None

    def test_drops_non_dict_array_entries(self):
        items = ChatReflectionGenerator._parse_items('["not a dict", {"key": "k", "content": "c"}]')
        assert items == [{"key": "k", "content": "c"}]


class TestReflect:
    def _setup(self, monkeypatch, response_content, memory_reflection=True):
        manager = _manager(monkeypatch, config_memory_reflection=memory_reflection)
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
        manager.memory_ops.write_note.return_value = saved_note

        return manager, ChatReflectionGenerator(manager), messages

    @pytest.mark.asyncio
    async def test_happy_path_persists_items_and_records_bookkeeping(self, monkeypatch):
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "likes anime", "content": "prefers anime style over realism", '
            '"kind": "recurring", "evidence": [1, 2]}]'
        )

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "saved"}]
        manager.memory_ops.write_note.assert_called_once_with(
            manager.llm_memory_repository,
            user_id="user-1", key="likes_anime",
            content="prefers anime style over realism",
            scope="global", scope_ref=None,
        )
        manager.chat_repository.record_memory_reflection.assert_called_once_with(
            "session-1", messages[-1].id,
            # A message covered in FULL records its whole length as the
            # offset (never 0) - see `_ReflectionSpan`'s docstring.
            offset=len(messages[-1].content), seq=len(messages) - 1, pending_backlog=False,
        )

    @pytest.mark.asyncio
    async def test_invalid_items_are_dropped_not_fatal(self, monkeypatch):
        """A seed-tainted item is rejected by validation but doesn't blow up the pass."""
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "seed_note", "content": "castle at seed 1234", '
            '"kind": "recurring", "evidence": [1, 2]}, '
            '{"scope": "global", "key": "good", "content": "prefers moody lighting", '
            '"kind": "recurring", "evidence": [1, 2]}]'
        )

        def write_note(repo, user_id, key, content, scope, scope_ref=None):
            if "seed" in content:
                raise ValueError("Memory note rejected: one generation")
            note = Mock()
            note.to_dict.return_value = {"key": key}
            return note

        manager.memory_ops.write_note.side_effect = write_note

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "good"}]

    @pytest.mark.asyncio
    async def test_sloppy_json_is_tolerated(self, monkeypatch):
        manager, generator, messages = self._setup(
            monkeypatch,
            'Sure! [{"scope": "global", "key": "k", "content": "prefers dark fantasy over anime", '
            '"kind": "recurring", "evidence": [1, 2]}] done.'
        )

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "saved"}]

    @pytest.mark.asyncio
    async def test_empty_array_saves_nothing_but_still_records_bookkeeping(self, monkeypatch):
        manager, generator, messages = self._setup(monkeypatch, "[]")

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.memory_ops.write_note.assert_not_called()
        manager.chat_repository.record_memory_reflection.assert_called_once()

    @pytest.mark.asyncio
    async def test_toggle_off_makes_no_llm_call(self, monkeypatch):
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "k", "content": "c", "kind": "recurring", "evidence": [1, 2]}]',
            memory_reflection=False,
        )

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.llm_service.generate_with_history.assert_not_called()
        manager.chat_repository.record_memory_reflection.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_threshold_makes_no_llm_call(self, monkeypatch):
        manager = _manager(monkeypatch)
        session = _session()
        manager.chat_repository.get_session.return_value = session
        manager.chat_repository.get_messages.return_value = _messages(MIN_UNREFLECTED_USER_MESSAGES - 1)
        generator = ChatReflectionGenerator(manager)

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.llm_service.generate_with_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty_and_does_not_raise(self, monkeypatch):
        manager, generator, messages = self._setup(monkeypatch, "irrelevant")
        manager.llm_service.generate_with_history = AsyncMock(side_effect=RuntimeError("provider down"))

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.chat_repository.record_memory_reflection.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_session_returns_empty(self, monkeypatch):
        manager = _manager(monkeypatch)
        manager.chat_repository.get_session.return_value = None
        generator = ChatReflectionGenerator(manager)

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.llm_service.generate_with_history.assert_not_called()


class TestReflectExtractionQuality:
    """Acceptance-level checks for the kind/evidence grounding gate and the
    content-overlap backstop, exercised through the real `reflect()` path
    (span-building included) rather than the unit-level helpers below."""

    def _setup(self, monkeypatch, response_content, user_messages=None):
        manager = _manager(monkeypatch)
        session = _session()
        messages = user_messages if user_messages is not None else _messages(MIN_UNREFLECTED_USER_MESSAGES)
        manager.chat_repository.get_session.return_value = session
        manager.chat_repository.get_messages.return_value = messages
        manager.chat_repository.record_memory_reflection.return_value = True

        response = Mock()
        response.content = response_content
        manager.llm_service.generate_with_history = AsyncMock(return_value=response)

        saved_note = Mock()
        saved_note.to_dict.return_value = {"key": "saved"}
        manager.memory_ops.write_note.return_value = saved_note
        manager.memory_ops.read_notes.return_value = []  # compaction no-op

        return manager, ChatReflectionGenerator(manager)

    @pytest.mark.asyncio
    async def test_one_off_prompt_with_no_grounding_yields_no_note(self, monkeypatch):
        """A model that tries to smuggle a single generation's subject through
        as a fact, without a 'kind' at all, is dropped outright."""
        manager, generator = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "outfit", '
            '"content": "likes generating a knight in silver armor"}]',
        )

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_recurring_request_across_three_turns_yields_one_note(self, monkeypatch):
        manager, generator = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "wants_shorter_captions", '
            '"content": "prefers short prompts without quality tags", '
            '"kind": "recurring", "evidence": [1, 2, 3]}]',
        )

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "saved"}]
        manager.memory_ops.write_note.assert_called_once()

    @pytest.mark.asyncio
    async def test_recurring_fact_with_only_one_evidence_turn_is_dropped(self, monkeypatch):
        manager, generator = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "k", "content": "always crops to portrait", '
            '"kind": "recurring", "evidence": [2]}]',
        )

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_stated_fact_with_quote_present_verbatim_is_accepted(self, monkeypatch):
        # A transcript whose first user turn literally contains the quote.
        messages = [_message("user", "I always want short captions, please.", msg_id="u0")]
        for i in range(MIN_UNREFLECTED_USER_MESSAGES - 1):
            messages.append(_message("assistant", f"ack {i}", msg_id=f"a{i}"))
            messages.append(_message("user", f"question {i}", msg_id=f"u{i + 1}"))

        manager, generator = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "short_captions", '
            '"content": "wants captions kept short", "kind": "stated", '
            '"evidence": [1], "quote": "I always want short captions, please."}]',
            user_messages=messages,
        )

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "saved"}]
        manager.memory_ops.write_note.assert_called_once()

    @pytest.mark.asyncio
    async def test_stated_fact_with_quote_not_in_cited_turn_is_dropped(self, monkeypatch):
        manager, generator = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "k", "content": "wants captions kept short", '
            '"kind": "stated", "evidence": [1], '
            '"quote": "this sentence was never actually said by the user"}]',
        )

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_stated_fact_missing_quote_is_dropped(self, monkeypatch):
        manager, generator = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "k", "content": "wants captions kept short", '
            '"kind": "stated", "evidence": [1]}]',
        )

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_generation_subject_restated_as_preference_is_dropped_by_overlap_backstop(self, monkeypatch):
        """The maintainer's reported case: a single generation prompt
        ('beautiful girl with white tshirt and jeans') restated as a
        'preference'. Even when the model plays by the kind/evidence rules
        (a real quote from the turn it cites), the content-overlap backstop
        independently rejects it because the note is just that turn's
        subject matter."""
        messages = [_message("user", "beautiful girl with white tshirt and jeans", msg_id="u0")]
        for i in range(MIN_UNREFLECTED_USER_MESSAGES - 1):
            messages.append(_message("assistant", f"ack {i}", msg_id=f"a{i}"))
            messages.append(_message("user", f"question {i}", msg_id=f"u{i + 1}"))

        manager, generator = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "outfit_pref", '
            '"content": "user likes to generate beautiful girls wearing white tshirts and jeans", '
            '"kind": "stated", "evidence": [1], '
            '"quote": "beautiful girl with white tshirt and jeans"}]',
            user_messages=messages,
        )

        saved = await generator.reflect("session-1")

        assert saved == []
        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_genuine_workflow_preference_passes_overlap_backstop(self, monkeypatch):
        """A real standing preference, synthesized across turns whose actual
        subject matter is unrelated, must not be caught by the same backstop
        that rejects the restated-subject case above."""
        messages = [
            _message("user", "draw a wizard casting a lightning spell", msg_id="u0"),
            _message("assistant", "ack", msg_id="a0"),
            _message("user", "a red sports car on a mountain road", msg_id="u1"),
            _message("assistant", "ack", msg_id="a1"),
            _message("user", "a lighthouse at sunset", msg_id="u2"),
            _message("assistant", "ack", msg_id="a2"),
            _message("user", "a snowy mountain peak at dawn", msg_id="u3"),
        ]

        manager, generator = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "short_prompts", '
            '"content": "prefers short prompts without quality tags", '
            '"kind": "recurring", "evidence": [1, 2, 3]}]',
            user_messages=messages,
        )

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "saved"}]
        manager.memory_ops.write_note.assert_called_once()


class TestReflectScoping:
    """Scope/scope_ref validation, resolved by the session's own mode: a
    generation session (``mode="generation"``) offers 'preset' scope, one
    entry per preset id seen; any other mode offers only 'mode' scope,
    scope_ref exactly the session's mode id. 'model' scope is never offered
    or accepted by reflection any more. A mismatch, or a scope this pass
    never offered, is DROPPED, never rewritten to 'global'."""

    def _setup(self, monkeypatch, response_content, mode=None):
        manager = _manager(monkeypatch)
        session = _session(mode=mode)
        messages = _messages(MIN_UNREFLECTED_USER_MESSAGES)
        manager.chat_repository.get_session.return_value = session
        manager.chat_repository.get_messages.return_value = messages
        manager.chat_repository.record_memory_reflection.return_value = True

        response = Mock()
        response.content = response_content
        manager.llm_service.generate_with_history = AsyncMock(return_value=response)

        manager.preset_collaborators.get_preset.return_value = {"name": "My Preset"}
        model = Mock()
        model.filename = "my_model.safetensors"
        manager.model_index_manager.model_repo.get_by_id.return_value = model
        chat_mode = Mock()
        chat_mode.name = "LoRA Dataset"
        manager.chat_mode_registry.get.return_value = chat_mode

        saved_note = Mock()
        saved_note.to_dict.return_value = {"key": "saved"}
        manager.memory_ops.write_note.return_value = saved_note
        # Compaction rides along after a persist; keep it a no-op so these
        # tests only exercise scoping, not compaction (read_notes returns []).
        manager.memory_ops.read_notes.return_value = []

        return manager, ChatReflectionGenerator(manager), messages

    @pytest.mark.asyncio
    async def test_valid_preset_scope_ref_accepted(self, monkeypatch):
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "preset", "scope_ref": "preset-123", "key": "k", '
            '"content": "always uses this preset for portraits", '
            '"kind": "recurring", "evidence": [1, 2]}]',
            mode="generation",
        )

        await generator.reflect("session-1", form_state={"preset": "preset-123", "form_data": {}})

        manager.memory_ops.write_note.assert_called_once_with(
            manager.llm_memory_repository,
            user_id="user-1", key="k",
            content="always uses this preset for portraits",
            scope="preset", scope_ref="preset-123",
        )

    @pytest.mark.asyncio
    async def test_preset_from_earlier_turn_accepted(self, monkeypatch):
        """A fact scoped to a preset that was active on an EARLIER unreflected
        turn (recorded in that message's metadata) is accepted even though
        the triggering turn's form_state names a different preset."""
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "preset", "scope_ref": "preset-old", "key": "k", '
            '"content": "always uses this preset for portraits", '
            '"kind": "recurring", "evidence": [1, 2]}]',
            mode="generation",
        )
        messages[0].metadata = {"preset_id": "preset-old"}

        await generator.reflect("session-1", form_state={"preset": "preset-new", "form_data": {}})

        sent = manager.llm_service.generate_with_history.await_args.kwargs["messages"][0]["content"]
        assert "preset-old" in sent and "preset-new" in sent
        manager.memory_ops.write_note.assert_called_once_with(
            manager.llm_memory_repository,
            user_id="user-1", key="k",
            content="always uses this preset for portraits",
            scope="preset", scope_ref="preset-old",
        )

    @pytest.mark.asyncio
    async def test_model_scope_never_offered_is_dropped(self, monkeypatch):
        """'model' scope is no longer offered or resolved by reflection at
        all (it remains exclusive to the interactive write_memory tool) -
        even a plausible model-shaped fact in a generation session is
        dropped, never rewritten to 'global'."""
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "model", "scope_ref": "model-1", "key": "k", '
            '"content": "always adds a LoRA with this model", '
            '"kind": "recurring", "evidence": [1, 2]}]',
            mode="generation",
        )

        await generator.reflect(
            "session-1",
            form_state={"preset": "preset-123", "form_data": {"checkpoint": "model:model-1"}},
        )

        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_mode_scope_ref_accepted(self, monkeypatch):
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "mode", "scope_ref": "lora-dataset", "key": "k", '
            '"content": "always captions one image at a time in this mode", '
            '"kind": "recurring", "evidence": [1, 2]}]',
            mode="lora-dataset",
        )

        await generator.reflect("session-1")

        manager.memory_ops.write_note.assert_called_once_with(
            manager.llm_memory_repository,
            user_id="user-1", key="k",
            content="always captions one image at a time in this mode",
            scope="mode", scope_ref="lora-dataset",
        )

    @pytest.mark.asyncio
    async def test_mismatched_mode_scope_ref_dropped(self, monkeypatch):
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "mode", "scope_ref": "some-other-mode", "key": "k", '
            '"content": "a habit reported for a mode never active here", '
            '"kind": "recurring", "evidence": [1, 2]}]',
            mode="lora-dataset",
        )

        await generator.reflect("session-1")

        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_hallucinated_scope_ref_dropped(self, monkeypatch):
        """A mismatched scope_ref is DROPPED, never silently rewritten to
        'global' - a scoped fact with no honest home is not evidence it's
        universally true."""
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "preset", "scope_ref": "preset-999", "key": "k", '
            '"content": "made up preference for a preset never active here", '
            '"kind": "recurring", "evidence": [1, 2]}]',
            mode="generation",
        )

        await generator.reflect("session-1", form_state={"preset": "preset-123", "form_data": {}})

        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_form_state_scoped_fact_dropped(self, monkeypatch):
        """A generation session with no active preset for this turn (and no
        preset seen anywhere in the span): a fact reported as 'preset' scope
        has nothing to validate against and is dropped - it does NOT fall
        back to a global note either (zero preset notes AND zero global
        notes derived from it)."""
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "preset", "scope_ref": "preset-123", "key": "k", '
            '"content": "a preference reported with no active context at all", '
            '"kind": "recurring", "evidence": [1, 2]}]',
            mode="generation",
        )

        await generator.reflect("session-1")

        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_model_scoped_global_still_saves_as_global_when_reported(self, monkeypatch):
        """'global' is only ever what the model itself deliberately reports -
        confirms the drop rule above isn't blocking legitimate global facts."""
        manager, generator, messages = self._setup(
            monkeypatch,
            '[{"scope": "global", "key": "k", "content": "prefers moody lighting overall", '
            '"kind": "recurring", "evidence": [1, 2]}]'
        )

        await generator.reflect("session-1")

        manager.memory_ops.write_note.assert_called_once_with(
            manager.llm_memory_repository,
            user_id="user-1", key="k",
            content="prefers moody lighting overall",
            scope="global", scope_ref=None,
        )


class TestValidateScope:
    """Unit coverage for `_validate_scope(scope, scope_ref, offered_preset_ids,
    offered_mode_id)` - see `_resolve_active_context` for how those two
    offered-scope inputs are built per session mode."""

    def test_matching_preset_ref_kept(self):
        assert ChatReflectionGenerator._validate_scope("preset", "p1", {"p1"}, None) == ("preset", "p1")

    def test_mismatched_preset_ref_dropped(self):
        assert ChatReflectionGenerator._validate_scope("preset", "p2", {"p1"}, None) is None

    def test_model_scope_never_valid_even_with_offered_presets(self):
        """'model' is never a valid reflection scope any more, regardless of
        what's offered - it's exclusive to the interactive write_memory tool."""
        assert ChatReflectionGenerator._validate_scope("model", "m1", {"m1"}, None) is None

    def test_matching_mode_ref_kept(self):
        assert ChatReflectionGenerator._validate_scope("mode", "md1", set(), "md1") == ("mode", "md1")

    def test_mismatched_mode_ref_dropped(self):
        assert ChatReflectionGenerator._validate_scope("mode", "md2", set(), "md1") is None

    def test_mode_ref_with_no_offered_mode_dropped(self):
        assert ChatReflectionGenerator._validate_scope("mode", "md1", set(), None) is None

    def test_preset_scope_with_no_offered_presets_dropped(self):
        assert ChatReflectionGenerator._validate_scope("preset", "p1", set(), None) is None

    def test_invalid_scope_name_dropped(self):
        assert ChatReflectionGenerator._validate_scope("banana", "p1", {"p1"}, None) is None

    def test_global_scope_always_kept(self):
        assert ChatReflectionGenerator._validate_scope("global", None, {"p1"}, "md1") == ("global", None)


class TestValidateFactGrounding:
    def test_recurring_with_two_distinct_turns_passes(self):
        item = {"kind": "recurring", "evidence": [1, 3]}
        assert ChatReflectionGenerator._validate_fact_grounding(item, {}) is None

    def test_recurring_with_duplicate_indices_fails(self):
        item = {"kind": "recurring", "evidence": [2, 2]}
        assert ChatReflectionGenerator._validate_fact_grounding(item, {}) is not None

    def test_recurring_with_one_turn_fails(self):
        item = {"kind": "recurring", "evidence": [1]}
        assert ChatReflectionGenerator._validate_fact_grounding(item, {}) is not None

    def test_stated_with_verbatim_quote_passes(self):
        item = {"kind": "stated", "evidence": [1], "quote": "always wants short captions"}
        turns = {1: "The user says: always wants short captions, every time."}
        assert ChatReflectionGenerator._validate_fact_grounding(item, turns) is None

    def test_stated_quote_is_whitespace_and_case_normalized(self):
        item = {"kind": "stated", "evidence": [1], "quote": "Always Wants   short captions"}
        turns = {1: "always wants short captions please"}
        assert ChatReflectionGenerator._validate_fact_grounding(item, turns) is None

    def test_stated_with_quote_not_in_turn_fails(self):
        item = {"kind": "stated", "evidence": [1], "quote": "never said this"}
        turns = {1: "something completely different"}
        assert ChatReflectionGenerator._validate_fact_grounding(item, turns) is not None

    def test_stated_missing_quote_fails(self):
        item = {"kind": "stated", "evidence": [1]}
        turns = {1: "anything"}
        assert ChatReflectionGenerator._validate_fact_grounding(item, turns) is not None

    def test_stated_citing_a_turn_not_in_span_fails(self):
        item = {"kind": "stated", "evidence": [5], "quote": "hello"}
        turns = {1: "hello there"}
        assert ChatReflectionGenerator._validate_fact_grounding(item, turns) is not None

    def test_missing_kind_fails(self):
        item = {"evidence": [1, 2]}
        assert ChatReflectionGenerator._validate_fact_grounding(item, {}) is not None

    def test_invalid_kind_fails(self):
        item = {"kind": "vibes", "evidence": [1, 2]}
        assert ChatReflectionGenerator._validate_fact_grounding(item, {}) is not None

    def test_missing_evidence_fails(self):
        item = {"kind": "recurring"}
        assert ChatReflectionGenerator._validate_fact_grounding(item, {}) is not None

    def test_empty_evidence_fails(self):
        item = {"kind": "recurring", "evidence": []}
        assert ChatReflectionGenerator._validate_fact_grounding(item, {}) is not None

    def test_non_integer_evidence_fails(self):
        item = {"kind": "recurring", "evidence": ["1", "2"]}
        assert ChatReflectionGenerator._validate_fact_grounding(item, {}) is not None


class TestRestatesSinglePrompt:
    def test_generation_subject_restated_as_preference_detected(self):
        content = "user likes to generate beautiful girls wearing white tshirts and jeans"
        turns = {1: "beautiful girl with white tshirt and jeans"}
        assert ChatReflectionGenerator._restates_single_prompt(content, turns) is True

    def test_genuine_workflow_preference_not_flagged(self):
        content = "prefers short prompts without quality tags"
        turns = {
            1: "draw a wizard casting a lightning spell",
            2: "a red sports car on a mountain road",
            3: "a lighthouse at sunset",
        }
        assert ChatReflectionGenerator._restates_single_prompt(content, turns) is False

    def test_short_content_never_flagged(self):
        """Below the minimum content-word count, the check doesn't even try -
        too few words and any overlap looks total."""
        content = "likes cats"
        turns = {1: "a photo of two cats sitting on a windowsill"}
        assert ChatReflectionGenerator._restates_single_prompt(content, turns) is False

    def test_no_turns_never_flagged(self):
        assert ChatReflectionGenerator._restates_single_prompt("prefers moody cinematic lighting", {}) is False

    def test_high_overlap_turn_that_is_itself_a_preference_statement_not_flagged(self):
        """A note closely restating a turn is only suspect when that turn
        reads as a plain generation request - not when the turn itself
        already reads as the user stating a preference."""
        content = "wants captions kept short"
        turns = {1: "I always want short captions, please."}
        assert ChatReflectionGenerator._restates_single_prompt(content, turns) is False


class TestLooksLikeAPreferenceStatement:
    def test_generation_request_has_no_cue(self):
        from src.features.chat.reflection import _looks_like_a_preference_statement
        assert _looks_like_a_preference_statement("beautiful girl with white tshirt and jeans") is False

    def test_preference_statement_has_a_cue(self):
        from src.features.chat.reflection import _looks_like_a_preference_statement
        assert _looks_like_a_preference_statement("I always want short captions, please.") is True

    def test_prefer_variants_match(self):
        from src.features.chat.reflection import _looks_like_a_preference_statement
        assert _looks_like_a_preference_statement("I prefer darker palettes") is True
        assert _looks_like_a_preference_statement("my preferred style is anime") is True


class TestBuildSpanTurnNumbering:
    def test_user_turns_numbered_sequentially_assistant_unnumbered(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session()
        messages = _messages(3)
        manager.chat_repository.get_session.return_value = session

        span = generator._build_span(session, messages, char_budget=10000)

        assert span.user_turn_texts == {1: "question 0", 2: "question 1", 3: "question 2"}
        assert "[1] User: question 0" in span.transcript
        assert "[2] User: question 1" in span.transcript
        assert "[3] User: question 2" in span.transcript
        assert "Assistant: answer 0" in span.transcript
        assert "[1] Assistant" not in span.transcript

    def test_deferred_backlog_turn_does_not_consume_a_number(self, monkeypatch):
        """A message that doesn't fit and is deferred to backlog must not
        burn a turn number that then never appears in the transcript."""
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session()
        messages = _messages(2)
        manager.chat_repository.get_session.return_value = session

        # Budget fits only the first user+assistant pair's worth of text.
        first_line_len = len(f"[1] User: {messages[0].content}")
        span = generator._build_span(session, messages, char_budget=first_line_len)

        assert span.user_turn_texts == {1: "question 0"}
        assert span.has_backlog is True


class TestBuildSpanPresetAnnotation:
    """A generation session's user turns are annotated with the preset that
    was active when the message was sent (see `ConversationRunner`'s
    per-message `preset_id` persistence)."""

    def test_generation_session_annotates_turns(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session(mode="generation")
        manager.preset_collaborators.get_preset.side_effect = lambda pid: {
            "preset-A": {"name": "Krea-2"}, "preset-B": {"name": "SDXL"},
        }[pid]
        messages = [
            _message("user", "question 0", "u0", metadata={"preset_id": "preset-A"}),
            _message("assistant", "answer 0", "a0"),
            _message("user", "question 1", "u1", metadata={"preset_id": "preset-B"}),
            _message("assistant", "answer 1", "a1"),
        ]

        span = generator._build_span(session, messages, char_budget=10000)

        assert "[1] User (preset: Krea-2): question 0" in span.transcript
        assert "[2] User (preset: SDXL): question 1" in span.transcript

    def test_generation_session_turn_without_preset_id_unannotated(self, monkeypatch):
        """An older message predating per-message preset persistence carries
        no metadata at all - it gets the plain, unannotated prefix."""
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session(mode="generation")
        messages = [
            _message("user", "question 0", "u0"),
            _message("assistant", "answer 0", "a0"),
        ]

        span = generator._build_span(session, messages, char_budget=10000)

        assert "[1] User: question 0" in span.transcript

    def test_non_generation_session_never_annotates_even_with_preset_metadata(self, monkeypatch):
        """A plugin-mode session's turns are never preset-annotated, even if
        a message happens to carry preset_id metadata (it shouldn't, but the
        annotation is gated on session.mode regardless)."""
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session(mode="lora-dataset")
        messages = [
            _message("user", "question 0", "u0", metadata={"preset_id": "preset-A"}),
            _message("assistant", "answer 0", "a0"),
        ]

        span = generator._build_span(session, messages, char_budget=10000)

        assert "[1] User: question 0" in span.transcript
        assert "preset:" not in span.transcript


class TestResolveActiveContext:
    """Unit coverage for `_resolve_active_context`'s per-session-mode offered
    scopes, independent of the full `reflect()` path above."""

    def test_generation_session_offers_span_and_form_state_presets(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session(mode="generation")
        manager.preset_collaborators.get_preset.side_effect = lambda pid: {
            "preset-A": {"name": "Krea-2"}, "preset-B": {"name": "SDXL"},
        }[pid]

        offered_presets, offered_mode = generator._resolve_active_context(
            session, {"preset": "preset-B", "form_data": {}}, frozenset({"preset-A"}),
        )

        assert offered_presets == {"preset-A": "Krea-2", "preset-B": "SDXL"}
        assert offered_mode is None

    def test_generation_session_with_no_span_or_form_state_preset_offers_nothing(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session(mode="generation")

        offered_presets, offered_mode = generator._resolve_active_context(session, None, frozenset())

        assert offered_presets == {}
        assert offered_mode is None

    def test_plugin_mode_session_offers_only_its_own_mode(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session(mode="lora-dataset")
        chat_mode = Mock()
        chat_mode.name = "LoRA Dataset"
        manager.chat_mode_registry.get.return_value = chat_mode

        # A stray preset id (span or form_state) must never be offered by a
        # plugin-mode session - it has no Generate form open at all.
        offered_presets, offered_mode = generator._resolve_active_context(
            session, {"preset": "preset-A", "form_data": {}}, frozenset({"preset-B"}),
        )

        assert offered_presets == {}
        assert offered_mode == ("lora-dataset", "LoRA Dataset")

    def test_no_session_mode_offers_nothing(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session(mode=None)

        offered_presets, offered_mode = generator._resolve_active_context(session, None, frozenset())

        assert offered_presets == {}
        assert offered_mode is None


class TestBuildPromptPerSessionMode:
    """The prompt's JSON 'scope' enum is built strictly from what this pass
    actually offers (see `_resolve_active_context`) - it never lists a scope
    that isn't offered, though the prose may still name a withheld scope to
    tell the model not to use it."""

    def test_generation_prompt_lists_offered_presets_enum_excludes_model_and_mode(self):
        prompt = ChatReflectionGenerator._build_prompt(
            {"preset-A": "Krea-2", "preset-B": "SDXL"}, None,
        )

        assert '"Krea-2" (id: preset-A)' in prompt
        assert '"SDXL" (id: preset-B)' in prompt
        assert '"scope": "global"|"preset"' in prompt

    def test_plugin_mode_prompt_names_the_mode_enum_excludes_preset_and_model(self):
        prompt = ChatReflectionGenerator._build_prompt({}, ("lora-dataset", "LoRA Dataset"))

        assert "lora-dataset" in prompt
        assert "LoRA Dataset" in prompt
        assert '"scope": "global"|"mode"' in prompt

    def test_no_offered_scope_prompt_enum_is_global_only(self):
        prompt = ChatReflectionGenerator._build_prompt({}, None)

        assert '{"scope": "global", "scope_ref":' in prompt


class TestValidateScopeAndPersistIntegration:
    """`_persist_items` never rewrites a dropped scope to global - regression
    guard for the old fallback behavior at the persistence boundary itself,
    independent of the full `reflect()` path above."""

    def test_scope_mismatch_is_never_persisted_as_global(self, monkeypatch):
        manager = _manager(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        items = [{
            "scope": "preset", "scope_ref": "preset-999", "key": "k",
            "content": "a fact for a preset that was never active",
            "kind": "recurring", "evidence": [1, 2],
        }]

        saved = generator._persist_items(
            "user-1", items, offered_preset_ids={"preset-123"}, offered_mode_id=None,
        )

        assert saved == []
        manager.memory_ops.write_note.assert_not_called()
