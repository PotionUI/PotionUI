"""Tests for the backend-owned chat turn registry.

The turn must run to completion — including assistant-message persistence — even
when the SSE subscriber that started it drops mid-stream, and a late subscriber
must be able to replay the whole turn from the start.
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.features.chat.runtime import ChatRuntime
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.chat.dto import MessageResponse, ToolApprovalRequest
from src.features.chat.routes import ChatController
from src.features.chat.turns import ChatTurnRegistry, TurnAlreadyRunningError
from src.platform.security.user import User


def _mode_registry() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


async def _make_stream(events, *, delay=0.0):
    """An async generator yielding the given event dicts, optionally spaced out."""
    for ev in events:
        if delay:
            await asyncio.sleep(delay)
        yield ev


# ---------------------------------------------------------------------------
# Registry mechanics (fake stream factory)
# ---------------------------------------------------------------------------

class TestTurnRegistryMechanics:
    @pytest.mark.asyncio
    async def test_turn_runs_to_completion_after_subscriber_disconnects(self):
        """Dropping the subscriber mid-stream must not stop the turn."""
        registry = ChatTurnRegistry()
        events = [
            {"event": "message_created", "data": {"user_message_id": "u1"}},
            {"event": "token", "data": {"content": "a"}},
            {"event": "token", "data": {"content": "b"}},
            {"event": "done", "data": {"assistant_message": {"id": "a1"}}},
        ]

        def factory():
            return _make_stream(events, delay=0.01)

        turn = registry.start("s1", "u1", factory)

        # Subscribe, read the first event, then disconnect.
        stream = turn.stream()
        first = await stream.__anext__()
        assert first["event"] == "message_created"
        await stream.aclose()

        # The turn keeps going and finishes on its own.
        await asyncio.wait_for(turn.done.wait(), timeout=2)
        assert turn.status == "completed"
        assert [e["event"] for e in turn.events] == [
            "message_created", "token", "token", "done",
        ]

    @pytest.mark.asyncio
    async def test_late_subscriber_replays_full_sequence(self):
        """A subscriber that attaches after the turn ends still sees every event."""
        registry = ChatTurnRegistry()
        events = [
            {"event": "message_created", "data": {}},
            {"event": "token", "data": {"content": "x"}},
            {"event": "done", "data": {}},
        ]
        turn = registry.start("s1", "u1", lambda: _make_stream(events))
        await asyncio.wait_for(turn.done.wait(), timeout=2)

        replayed = [ev async for ev in turn.stream()]
        assert replayed == events

    @pytest.mark.asyncio
    async def test_midflight_subscriber_gets_replay_then_live(self):
        """Attaching mid-turn replays buffered events, then streams the rest live."""
        registry = ChatTurnRegistry()
        gate = asyncio.Event()

        async def factory_gen():
            yield {"event": "message_created", "data": {}}
            yield {"event": "token", "data": {"content": "1"}}
            await gate.wait()
            yield {"event": "token", "data": {"content": "2"}}
            yield {"event": "done", "data": {}}

        turn = registry.start("s1", "u1", factory_gen)

        # Let the first two events buffer before we attach.
        while len(turn.events) < 2:
            await asyncio.sleep(0)

        collected = []

        async def consume():
            async for ev in turn.stream():
                collected.append(ev)

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)  # let the consumer replay the buffer
        gate.set()
        await asyncio.wait_for(consumer, timeout=2)

        assert [e["event"] for e in collected] == [
            "message_created", "token", "token", "done",
        ]

    @pytest.mark.asyncio
    async def test_second_turn_rejected_while_running(self):
        """One active turn per session: a second start raises."""
        registry = ChatTurnRegistry()
        gate = asyncio.Event()

        async def slow():
            yield {"event": "token", "data": {"content": "x"}}
            await gate.wait()

        turn = registry.start("s1", "u1", lambda: slow())
        while not turn.events:
            await asyncio.sleep(0)

        with pytest.raises(TurnAlreadyRunningError):
            registry.start("s1", "u1", lambda: slow())

        gate.set()
        await asyncio.wait_for(turn.done.wait(), timeout=2)

        # After it finishes, a new turn for the same session is allowed again.
        turn2 = registry.start("s1", "u1", lambda: _make_stream([{"event": "done", "data": {}}]))
        await asyncio.wait_for(turn2.done.wait(), timeout=2)

    @pytest.mark.asyncio
    async def test_explicit_cancel_stops_and_emits_cancelled(self):
        """request_cancel finishes the turn as cancelled with a cancelled event."""
        registry = ChatTurnRegistry()

        async def forever():
            yield {"event": "token", "data": {"content": "x"}}
            await asyncio.Event().wait()  # never completes

        turn = registry.start("s1", "u1", lambda: forever())
        while not turn.events:
            await asyncio.sleep(0)

        turn.request_cancel()
        await asyncio.wait_for(turn.done.wait(), timeout=2)

        assert turn.status == "cancelled"
        assert turn.events[-1]["event"] == "generation_cancelled"

    @pytest.mark.asyncio
    async def test_turn_timeout_emits_error(self):
        """The safety timeout finishes a wedged turn as an error."""
        registry = ChatTurnRegistry(turn_timeout_seconds=0.05)

        async def wedged():
            yield {"event": "token", "data": {"content": "x"}}
            await asyncio.sleep(5)

        turn = registry.start("s1", "u1", lambda: wedged())
        await asyncio.wait_for(turn.done.wait(), timeout=2)

        assert turn.status == "error"
        assert turn.events[-1]["data"]["error"] == "turn_timeout"

    @pytest.mark.asyncio
    async def test_active_and_get_semantics(self):
        """active() only returns a running turn; get() returns the retained one."""
        registry = ChatTurnRegistry()
        turn = registry.start("s1", "u1", lambda: _make_stream([{"event": "done", "data": {}}]))
        await asyncio.wait_for(turn.done.wait(), timeout=2)

        assert registry.active("s1") is None
        assert registry.get("s1") is turn


# ---------------------------------------------------------------------------
# Persistence survives disconnect (real ChatRuntime, mocked repo/LLM)
# ---------------------------------------------------------------------------

class TestPersistenceSurvivesDisconnect:
    def _manager(self, chunks):
        repo = Mock()
        session = Mock()
        session.id = "session-123"
        session.user_id = "user-123"
        session.status = "active"
        session.llm_config_id = "llm-1"
        session.mode = "generation"
        session.metadata = None
        repo.get_session.return_value = session
        repo.get_conversation_history.return_value = []

        user_msg = MessageResponse(id="msg-user", session_id="session-123", role="user", content="hi")
        assistant_msg = MessageResponse(
            id="msg-asst", session_id="session-123", role="assistant", content="".join(chunks)
        )
        repo.add_message.side_effect = [user_msg, assistant_msg]

        async def _llm_gen():
            for c in chunks:
                await asyncio.sleep(0.01)
                yield {"type": "token", "content": c}
            yield {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None}

        llm = Mock()
        llm.stream_with_history = Mock(return_value=_llm_gen())

        processor = Mock()
        processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        plugins = Mock()
        ctx = Mock()
        ctx.data = {}
        plugins.execute_hook.return_value = (ctx, [])

        manager = ChatRuntime(
            chat_repository=repo,
            llm_service=llm,
            response_processor=processor,
            plugin_registry=plugins,
            chat_mode_registry=_mode_registry(),
        )
        return manager, repo

    @pytest.mark.asyncio
    async def test_assistant_message_persisted_after_subscriber_drops(self):
        chunks = ["Hello", " ", "world"]
        manager, repo = self._manager(chunks)
        registry = ChatTurnRegistry()

        def factory():
            return manager.send_message_stream(
                session_id="session-123", user_id="user-123", content="hi",
            )

        turn = registry.start("session-123", "user-123", factory)

        # Attach, read one event, then bail out like a page reload would.
        stream = turn.stream()
        await stream.__anext__()
        await stream.aclose()

        await asyncio.wait_for(turn.done.wait(), timeout=5)

        # Two persists: the user message AND the assistant message — the latter
        # is the response that used to be lost on disconnect.
        assert repo.add_message.call_count == 2
        assert repo.add_message.call_args_list[1].kwargs["role"] == "assistant"
        assert repo.add_message.call_args_list[1].kwargs["content"] == "Hello world"
        # The full turn is buffered for a late reattach.
        assert turn.events[-1]["event"] == "done"


# ---------------------------------------------------------------------------
# Approval is orthogonal to the streaming connection / turn
# ---------------------------------------------------------------------------

class TestApprovalAcrossConnections:
    @pytest.mark.asyncio
    async def test_approval_works_with_no_active_turn(self):
        """Approving a pending tool is a plain POST — it must not depend on the
        original stream still being connected, nor on an active turn existing."""
        manager = Mock()
        manager.get_session.return_value = Mock(id="s1")
        manager.approve_tool_execution = AsyncMock(return_value={
            "result": {"success": True, "data": "done", "error": None},
            "assistant_message": {"id": "a2", "role": "assistant", "content": "Removed it."},
        })

        registry = ChatTurnRegistry()
        controller = ChatController(chat_runtime=manager, turn_registry=registry)

        # No turn was ever started for this session.
        assert registry.active("s1") is None

        user = User(id="u1", username="u", email="e@x.com", password_hash="h", account_type="USER")
        result = await controller.approve_tool(
            "s1", ToolApprovalRequest(message_id="m1", tool_index=0, approved=True), user,
        )

        assert result.success is True
        manager.approve_tool_execution.assert_awaited_once()
        assert result.data["assistant_message"]["id"] == "a2"
