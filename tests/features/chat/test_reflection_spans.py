"""Tests for the bounded-span reflection mechanism: a char-budget span with a
resumable {message_id, offset} cursor, per-session single-flight triggering
with one coalesced follow-up, and monotonic cursor bookkeeping that survives
two passes completing out of order. See `ChatReflectionGenerator`'s module
docstring in `src/features/chat/reflection.py` for the design this covers.
"""

import asyncio
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
