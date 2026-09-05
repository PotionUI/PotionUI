"""Tests for the backend-owned chat turn registry.

The turn must run to completion — including assistant-message persistence — even
when the SSE subscriber that started it drops mid-stream, and a late subscriber
must be able to replay the whole turn from the start.
"""

import asyncio
import json
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
        # Every event now carries a monotonic replay ``seq`` in addition to
        # the original event/data fields.
        assert [{k: v for k, v in ev.items() if k != "seq"} for ev in replayed] == events
        assert [ev["seq"] for ev in replayed] == [1, 2, 3]

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

    @pytest.mark.asyncio
    async def test_replay_is_read_only_and_never_reruns_the_factory(self):
        """Attaching subscribers (including after completion) must not re-invoke
        the stream factory — replay is pure fan-out, never a re-execution."""
        registry = ChatTurnRegistry()
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            return _make_stream([
                {"event": "tool_start", "data": {"tool_name": "delete_thing"}},
                {"event": "tool_end", "data": {"tool_name": "delete_thing", "success": True}},
                {"event": "done", "data": {}},
            ])

        turn = registry.start("s1", "u1", factory)

        # A subscriber attaches mid-turn...
        mid_stream = turn.stream()
        await mid_stream.__anext__()
        await mid_stream.aclose()

        await asyncio.wait_for(turn.done.wait(), timeout=2)

        # ...and two more attach after it's finished (a reload, then another tab).
        _ = [ev async for ev in turn.stream()]
        _ = [ev async for ev in turn.stream()]

        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Bounded replay buffer: caps, compaction, and reconnect cursors
# ---------------------------------------------------------------------------

class TestBoundedReplayBuffer:
    @pytest.mark.asyncio
    async def test_retained_buffer_plateaus_as_events_grow(self):
        """Retained event count and serialized bytes must stay bounded even as
        a turn emits far more events than the caps allow."""
        registry = ChatTurnRegistry(max_events_per_turn=20, max_replay_bytes_per_turn=10_000)
        events = [{"event": "token", "data": {"content": "x" * 20}} for _ in range(500)]
        events.append({"event": "done", "data": {}})

        turn = registry.start("s1", "u1", lambda: _make_stream(events))
        await asyncio.wait_for(turn.done.wait(), timeout=5)

        assert len(turn.events) <= 21  # cap + at most one compaction marker
        assert turn._total_bytes <= 10_000 + 8_500  # cap + one marker's own size

    @pytest.mark.asyncio
    async def test_essential_events_survive_compaction(self):
        """tool_start/tool_end/done are never dropped even when far more token
        deltas than the cap have been emitted around them."""
        registry = ChatTurnRegistry(max_events_per_turn=10, max_replay_bytes_per_turn=10_000)
        events = [{"event": "tool_start", "data": {"tool_name": "search"}}]
        events += [{"event": "token", "data": {"content": "y"}} for _ in range(200)]
        events.append({"event": "tool_end", "data": {"tool_name": "search", "success": True}})
        events.append({"event": "done", "data": {}})

        turn = registry.start("s1", "u1", lambda: _make_stream(events))
        await asyncio.wait_for(turn.done.wait(), timeout=5)

        kinds = [e["event"] for e in turn.events]
        assert "tool_start" in kinds
        assert "tool_end" in kinds
        assert kinds[-1] == "done"

    @pytest.mark.asyncio
    async def test_reconnect_after_compaction_gets_snapshot_then_resumes(self):
        """A subscriber whose cursor predates the compacted prefix gets a
        ``replay_snapshot`` marker (with the dropped text and a cursor) instead
        of a silent gap, followed by the events still retained."""
        registry = ChatTurnRegistry(max_events_per_turn=5, max_replay_bytes_per_turn=100_000)
        gate = asyncio.Event()

        async def factory_gen():
            for i in range(20):
                yield {"event": "token", "data": {"content": f"t{i}-"}}
            await gate.wait()
            yield {"event": "done", "data": {}}

        turn = registry.start("s1", "u1", factory_gen)
        while turn._compacted_through == 0:
            await asyncio.sleep(0)

        # The turn is still running (blocked on the gate), so read only the
        # replay batch a fresh subscriber is pre-loaded with — iterating
        # ``stream()`` itself would wait forever for the not-yet-sent sentinel.
        sub = turn.add_subscriber(after_seq=0)
        replayed = []
        while not sub.queue.empty():
            replayed.append(sub.queue.get_nowait())
        assert replayed[0]["event"] == "replay_snapshot"
        assert replayed[0]["seq"] == turn._compacted_through
        assert "t0-" in replayed[0]["data"]["text_so_far"]
        assert replayed[0]["data"]["cursor"] == turn._compacted_through

        gate.set()
        await asyncio.wait_for(turn.done.wait(), timeout=2)

        # A subscriber already past the marker's cursor never sees it again.
        # The turn is finished now, so a full stream() replay terminates.
        caught_up = [ev async for ev in turn.stream(after_seq=turn._compacted_through)]
        assert all(ev["event"] != "replay_snapshot" for ev in caught_up)
        assert caught_up[-1]["event"] == "done"

    @pytest.mark.asyncio
    async def test_slow_subscriber_gets_overflow_marker_without_blocking_producer(self):
        """A subscriber that never reads must not block the producer; once it
        does read, it gets an explicit overflow marker instead of silence."""
        registry = ChatTurnRegistry(subscriber_queue_maxsize=3)

        async def factory_gen():
            yield {"event": "token", "data": {"content": "0"}}
            await asyncio.sleep(0)  # deterministic handoff: let the subscriber attach and read this one
            for i in range(1, 60):
                yield {"event": "token", "data": {"content": str(i)}}
            yield {"event": "done", "data": {}}

        turn = registry.start("s1", "u1", factory_gen)

        # Force subscription (add_subscriber runs on the first __anext__), then
        # stop reading — a stalled browser tab, from the producer's view.
        stream = turn.stream()
        first = await stream.__anext__()
        assert first["event"] == "token"

        # The producer must finish the whole turn without ever blocking on us,
        # even though nothing further is read from `stream` until it's done.
        await asyncio.wait_for(turn.done.wait(), timeout=5)

        collected = [ev async for ev in stream]
        assert any(ev["event"] == "overflow" for ev in collected)
        assert collected[-1]["event"] == "done"

    @pytest.mark.asyncio
    async def test_finished_turn_evicted_after_ttl(self):
        """A finished turn past its TTL is reclaimed at the next lifecycle
        boundary, independent of the count cap."""
        registry = ChatTurnRegistry(finished_turn_ttl_seconds=0)
        turn = registry.start("s1", "u1", lambda: _make_stream([{"event": "done", "data": {}}]))
        await asyncio.wait_for(turn.done.wait(), timeout=2)

        # _finish() already triggered a reclamation pass via the on-change hook.
        assert registry.get("s1") is None

    @pytest.mark.asyncio
    async def test_pathological_group_reclaimed_once_all_finish(self):
        """When every retained turn is still running past the count cap, the
        eviction is a no-op — but once they finish, a later lifecycle boundary
        (not a new start()) reclaims them down to the cap."""
        registry = ChatTurnRegistry(max_retained_turns=2)
        gates = [asyncio.Event() for _ in range(3)]

        async def slow(i):
            yield {"event": "token", "data": {"content": "x"}}
            await gates[i].wait()
            yield {"event": "done", "data": {}}

        turns = [registry.start(f"s{i}", "u1", lambda i=i: slow(i)) for i in range(3)]
        while any(not t.events for t in turns):
            await asyncio.sleep(0)

        # All three still running: over the cap of 2, but nothing to evict yet.
        assert len(registry._turns) == 3

        for i, gate in enumerate(gates):
            gate.set()
            await asyncio.wait_for(turns[i].done.wait(), timeout=2)

        # Each finish() ran a reclamation pass; the group is back under the cap.
        assert len(registry._turns) <= 2


# ---------------------------------------------------------------------------
# Subscriber-local preload collapsing and essential-event bounding
# ---------------------------------------------------------------------------

class TestSubscriberPreloadAndEssentialBounding:
    @pytest.mark.asyncio
    async def test_preload_larger_than_subscriber_queue_collapses_coherently(self):
        """The retained buffer cap and one subscriber's queue depth are
        independent: a buffer far bigger than the queue must still produce a
        coherent snapshot+tail preload, never raise QueueFull."""
        registry = ChatTurnRegistry(
            max_events_per_turn=300, max_replay_bytes_per_turn=10_000_000,
            subscriber_queue_maxsize=50,
        )
        events = [{"event": "token", "data": {"content": "x"}} for _ in range(280)]
        events.append({"event": "done", "data": {}})

        turn = registry.start("s1", "u1", lambda: _make_stream(events))
        await asyncio.wait_for(turn.done.wait(), timeout=5)
        # The retained buffer itself is bounded only by max_events (300), not
        # by any one subscriber's queue depth (50) — confirms the two caps
        # are genuinely independent before checking the preload shrinks to fit.
        assert len(turn.events) > 50

        replayed = [ev async for ev in turn.stream()]  # turn is done; terminates on its own
        assert replayed[0]["event"] == "replay_snapshot"
        assert replayed[-1]["event"] == "done"
        assert len(replayed) <= 50

    @pytest.mark.asyncio
    async def test_finished_turn_replay_exactly_filling_queue_still_gets_sentinel(self):
        """A replay that exactly fills the subscriber's queue (with the
        sentinel appended right after) must not raise QueueFull."""
        registry = ChatTurnRegistry(subscriber_queue_maxsize=6)
        events = [{"event": "token", "data": {"content": str(i)}} for i in range(4)]
        events.append({"event": "done", "data": {}})  # 5 events total

        turn = registry.start("s1", "u1", lambda: _make_stream(events))
        await asyncio.wait_for(turn.done.wait(), timeout=2)
        assert len(turn.events) == 5  # no compaction at this scale; capacity = 6-1 = 5, exact fit

        replayed = [ev async for ev in turn.stream()]
        assert [e["event"] for e in replayed] == ["token", "token", "token", "token", "done"]

    @pytest.mark.asyncio
    async def test_oversized_essential_event_retained_as_truncated_reference(self):
        """An essential event whose payload exceeds the per-event budget is
        retained for replay as a small truncated reference, not its full body —
        live delivery is unaffected."""
        registry = ChatTurnRegistry(max_essential_event_bytes=100)
        big_preview = "x" * 5000
        events = [
            {"event": "tool_start", "data": {"tool_name": "search"}},
            {"event": "tool_end", "data": {"tool_name": "search", "success": True, "preview": big_preview}},
            {"event": "done", "data": {}},
        ]

        live_collected = []

        async def factory():
            async for ev in _make_stream(events):
                yield ev

        turn = registry.start("s1", "u1", factory)

        # A live subscriber attached before the oversized event is emitted
        # must still see it in full.
        stream = turn.stream()
        first = await stream.__anext__()
        assert first["event"] == "tool_start"

        async def drain():
            async for ev in stream:
                live_collected.append(ev)

        drain_task = asyncio.create_task(drain())
        await asyncio.wait_for(turn.done.wait(), timeout=2)
        await asyncio.wait_for(drain_task, timeout=2)

        live_tool_end = next(e for e in live_collected if e["event"] == "tool_end")
        assert live_tool_end["data"]["preview"] == big_preview  # full payload, live

        retained_tool_end = next(e for e in turn.events if e["event"] == "tool_end")
        assert retained_tool_end["data"]["truncated"] is True
        assert retained_tool_end["data"]["tool_name"] == "search"
        assert len(json.dumps(retained_tool_end)) < len(json.dumps(live_tool_end))

    @pytest.mark.asyncio
    async def test_essential_only_backlog_folds_oldest_keeps_newest(self):
        """With no compactable events at all, an essential-only backlog past
        the count cap folds its oldest entries into the snapshot marker
        (recorded as a dropped-essential count + seq range) rather than
        defeating the cap outright — the newest essential events survive."""
        registry = ChatTurnRegistry(max_events_per_turn=4, max_replay_bytes_per_turn=10_000_000)
        events = [{"event": "tool_start", "data": {"tool_name": f"t{i}"}} for i in range(10)]
        events.append({"event": "done", "data": {}})

        turn = registry.start("s1", "u1", lambda: _make_stream(events))
        await asyncio.wait_for(turn.done.wait(), timeout=5)

        assert len(turn.events) <= 5  # cap (4) + at most one marker
        marker = turn.events[0]
        assert marker["event"] == "replay_snapshot"
        assert marker["data"]["dropped_essential_count"] > 0
        assert len(marker["data"]["dropped_essential_seq_range"]) == 2

        # The newest essential events (closest to the end) survive, including
        # the terminal outcome.
        kinds = [e["event"] for e in turn.events[1:]]
        assert kinds[-1] == "done"
        assert "tool_start" in kinds


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
