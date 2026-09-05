"""The interruption/reconnect scenario against the REAL chat turn registry.

The `interruption_reconnect` scenario fixture scores a canned transcript's
observable shape (the tool isn't re-called merely because of a reconnect).
This test goes one level deeper and drives the actual
``src.features.chat.turns.ChatTurnRegistry`` through its public API only
(``start``/``get``/``stream``) — never touching its internals or editing the
module — to prove the replay contract those fixtures assume actually holds:
a subscriber that reconnects with ``after_seq`` sees the remaining events
exactly once, with no gap and no duplicate.
"""

import asyncio

import pytest

from src.features.chat.turns import ChatTurnRegistry


async def _fake_turn_events():
    yield {"event": "generation_status", "data": {"phase": "tool_call", "tool": "get_active_models"}}
    yield {"event": "tool_result", "data": {"tool": "get_active_models", "outcome": "ok"}}
    yield {"event": "message", "data": {"role": "assistant", "content": "You're generating with JuggernautXL v9."}}
    yield {"event": "done", "data": {}}


@pytest.mark.asyncio
async def test_reconnect_after_seq_replays_remaining_events_exactly_once():
    registry = ChatTurnRegistry()
    turn = registry.start("session-1", "user-1", _fake_turn_events)

    # A subscriber attached from the start sees every event once.
    first_events = [event async for event in turn.stream(after_seq=None)]
    assert [e["event"] for e in first_events] == [
        "generation_status", "tool_result", "message", "done",
    ]
    assert [e["seq"] for e in first_events] == sorted(e["seq"] for e in first_events)

    # A reconnect after the 2nd event (by seq) must see only what's newer -
    # never a re-emission of the tool call/result already delivered.
    cursor = first_events[1]["seq"]
    replay = registry.get("session-1")
    assert replay is not None
    reconnected_events = [event async for event in replay.stream(after_seq=cursor)]
    assert [e["event"] for e in reconnected_events] == ["message", "done"]
    assert all(e["seq"] > cursor for e in reconnected_events)


@pytest.mark.asyncio
async def test_reconnect_never_re_invokes_the_stream_factory():
    call_count = 0

    async def _counting_factory():
        nonlocal call_count
        call_count += 1
        yield {"event": "message", "data": {"role": "assistant", "content": "done"}}
        yield {"event": "done", "data": {}}

    registry = ChatTurnRegistry()
    turn = registry.start("session-2", "user-1", _counting_factory)
    await turn.done.wait()

    # Two independent reconnects after completion must not trigger a second
    # tool call/LLM turn - replay is read-only fan-out of what already ran.
    for _ in range(2):
        finished = registry.get("session-2")
        assert finished is not None
        events = [event async for event in finished.stream(after_seq=None)]
        assert any(e["event"] == "done" for e in events)

    assert call_count == 1
