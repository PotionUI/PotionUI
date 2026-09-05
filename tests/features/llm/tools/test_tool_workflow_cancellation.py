"""What the tool loop does when its caller walks away mid-turn.

Nothing in the loop probes for cancellation — there is no cancel token and no
checkpoint. What the three entry points do offer is ordinary structured
concurrency: `execute_with_tools` is a coroutine a caller can cancel, and both
streaming entry points are async generators a caller can `aclose()`. These
fixtures pin what that already does, so a later change to the loop cannot
quietly break the provider's own cleanup or let work continue past the point
the caller stopped consuming.

Each fixture asserts three things: the provider's `finally` ran, no tool
dispatched after the stop, and no further provider turn was requested.
"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from src.features.llm.clients.base import LLMResponse
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.executor import ToolExecutor
from src.features.llm.tools.registry import ToolRegistry

from tests.features.llm.tools.test_tool_workflow_traces import Turn, call


class CountingEchoTool(BaseTool):
    """Echoes, and counts how many times it was actually entered."""

    def __init__(self) -> None:
        self.runs = 0

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes a message back."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, data=f"echo: {kwargs.get('message', '')}")


class InterruptibleLLM:
    """Provider double whose Nth call parks forever inside a try/finally.

    `markers` records the cleanup that actually ran, so a fixture can prove the
    provider was unwound rather than abandoned.
    """

    def __init__(self, turns: List[Turn], config_type: str = "ollama",
                 block_on_call: Optional[int] = None,
                 block_after_chunks: Optional[int] = None):
        self._turns = list(turns)
        self._block_on_call = block_on_call
        self._block_after_chunks = block_after_chunks
        self.calls = 0
        self.markers: List[str] = []
        self.entered = asyncio.Event()

        class _Config:
            type = config_type
            provider_options: Dict[str, Any] = {}

        class _Repository:
            @staticmethod
            def get_configuration(llm_id):
                return _Config()

        self.repository = _Repository()

    def _take(self) -> Turn:
        turn = self._turns[self.calls]
        self.calls += 1
        return turn

    async def _park(self) -> None:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.markers.append("provider_unwound")

    async def generate_with_tools(self, messages, llm_id, tools=None, image_data=None,
                                  custom_system_message=None, mode=None, options_override=None):
        if self.calls + 1 == self._block_on_call:
            self.calls += 1
            await self._park()
        turn = self._take()
        return LLMResponse(
            content=turn.content,
            model="model-1",
            provider_id="p",
            tool_calls=list(turn.tool_calls or []),
        )

    def stream_with_tools(self, messages, llm_id, tools=None, image_data=None,
                          custom_system_message=None, mode=None, options_override=None):
        turn = self._take()
        events = turn.stream_events()
        block_after = self._block_after_chunks
        park = self._park
        markers = self.markers

        async def _gen():
            emitted = 0
            try:
                for event in events:
                    yield event
                    emitted += 1
                    if block_after is not None and emitted == block_after:
                        await park()
            finally:
                markers.append("stream_closed")

        return _gen()


def build(turns, tool, config_type="ollama", executor_cls=ToolExecutor, **llm_kwargs):
    registry = ToolRegistry()
    registry.register(tool)
    llm = InterruptibleLLM(turns, config_type=config_type, **llm_kwargs)
    return executor_cls(tool_registry=registry, llm_service=llm), llm


COMMON_ARGS = dict(
    llm_id="model-1",
    system_message="sys",
)

TOOL_THEN_BLOCK = [
    Turn(tool_calls=[call("call_1", "echo", {"message": "hi"})]),
    Turn(content="never reached"),
    Turn(content="never reached either"),
]


# ---------------------------------------------------------------------------
# (1) Cancelling the buffered entry points while a provider await is pending
# ---------------------------------------------------------------------------

async def cancel_buffered(executor_cls=ToolExecutor) -> Dict[str, Any]:
    """Cancel `execute_with_tools` during the second provider call."""
    tool = CountingEchoTool()
    executor, llm = build(TOOL_THEN_BLOCK, tool, executor_cls=executor_cls, block_on_call=2)

    task = asyncio.ensure_future(executor.execute_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tool_context=ToolContext(user_id="u1"),
        **COMMON_ARGS,
    ))
    await asyncio.wait_for(llm.entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    return {"markers": list(llm.markers), "tool_runs": tool.runs, "provider_calls": llm.calls}


async def cancel_legacy(executor_cls=ToolExecutor) -> Dict[str, Any]:
    """Cancel the legacy stream during the second provider call. A `native`
    config always routes there (see `_force_prompt_tools_for`)."""
    tool = CountingEchoTool()
    executor, llm = build(TOOL_THEN_BLOCK, tool, config_type="native",
                          executor_cls=executor_cls, block_on_call=2)

    events: List[Dict[str, Any]] = []

    async def consume():
        async for event in executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            tool_context=ToolContext(user_id="u1"),
            **COMMON_ARGS,
        ):
            events.append(event)

    task = asyncio.ensure_future(consume())
    await asyncio.wait_for(llm.entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    return {
        "markers": list(llm.markers),
        "tool_runs": tool.runs,
        "provider_calls": llm.calls,
        "event_types": [e["type"] for e in events],
    }


# ---------------------------------------------------------------------------
# (2) Closing the live stream generator after its first visible event
# ---------------------------------------------------------------------------

LIVE_TOKENS_THEN_TOOL = [
    Turn(content="hello", chunks=["hel", "lo"],
         tool_calls=[call("call_1", "echo", {"message": "hi"})]),
    Turn(content="never reached"),
]


async def close_live(executor_cls=ToolExecutor) -> Dict[str, Any]:
    """Stop consuming the live stream after its first token and `aclose()` it
    while the provider's own stream is still suspended mid-iteration."""
    tool = CountingEchoTool()
    executor, llm = build(LIVE_TOKENS_THEN_TOOL, tool, executor_cls=executor_cls)

    stream = executor.execute_with_tools_stream(
        messages=[{"role": "user", "content": "hi"}],
        tool_context=ToolContext(user_id="u1"),
        **COMMON_ARGS,
    )
    first = await stream.__anext__()
    await stream.aclose()
    await asyncio.sleep(0)
    return {
        "first": first,
        "markers": list(llm.markers),
        "tool_runs": tool.runs,
        "provider_calls": llm.calls,
    }


async def cancel_live(executor_cls=ToolExecutor) -> Dict[str, Any]:
    """Cancel the task consuming the live stream while the provider's own
    stream is parked in a pending await."""
    tool = CountingEchoTool()
    executor, llm = build(LIVE_TOKENS_THEN_TOOL, tool, executor_cls=executor_cls,
                          block_after_chunks=1)

    events: List[Dict[str, Any]] = []

    async def consume():
        async for event in executor.execute_with_tools_stream(
            messages=[{"role": "user", "content": "hi"}],
            tool_context=ToolContext(user_id="u1"),
            **COMMON_ARGS,
        ):
            events.append(event)

    task = asyncio.ensure_future(consume())
    await asyncio.wait_for(llm.entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    return {
        "markers": list(llm.markers),
        "tool_runs": tool.runs,
        "provider_calls": llm.calls,
        "event_types": [e["type"] for e in events],
    }


async def close_live_then_drain(executor_cls=ToolExecutor) -> Dict[str, Any]:
    """`close_live`, then force the loop to finalize any async generators
    still pending. Before LLM-10 this was the ONLY point at which the
    provider's cleanup became observable (see the historical note on
    `test_closing_the_live_stream_closes_the_provider_immediately` below);
    now it is a no-op — `before_drain` already carries the marker — kept as
    a regression guard against generator ownership quietly sliding back to
    depending on the loop's finalizer."""
    tool = CountingEchoTool()
    executor, llm = build(LIVE_TOKENS_THEN_TOOL, tool, executor_cls=executor_cls)

    stream = executor.execute_with_tools_stream(
        messages=[{"role": "user", "content": "hi"}],
        tool_context=ToolContext(user_id="u1"),
        **COMMON_ARGS,
    )
    await stream.__anext__()
    await stream.aclose()
    before_drain = list(llm.markers)
    await asyncio.get_running_loop().shutdown_asyncgens()
    return {
        "before_drain": before_drain,
        "after_drain": list(llm.markers),
        "tool_runs": tool.runs,
        "provider_calls": llm.calls,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancelling_the_buffered_turn_unwinds_the_provider(self):
        out = await cancel_buffered()
        assert out["markers"] == ["provider_unwound"]
        assert out["tool_runs"] == 1        # only the round before the cancel
        assert out["provider_calls"] == 2   # no third turn was requested

    @pytest.mark.asyncio
    async def test_cancelling_the_legacy_stream_unwinds_the_provider(self):
        out = await cancel_legacy()
        assert out["markers"] == ["provider_unwound"]
        assert out["tool_runs"] == 1
        assert out["provider_calls"] == 2
        # The tool round before the cancel was reported; no `done` ever was.
        assert out["event_types"] == ["tool_start", "tool_end"]

    @pytest.mark.asyncio
    async def test_cancelling_the_live_stream_unwinds_the_provider(self):
        out = await cancel_live()
        assert out["markers"] == ["provider_unwound", "stream_closed"]
        assert out["tool_runs"] == 0
        assert out["provider_calls"] == 1
        assert out["event_types"] == ["token"]

    @pytest.mark.asyncio
    async def test_closing_the_live_stream_stops_the_loop_at_once(self):
        """The caller-visible half of `aclose()`: no token after the one it
        stopped on, no tool dispatched, no further provider turn."""
        out = await close_live()
        assert out["first"] == {"type": "token", "data": {"content": "hel"}}
        assert out["tool_runs"] == 0
        assert out["provider_calls"] == 1

    @pytest.mark.asyncio
    async def test_closing_the_live_stream_closes_the_provider_immediately(self):
        """LLM-10: every generator between the outermost stream and the
        provider (`ToolExecutor.execute_with_tools_stream`,
        `ToolWorkflow.run`, `_LiveTurnSource.acquire`, and — outside this
        executor-level fixture, which stands `InterruptibleLLM` in for the
        gateway — `LLMGateway.stream_with_tools`) now owns an explicit close
        scope (`contextlib.aclosing`) over the child generator it delegates
        to. `aclose()` on the outermost stream therefore reaches the
        provider's own `finally` synchronously, at the declared close
        boundary — never deferred to the event loop's async-generator
        finalizer or to garbage collection.

        Historical note (the contract this test pinned before LLM-10):
        `aclose()` used to unwind only the generator it was called on. Every
        generator below it — the workflow, the turn source, the provider's
        own stream — was left for `loop.shutdown_asyncgens()` (or GC) to
        eventually finalize, so the provider's `finally` had NOT run when
        `aclose()` returned; observing it required cancelling the consuming
        task instead (see `test_cancelling_the_live_stream_...`), which
        propagates as an exception and unwinds every frame at once. That
        deferral is gone: `before_drain` below already carries the marker,
        and the trailing `shutdown_asyncgens()` in `close_live_then_drain`
        is now a no-op kept only as a regression guard.
        """
        out = await close_live_then_drain()
        assert out["before_drain"] == ["stream_closed"]
        assert out["after_drain"] == ["stream_closed"]
        assert out["tool_runs"] == 0
        assert out["provider_calls"] == 1
