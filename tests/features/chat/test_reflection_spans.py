"""Tests for the bounded-span reflection mechanism: a char-budget span with a
resumable {message_id, offset} cursor, per-session single-flight triggering
with one coalesced follow-up, and monotonic cursor bookkeeping that survives
two passes completing out of order. See `ChatReflectionGenerator`'s module
docstring in `src/features/chat/reflection.py` for the design this covers.
"""

import asyncio
import math
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.features.chat.reflection import (
    ChatReflectionGenerator,
    MAX_TRANSCRIPT_CHARS,
)
from src.features.chat.repository import ChatRepository
from src.features.chat.runtime import ChatRuntime
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.llm.gateway import LLMGateway
from src.features.llm.repository import LLMConfig
from tests.fixtures.persistence_base import PersistenceTestBase


def _mode_registry() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


def _message(role: str, content: str, msg_id: str) -> Mock:
    msg = Mock()
    msg.id = msg_id
    msg.role = role
    msg.content = content
    return msg


def _session(llm_config_id: str = "llm-1", metadata: dict = None) -> Mock:
    session = Mock()
    session.id = "session-1"
    session.user_id = "user-1"
    session.llm_config_id = llm_config_id
    session.metadata = metadata or {}
    return session


class _FakeLLMService:
    """Records every call's transcript and replays queued responses (a
    string content, or an exception instance to raise). `gate_next_call`
    lets a test suspend the NEXT call on an event, to genuinely observe a
    pass "in flight" before letting it finish.
    """

    def __init__(self, memory_reflection: bool = True):
        self.calls = []
        self._responses = []
        self._gate = None
        self.repository = SimpleNamespace(
            get_configuration=lambda cfg_id: SimpleNamespace(
                memory_reflection=memory_reflection, provider_options=None, type="test",
            )
        )

    def queue(self, content_or_exc) -> None:
        self._responses.append(content_or_exc)

    def gate_next_call(self) -> asyncio.Event:
        self._gate = asyncio.Event()
        return self._gate

    async def generate_with_history(self, messages, llm_id, options_override=None):
        self.calls.append(messages[0]["content"])
        if self._gate is not None:
            gate, self._gate = self._gate, None
            await gate.wait()
        item = self._responses.pop(0) if self._responses else "[]"
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(content=item)


def _manager_with_mock_repo(monkeypatch, memory_reflection: bool = True) -> Mock:
    manager = Mock()
    manager.llm_service = _FakeLLMService(memory_reflection=memory_reflection)
    manager.chat_repository = Mock()
    manager.llm_memory_repository = Mock()
    manager._reflection_tasks = set()
    mock_ops = Mock()
    monkeypatch.setattr("src.features.chat.reflection.memory_operations", mock_ops)
    saved_note = Mock()
    saved_note.to_dict.return_value = {"key": "saved"}
    mock_ops.write_note.return_value = saved_note
    mock_ops.read_notes.return_value = []
    manager.memory_ops = mock_ops
    return manager


class TestSpanCharBudget:
    """`_build_span`'s char-budget boundary and its effect on the cursor
    recorded across two passes."""

    @pytest.mark.asyncio
    async def test_correction_past_boundary_lands_in_second_pass_not_first(self, monkeypatch):
        manager = _manager_with_mock_repo(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        manager.chat_repository.get_session.return_value = _session()
        manager.chat_repository.record_memory_reflection.return_value = True

        # Filler that nearly fills the whole default budget, then a short
        # correction right after it - the correction must never appear in
        # the pass that claims the filler.
        filler = "x" * (MAX_TRANSCRIPT_CHARS - 50)
        messages = [
            _message("user", filler, "u0"),
            _message("assistant", "ok", "a0"),
            _message("user", "actually I prefer dark fantasy, not anime", "u1"),
            _message("assistant", "noted", "a1"),
            _message("user", "q2", "u2"),
            _message("assistant", "a2", "a2"),
            _message("user", "q3", "u3"),
            _message("assistant", "a3", "a3"),
        ]
        manager.chat_repository.get_messages.return_value = messages
        manager.llm_service.queue("[]")

        saved = await generator.reflect("session-1")

        assert saved == []
        assert len(manager.llm_service.calls) == 1
        assert "dark fantasy" not in manager.llm_service.calls[0]

        call = manager.chat_repository.record_memory_reflection.call_args
        recorded_id, recorded_seq = call.args[1], call.kwargs["seq"]
        assert recorded_id != "u1"
        assert call.kwargs["pending_backlog"] is True

        # A follow-up pass (as `_run_pass` would run, unthreshold-gated,
        # continuing the same burst) must now see the correction.
        manager.chat_repository.get_session.return_value = _session(metadata={
            "memory_reflection": {
                "reflected_up_to_message_id": recorded_id,
                "reflected_up_to_offset": 0,
                "reflected_up_to_seq": recorded_seq,
            }
        })
        manager.llm_service.queue(
            '[{"scope": "global", "key": "k", "content": "prefers dark fantasy over anime"}]'
        )

        await generator.reflect("session-1", require_threshold=False)

        assert len(manager.llm_service.calls) == 2
        assert "dark fantasy" in manager.llm_service.calls[1]

    @pytest.mark.asyncio
    async def test_single_oversized_message_chunks_and_resumes_at_offset(self, monkeypatch):
        manager = _manager_with_mock_repo(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        manager.chat_repository.get_session.return_value = _session()
        manager.chat_repository.record_memory_reflection.return_value = True

        # One message alone far exceeds the budget - each token is unique so
        # we can tell exactly which slice of it reached the model.
        huge = "".join(f"[{i:05d}]" for i in range(4000))
        messages = [
            _message("user", huge, "u0"),
            _message("assistant", "ok", "a0"),
            _message("user", "q1", "u1"),
            _message("assistant", "a1", "a1"),
            _message("user", "q2", "u2"),
            _message("assistant", "a2", "a2"),
            _message("user", "q3", "u3"),
            _message("assistant", "a3", "a3"),
        ]
        manager.chat_repository.get_messages.return_value = messages
        manager.llm_service.queue("[]")

        await generator.reflect("session-1")

        assert len(manager.llm_service.calls) == 1
        transcript = manager.llm_service.calls[0]
        assert "[00000]" in transcript  # the start of the message was sent

        call = manager.chat_repository.record_memory_reflection.call_args
        assert call.args[1] == "u0"
        offset = call.kwargs["offset"]
        assert offset > 0
        assert call.kwargs["pending_backlog"] is True

        manager.chat_repository.get_session.return_value = _session(metadata={
            "memory_reflection": {
                "reflected_up_to_message_id": "u0",
                "reflected_up_to_offset": offset,
                "reflected_up_to_seq": call.kwargs["seq"],
            }
        })
        manager.llm_service.queue("[]")

        await generator.reflect("session-1", require_threshold=False)

        assert len(manager.llm_service.calls) == 2
        resumed = manager.llm_service.calls[1]
        # The resumed pass never re-sends the part already covered.
        assert "[00000]" not in resumed


class TestReflectionFailure:
    @pytest.mark.asyncio
    async def test_failure_does_not_advance_cursor_or_retry_immediately(self, monkeypatch):
        manager = _manager_with_mock_repo(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session()
        manager.chat_repository.get_session.return_value = session
        messages = [
            _message("user", f"q{i}", f"u{i}") if i % 2 == 0 else _message("assistant", f"a{i}", f"a{i}")
            for i in range(8)
        ]
        manager.chat_repository.get_messages.return_value = messages
        manager.llm_service.queue(RuntimeError("provider down"))

        saved = await generator.reflect("session-1")

        assert saved == []
        assert len(manager.llm_service.calls) == 1
        manager.chat_repository.record_memory_reflection.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_model_output_claims_no_coverage(self, monkeypatch):
        """Distinct from a valid `[]`: text with no parseable JSON array is
        a malformed-output failure, not a real 'nothing durable' answer -
        no coverage is claimed either way."""
        manager = _manager_with_mock_repo(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session()
        manager.chat_repository.get_session.return_value = session
        messages = [
            _message("user", f"q{i}", f"u{i}") if i % 2 == 0 else _message("assistant", f"a{i}", f"a{i}")
            for i in range(8)
        ]
        manager.chat_repository.get_messages.return_value = messages
        manager.llm_service.queue("sorry, I can't help with that")

        saved = await generator.reflect("session-1")

        assert saved == []
        assert len(manager.llm_service.calls) == 1
        manager.chat_repository.record_memory_reflection.assert_not_called()
        manager.memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_persistence_failure_claims_no_coverage_but_keeps_saved_notes(self, monkeypatch):
        """A raised exception writing the cursor is a persistence failure -
        distinct from the extraction/note-writing that already succeeded."""
        manager = _manager_with_mock_repo(monkeypatch)
        generator = ChatReflectionGenerator(manager)
        session = _session()
        manager.chat_repository.get_session.return_value = session
        messages = [
            _message("user", f"q{i}", f"u{i}") if i % 2 == 0 else _message("assistant", f"a{i}", f"a{i}")
            for i in range(8)
        ]
        manager.chat_repository.get_messages.return_value = messages
        manager.llm_service.queue(
            '[{"scope": "global", "key": "k", "content": "prefers dark fantasy over anime", '
            '"kind": "recurring", "evidence": [1, 2]}]'
        )
        manager.chat_repository.record_memory_reflection.side_effect = RuntimeError("db write failed")

        saved = await generator.reflect("session-1")

        assert saved == [{"key": "saved"}]  # the note itself was written before the cursor write failed
        manager.chat_repository.record_memory_reflection.assert_called_once()


class TestReflectionConcurrencyAndBookkeeping(PersistenceTestBase):
    """Exercises the real trigger seam (`ChatReflectionGenerator.trigger`
    through a real `ChatRuntime`) and the real `ChatRepository`, with a fake
    LLM provider standing in for the model call."""

    def setUp(self):
        super().setUp()
        self.chat_repo = ChatRepository()
        self.user_id = self.create_test_user()

    def tearDown(self):
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute("DELETE FROM chat_messages")
                cursor.execute("DELETE FROM chat_sessions")
                cursor.execute("DELETE FROM users")
        except Exception:
            pass
        super().tearDown()

    def _build_runtime(self, memory_reflection: bool = True):
        llm_service = _FakeLLMService(memory_reflection=memory_reflection)
        runtime = ChatRuntime(
            chat_repository=self.chat_repo,
            llm_service=llm_service,
            response_processor=Mock(),
            plugin_registry=Mock(),
            chat_mode_registry=_mode_registry(),
        )
        runtime.llm_memory_repository = Mock()
        return runtime, llm_service

    def _create_session_with_messages(self, turns) -> str:
        session = self.chat_repo.create_session(user_id=self.user_id, llm_config_id="llm-1")
        for role, content in turns:
            self.chat_repo.add_message(session_id=session.id, role=role, content=content)
        return session.id

    # --- monotonic cursor: a stale, slower pass completing after a newer
    # one must never move the cursor backward (the original bug). ---

    def test_reversed_completion_cannot_move_cursor_backward(self):
        session = self.chat_repo.create_session(user_id=self.user_id, llm_config_id="llm-1")
        m0 = self.chat_repo.add_message(session_id=session.id, role="user", content="q0")
        self.chat_repo.add_message(session_id=session.id, role="assistant", content="a0")
        m2 = self.chat_repo.add_message(session_id=session.id, role="user", content="q1")

        # The newer pass (further along, seq=2) finishes and records first.
        assert self.chat_repo.record_memory_reflection(
            session.id, m2.id, offset=0, seq=2, pending_backlog=False,
        ) is True

        # An older, slower pass (seq=0) completes after it - must not move
        # the cursor backward even though its write happens later.
        assert self.chat_repo.record_memory_reflection(
            session.id, m0.id, offset=0, seq=0, pending_backlog=True,
        ) is False

        updated = self.chat_repo.get_session(session.id)
        stored = updated.metadata["memory_reflection"]
        assert stored["reflected_up_to_message_id"] == m2.id
        assert stored["reflected_up_to_seq"] == 2
        assert stored["pending_backlog"] is False

    def test_completing_a_message_outranks_its_own_earlier_partial_offset(self):
        """Same seq, partial then complete: a chunked pass records a
        positive mid-message offset; a later pass finishing that SAME
        message must record its full length as "later" than that partial
        offset - not be rejected for looking like it covers less."""
        session = self.chat_repo.create_session(user_id=self.user_id, llm_config_id="llm-1")
        huge = self.chat_repo.add_message(session_id=session.id, role="assistant", content="x" * 500)

        # First pass: chunked partway through the message.
        assert self.chat_repo.record_memory_reflection(
            session.id, huge.id, offset=300, seq=0, pending_backlog=True,
        ) is True

        # Second pass: finishes the same message in full - offset is its
        # whole length (500), which must outrank the earlier partial (300).
        assert self.chat_repo.record_memory_reflection(
            session.id, huge.id, offset=500, seq=0, pending_backlog=False,
        ) is True

        stored = self.chat_repo.get_session(session.id).metadata["memory_reflection"]
        assert stored["reflected_up_to_offset"] == 500
        assert stored["pending_backlog"] is False

        # A stale, even-older completion of the same message (e.g. a
        # slow duplicate pass) arriving after must not regress it.
        assert self.chat_repo.record_memory_reflection(
            session.id, huge.id, offset=300, seq=0, pending_backlog=True,
        ) is False
        stored_after = self.chat_repo.get_session(session.id).metadata["memory_reflection"]
        assert stored_after["reflected_up_to_offset"] == 500
        assert stored_after["pending_backlog"] is False

    def test_oversized_final_message_partial_then_complete_then_idle(self):
        """The end-to-end trigger->span->real-repository path for the bug:
        an oversized LAST message (nothing after it) is chunked on the first
        pass, completed on a second, and a further trigger extracts nothing
        further - proving the cursor didn't get stuck re-offering the tail
        forever (the original 0-means-complete bug) nor silently regress to
        re-extract it once more."""
        asyncio.run(self._async_oversized_final_message())

    async def _async_oversized_final_message(self):
        runtime, llm = self._build_runtime()
        session = self.chat_repo.create_session(user_id=self.user_id, llm_config_id="llm-1")
        session_id = session.id
        last_short = None
        for i in range(4):
            self.chat_repo.add_message(session_id=session_id, role="user", content=f"q{i}")
            last_short = self.chat_repo.add_message(session_id=session_id, role="assistant", content=f"a{i}")

        # A final assistant message, alone (nothing after it), sized so the
        # default 12,000-char span budget covers it in exactly two passes:
        # a first chunk, then a second pass completing the rest. Unique
        # markers let us tell which slice of it reached the model each time.
        huge = "".join(f"[{i:05d}]" for i in range(2500))  # 17,500 chars
        huge_msg = self.chat_repo.add_message(session_id=session_id, role="assistant", content=huge)

        # The 4 short turns are already reflected (an earlier pass), so the
        # huge final message is the ONLY unreflected content - isolating the
        # partial/complete offset behaviour this test targets.
        assert self.chat_repo.record_memory_reflection(
            session_id, last_short.id, offset=len(last_short.content), seq=7, pending_backlog=False,
        ) is True

        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            mock_ops.write_note.return_value = Mock()
            mock_ops.read_notes.return_value = []

            # Pass 1: the huge message alone exceeds the budget, so it's
            # chunked - a positive, partial offset is recorded.
            llm.queue("[]")
            saved1 = await runtime.reflection_generator.reflect(session_id, require_threshold=False)
            assert saved1 == []
            assert len(llm.calls) == 1
            assert "[00000]" in llm.calls[0]

            stored1 = self.chat_repo.get_session(session_id).metadata["memory_reflection"]
            assert stored1["reflected_up_to_message_id"] == huge_msg.id
            assert 0 < stored1["reflected_up_to_offset"] < len(huge)
            session_after_1 = self.chat_repo.get_session(session_id)
            assert runtime.reflection_generator.pending_reflection_backlog(session_after_1) is True

            # Pass 2 (continuation, unthresholded like a coalesced
            # follow-up): the remaining tail now fits whole - completes the
            # message. Under the bug, offset=0 for "complete" compared as
            # OLDER than the stored partial offset and this write was
            # silently rejected.
            llm.queue("[]")
            await runtime.reflection_generator.reflect(session_id, require_threshold=False)
            assert len(llm.calls) == 2

            stored2 = self.chat_repo.get_session(session_id).metadata["memory_reflection"]
            assert stored2["reflected_up_to_offset"] == len(huge)
            session_after_2 = self.chat_repo.get_session(session_id)
            assert runtime.reflection_generator.pending_reflection_backlog(session_after_2) is False

            # Pass 3: a further trigger/continuation must extract NOTHING -
            # under the bug the cursor never actually advanced in pass 2, so
            # pass 3 would re-send the same tail, making a THIRD LLM call.
            result3 = await runtime.reflection_generator.reflect(session_id, require_threshold=False)
            assert result3 == []
            assert len(llm.calls) == 2  # no further call was made - nothing left to extract

    # --- single-flight + coalescing: an arrival during a pass produces
    # exactly one follow-up, not a second concurrent pass. ---

    def test_arrivals_during_pass_coalesce_into_one_follow_up(self):
        asyncio.run(self._async_arrivals_during_pass())

    async def _async_arrivals_during_pass(self):
        runtime, llm = self._build_runtime()
        session_id = self._create_session_with_messages([
            ("user", "q0"), ("assistant", "a0"),
            ("user", "q1"), ("assistant", "a1"),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        ])
        gate = llm.gate_next_call()
        llm.queue("[]")

        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            mock_ops.write_note.return_value = Mock()
            mock_ops.read_notes.return_value = []

            runtime.reflection_generator.trigger(session_id)
            await asyncio.sleep(0)  # let the task run up to the gated LLM call
            assert len(llm.calls) == 1  # the first pass is genuinely in flight

            # A real new turn (well past the 4-message threshold on its own)
            # arrives while that pass is still running.
            for i in range(4):
                self.chat_repo.add_message(session_id=session_id, role="user", content=f"more {i}")
                self.chat_repo.add_message(session_id=session_id, role="assistant", content=f"ack {i}")
            self.chat_repo.add_message(
                session_id=session_id, role="user", content="actually I prefer dark fantasy",
            )
            runtime.reflection_generator.trigger(session_id)  # must coalesce, not start a 2nd task

            tasks = list(runtime._reflection_tasks)
            assert len(tasks) == 1

            llm.queue('[{"scope": "global", "key": "k", "content": "prefers dark fantasy over anime"}]')
            gate.set()
            await asyncio.gather(*tasks)

        assert len(llm.calls) == 2  # one extraction call per span: original + one coalesced follow-up
        assert "dark fantasy" not in llm.calls[0]
        assert "dark fantasy" in llm.calls[1]

    # --- independent sessions proceed concurrently, unaffected by each
    # other's single-flight bookkeeping. ---

    def test_independent_sessions_both_proceed(self):
        asyncio.run(self._async_independent_sessions())

    async def _async_independent_sessions(self):
        runtime, llm = self._build_runtime()
        session_a = self._create_session_with_messages([
            ("user", "a-q0"), ("assistant", "a-a0"), ("user", "a-q1"), ("assistant", "a-a1"),
            ("user", "a-q2"), ("assistant", "a-a2"), ("user", "a-q3"), ("assistant", "a-a3"),
        ])
        session_b = self._create_session_with_messages([
            ("user", "b-q0"), ("assistant", "b-a0"), ("user", "b-q1"), ("assistant", "b-a1"),
            ("user", "b-q2"), ("assistant", "b-a2"), ("user", "b-q3"), ("assistant", "b-a3"),
        ])

        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            mock_ops.write_note.return_value = Mock()
            mock_ops.read_notes.return_value = []
            llm.queue("[]")
            llm.queue("[]")

            runtime.reflection_generator.trigger(session_a)
            runtime.reflection_generator.trigger(session_b)

            tasks = list(runtime._reflection_tasks)
            assert len(tasks) == 2  # neither session's trigger blocked the other's
            await asyncio.gather(*tasks)

        assert len(llm.calls) == 2

    # --- outcome classes through the real bookkeeping seam: a valid empty
    # extraction advances the stored cursor; a malformed response doesn't,
    # and is naturally retried by the next trigger, not immediately. ---

    def test_valid_empty_extraction_advances_the_real_cursor(self):
        asyncio.run(self._async_valid_empty_extraction())

    async def _async_valid_empty_extraction(self):
        runtime, llm = self._build_runtime()
        session_id = self._create_session_with_messages([
            ("user", "q0"), ("assistant", "a0"),
            ("user", "q1"), ("assistant", "a1"),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        ])
        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            llm.queue("[]")
            saved = await runtime.reflection_generator.reflect(session_id)
            assert saved == []
            mock_ops.write_note.assert_not_called()

        stored = (self.chat_repo.get_session(session_id).metadata or {}).get("memory_reflection")
        assert stored is not None
        assert stored["reflected_up_to_message_id"] is not None

    def test_malformed_output_does_not_advance_cursor_and_is_retried_naturally(self):
        asyncio.run(self._async_malformed_output())

    async def _async_malformed_output(self):
        runtime, llm = self._build_runtime()
        session_id = self._create_session_with_messages([
            ("user", "q0"), ("assistant", "a0"),
            ("user", "q1"), ("assistant", "a1"),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        ])

        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            llm.queue("sorry, I can't help with that")
            saved = await runtime.reflection_generator.reflect(session_id)
            assert saved == []
            mock_ops.write_note.assert_not_called()

        # No coverage claimed - the cursor stayed exactly as it was.
        assert (self.chat_repo.get_session(session_id).metadata or {}).get("memory_reflection") is None

        # The NEXT natural trigger (not an immediate retry from within the
        # failed call) sees the identical unreflected span and can still
        # extract it once the model behaves.
        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            mock_ops.write_note.return_value = Mock(to_dict=lambda: {"key": "k"})
            llm.queue(
                '[{"scope": "global", "key": "k", "content": "prefers dark fantasy over anime", '
                '"kind": "recurring", "evidence": [1, 2]}]'
            )
            saved2 = await runtime.reflection_generator.reflect(session_id)
            assert len(saved2) == 1

        assert (self.chat_repo.get_session(session_id).metadata or {}).get("memory_reflection") is not None

    # --- coalesced context: a follow-up covering genuinely NEW messages
    # uses the LATEST trigger's form_state, not the one the in-flight pass
    # started with. ---

    def test_coalesced_follow_up_uses_latest_trigger_context(self):
        asyncio.run(self._async_coalesced_context())

    async def _async_coalesced_context(self):
        runtime, llm = self._build_runtime()
        session_id = self._create_session_with_messages([
            ("user", "q0"), ("assistant", "a0"),
            ("user", "q1"), ("assistant", "a1"),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        ])
        gate = llm.gate_next_call()
        llm.queue("[]")
        form_state_a = {"preset": "preset-A", "form_data": {}}
        form_state_b = {"preset": "preset-B", "form_data": {}}

        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            note = Mock()
            note.to_dict.return_value = {"key": "k"}
            mock_ops.write_note.return_value = note
            mock_ops.read_notes.return_value = []

            # Trigger A starts a pass under preset A's context.
            runtime.reflection_generator.trigger(session_id, form_state_a)
            await asyncio.sleep(0)  # let it reach the gated LLM call
            assert len(llm.calls) == 1

            # New messages arrive and a genuinely new trigger fires under
            # preset B's context WHILE A's pass is still in flight.
            for i in range(4):
                self.chat_repo.add_message(session_id=session_id, role="user", content=f"more {i}")
                self.chat_repo.add_message(session_id=session_id, role="assistant", content=f"ack {i}")
            runtime.reflection_generator.trigger(session_id, form_state_b)  # coalesces

            llm.queue(
                '[{"scope": "preset", "scope_ref": "preset-B", "key": "k", '
                '"content": "always wants dark fantasy lighting with this preset", '
                '"kind": "recurring", "evidence": [1, 2]}]'
            )
            gate.set()
            await asyncio.gather(*list(runtime._reflection_tasks))

            assert len(llm.calls) == 2
            # The coalesced follow-up must have resolved its active preset
            # from B's form_state, not A's - a stale A context would make
            # `_validate_scope` reject "preset-B" and fall back to global.
            mock_ops.write_note.assert_called_once_with(
                runtime.llm_memory_repository,
                user_id=self.user_id, key="k",
                content="always wants dark fantasy lighting with this preset",
                scope="preset", scope_ref="preset-B",
            )


class _FakeGatewayLLMService:
    """A minimal stand-in for `LLMGateway` that implements the real
    `accounting_inputs_for` shape (unlike `_FakeLLMService`, which lacks it
    entirely and only exercises `_resolve_span_budget`'s degrade-gracefully
    fallback) - so a test through this fixture actually exercises the real
    accounting path, not the fallback estimate.
    """

    def __init__(self, capacity_tokens: int, reserve_tokens: int, chars_per_token: float = 1.0):
        self.calls = []
        self._responses = []
        self._capacity_tokens = capacity_tokens
        self._reserve_tokens = reserve_tokens
        self._chars_per_token = chars_per_token
        self.repository = SimpleNamespace(
            get_configuration=lambda cfg_id: SimpleNamespace(memory_reflection=True, provider_options=None)
        )

    def queue(self, content_or_exc) -> None:
        self._responses.append(content_or_exc)

    def accounting_inputs_for(self, config, options_override=None):
        return SimpleNamespace(
            capacity=SimpleNamespace(capacity_tokens=self._capacity_tokens, source="config"),
            reserve_tokens=self._reserve_tokens,
            counter=lambda text: math.ceil(len(text) / self._chars_per_token),
            messages_counter=None,
            image_tokens_override=None,
        )

    async def generate_with_history(self, messages, llm_id, options_override=None):
        self.calls.append(messages[0]["content"])
        item = self._responses.pop(0) if self._responses else "[]"
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(content=item)


class TestReflectionSmallCapacityBudget(PersistenceTestBase):
    """`_resolve_span_budget` must account for the fixed reflection prompt
    and the response reservation using the real gateway accounting, not a
    flat fraction of the window - a small `context_window` where the fixed
    prompt plus the output reservation alone approach or exceed capacity
    must be sized honestly (down to "no span can run"), never handed the
    same generous default a huge window would get."""

    def setUp(self):
        super().setUp()
        self.chat_repo = ChatRepository()
        self.user_id = self.create_test_user()

    def tearDown(self):
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute("DELETE FROM chat_messages")
                cursor.execute("DELETE FROM chat_sessions")
                cursor.execute("DELETE FROM users")
        except Exception:
            pass
        super().tearDown()

    def _session_with_messages(self, turns):
        session = self.chat_repo.create_session(user_id=self.user_id, llm_config_id="llm-1")
        for role, content in turns:
            self.chat_repo.add_message(session_id=session.id, role=role, content=content)
        return session.id

    def test_fixed_prompt_alone_exceeding_capacity_runs_no_span(self):
        asyncio.run(self._async_exhausted())

    async def _async_exhausted(self):
        # 1 "token" per character (extreme but deterministic regardless of
        # the exact prompt wording): a tiny capacity/reserve guarantees the
        # multi-hundred-char fixed prompt alone can't fit, however it reads.
        llm = _FakeGatewayLLMService(capacity_tokens=100, reserve_tokens=20, chars_per_token=1.0)
        runtime = ChatRuntime(
            chat_repository=self.chat_repo,
            llm_service=llm,
            response_processor=Mock(),
            plugin_registry=Mock(),
            chat_mode_registry=_mode_registry(),
        )
        runtime.llm_memory_repository = Mock()
        session_id = self._session_with_messages([
            ("user", "q0"), ("assistant", "a0"),
            ("user", "q1"), ("assistant", "a1"),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        ])

        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            saved = await runtime.reflection_generator.reflect(session_id)

        assert saved == []
        assert len(llm.calls) == 0  # no LLM call was even attempted
        mock_ops.write_note.assert_not_called()
        assert (self.chat_repo.get_session(session_id).metadata or {}).get("memory_reflection") is None

    def test_real_accounting_yields_a_materially_smaller_span_than_the_default(self):
        asyncio.run(self._async_small_but_viable())

    async def _async_small_but_viable(self):
        # A real (if tiny) capacity, reserve and per-char counter - proves
        # the gateway's actual numbers drive the span, not the 12,000-char
        # default the old flat-fraction heuristic (or the no-accounting
        # fallback) would have produced for an "unknown" capacity.
        llm = _FakeGatewayLLMService(capacity_tokens=1000, reserve_tokens=200, chars_per_token=4.0)
        runtime = ChatRuntime(
            chat_repository=self.chat_repo,
            llm_service=llm,
            response_processor=Mock(),
            plugin_registry=Mock(),
            chat_mode_registry=_mode_registry(),
        )
        runtime.llm_memory_repository = Mock()
        session_id = self._session_with_messages([
            ("user", "q0"), ("assistant", "a0"),
            ("user", "q1"), ("assistant", "a1"),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"), ("assistant", "a3"),
        ])
        # One message far bigger than the small budget this capacity should
        # produce, but comfortably under the 12,000-char default.
        big = "y" * 3000
        self.chat_repo.add_message(session_id=session_id, role="user", content=big)

        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            mock_ops.write_note.return_value = Mock()
            llm.queue("[]")
            saved = await runtime.reflection_generator.reflect(session_id)

        assert saved == []
        assert len(llm.calls) == 1
        # The gateway-informed budget is far smaller than the message - it
        # must have been chunked, not sent whole (which the old flat-window
        # heuristic's ~12,000-char budget would have done easily).
        transcript = llm.calls[0]
        assert "y" * 3000 not in transcript
        stored = self.chat_repo.get_session(session_id).metadata["memory_reflection"]
        assert stored["pending_backlog"] is True


def _dense_messages_counter(chars_per_token: float, overhead_tokens: int = 0):
    """A whole-request counter denser than the fallback chars/token estimate
    (`context_budget.DEFAULT_CHARS_PER_TOKEN`), with a fixed template
    overhead standing in for real chat-template framing - the two things
    `_resolve_span_budget`'s old prompt-only estimate couldn't see."""
    def _count(system_message, messages, tool_schemas=None):
        text = (system_message or "") + "".join(m.get("content", "") for m in messages)
        return math.ceil(len(text) / chars_per_token) + overhead_tokens
    return _count


class _FakeNativeSend:
    """Stands in for `NativeLLMClient.generate_with_history` behind a real
    `LLMGateway` - records the fully-prepared (post budget-enforcement)
    request and replays queued responses."""

    def __init__(self):
        self.calls = []
        self._responses = []

    def queue(self, content_or_exc) -> None:
        self._responses.append(content_or_exc)

    async def __call__(self, messages, config, system_message, image_data=None, options_override=None):
        self.calls.append({"system_message": system_message, "messages": messages})
        item = self._responses.pop(0) if self._responses else "[]"
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(content=item)


class TestReflectionRealGatewayBudget(PersistenceTestBase):
    """Sizes a span through the REAL `LLMGateway.estimate_context_budget` -
    the exact whole-request check the final send enforces - rather than an
    independent estimate. A real `LLMGateway` with a fake native provider
    client (no model) proves a non-trivial configured system message and
    real chat-template overhead - both invisible to a prompt-only estimate -
    correctly shrink an oversized candidate to one that actually reaches
    the provider, and that successive passes make monotonic progress."""

    def setUp(self):
        super().setUp()
        self.chat_repo = ChatRepository()
        self.user_id = self.create_test_user()

    def tearDown(self):
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute("DELETE FROM chat_messages")
                cursor.execute("DELETE FROM chat_sessions")
                cursor.execute("DELETE FROM users")
        except Exception:
            pass
        super().tearDown()

    def _build_gateway_and_config(self):
        gateway = LLMGateway(llm_repository=Mock())
        # A non-trivial configured system message - invisible to an estimate
        # that only prices the fixed reflection prompt's own text.
        system_message = "You are a strict, formatting-conscious assistant. " * 10
        config = LLMConfig(
            id="llm-1", name="Reflect Test", type="native", enabled=True,
            base_url="http://internal", model="native-model",
            system_message=system_message, disable_system_prompt=False,
            memory_reflection=True, provider_options={"context_window": 2800},
            max_tokens=2000,
        )
        gateway.repository.get_configuration.return_value = config
        # Denser than DEFAULT_CHARS_PER_TOKEN (3.5) - the naive tokens->chars
        # conversion in `_resolve_span_budget`'s estimate over-allows at this
        # ratio, so the real recount must reject the first candidate and the
        # shrink loop must actually run, not just a better initial guess.
        gateway._native.token_counter = Mock(return_value=lambda text: math.ceil(len(text) / 2.0))
        gateway._native.messages_token_counter = Mock(
            return_value=_dense_messages_counter(2.0, overhead_tokens=300)
        )
        sender = _FakeNativeSend()
        gateway._native.generate_with_history = sender
        return gateway, config, sender

    def _runtime_with_session(self, gateway, turns):
        runtime = ChatRuntime(
            chat_repository=self.chat_repo,
            llm_service=gateway,
            response_processor=Mock(),
            plugin_registry=Mock(),
            chat_mode_registry=_mode_registry(),
        )
        runtime.llm_memory_repository = Mock()
        session = self.chat_repo.create_session(user_id=self.user_id, llm_config_id="llm-1")
        for role, content in turns:
            self.chat_repo.add_message(session_id=session.id, role=role, content=content)
        return runtime, session.id

    def test_oversized_estimate_shrinks_to_a_span_the_real_gateway_accepts(self):
        asyncio.run(self._async_shrinks_to_viable_span())

    async def _async_shrinks_to_viable_span(self):
        gateway, config, sender = self._build_gateway_and_config()
        runtime, session_id = self._runtime_with_session(gateway, [
            ("user", "q0 " + "z" * 100), ("assistant", "a0 " + "z" * 100),
            ("user", "q1 " + "z" * 100), ("assistant", "a1 " + "z" * 100),
            ("user", "q2 " + "z" * 100), ("assistant", "a2 " + "z" * 100),
            ("user", "q3 " + "z" * 100), ("assistant", "a3 " + "z" * 100),
        ])

        with patch("src.features.chat.reflection.memory_operations") as mock_ops:
            mock_ops.write_note.return_value = Mock()
            sender.queue("[]")

            saved = await runtime.reflection_generator.reflect(session_id)

        assert saved == []
        # The request actually reached the fake provider - a candidate the
        # real gateway rejected would have raised inside `reflect()` and
        # never gotten this far (no coverage claimed, no call recorded).
        assert len(sender.calls) == 1
        sent = sender.calls[0]
        # Independent confirmation: recomputing the real gateway's own
        # budget check on the EXACT request sent must not raise.
        gateway.estimate_context_budget(
            config, sent["system_message"], sent["messages"],
            options_override={"max_tokens": 800, "temperature": 0.2, "think": False},
        )

        stored = self.chat_repo.get_session(session_id).metadata.get("memory_reflection")
        assert stored is not None
        first_position = (stored["reflected_up_to_seq"], stored["reflected_up_to_offset"])

        # A follow-up pass makes monotonic progress - it must cover strictly
        # more than the first, never re-request the identical, already
        # covered span forever.
        sender.queue("[]")
        await runtime.reflection_generator.reflect(session_id, require_threshold=False)
        stored2 = self.chat_repo.get_session(session_id).metadata["memory_reflection"]
        second_position = (stored2["reflected_up_to_seq"], stored2["reflected_up_to_offset"])
        assert second_position > first_position
