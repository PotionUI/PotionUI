"""Completion-outcome propagation through the tool loop's three entry paths.

The workflow's own terminal `reason` ("answer"/"truncated"/"ambiguous"/
"budget" -- see `ToolWorkflow._decide`) decides whether a round's completion
outcome is a genuine model completion worth persisting: a tool-call dispatch
round never reaches a `done`/`Completed` event at all (the loop keeps going),
and a rescue's canned fallback message ("truncated"/"ambiguous") is never
attributed a completion even though the underlying API call that produced it
really did report one -- only "answer" and "budget" carry their round's
completion forward. Exercised on all three of `ToolExecutor`'s entry points
(buffered, legacy buffered-per-iteration stream, live per-token stream) so a
regression in any one of them is caught here rather than only in the wiring
one layer up (see `tests/features/chat/test_conversation_completion.py` for
that layer).
"""

import copy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.features.llm.clients.base import LLMResponse
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.executor import ToolExecutor
from src.features.llm.tools.registry import ToolRegistry

STOP = {"reason": "stop", "raw": "stop"}
LENGTH = {"reason": "length", "raw": "length"}


@dataclass
class Turn:
    """One scripted assistant turn; `completion` is the raw round's outcome
    the provider would have reported, independent of what the workflow does
    with it."""

    content: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    completion: Optional[Dict[str, Any]] = None

    def stream_events(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if self.content:
            events.append({"type": "token", "content": self.content})
        if self.tool_calls:
            events.append({"type": "tool_calls", "tool_calls": copy.deepcopy(self.tool_calls)})
        events.append({
            "type": "usage",
            "tokens_used": 1,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "completion": self.completion,
        })
        return events


class ScriptedLLM:
    """Provider double that hands out pre-scripted turns in order."""

    def __init__(self, turns: List[Turn], config_type: str):
        self._turns = list(turns)
        self._index = 0
        config = SimpleNamespace(type=config_type, provider_options={})
        self.repository = SimpleNamespace(get_configuration=lambda llm_id: config)

    def _next(self) -> Turn:
        turn = self._turns[self._index]
        self._index += 1
        return turn

    async def generate_with_tools(self, messages, llm_id, tools=None, image_data=None,
                                  custom_system_message=None, mode=None, options_override=None):
        turn = self._next()
        return LLMResponse(
            content=turn.content,
            model="model-1",
            provider_id="p",
            tool_calls=copy.deepcopy(turn.tool_calls) or [],
            completion=turn.completion,
        )

    def stream_with_tools(self, messages, llm_id, tools=None, image_data=None,
                          custom_system_message=None, mode=None, options_override=None):
        turn = self._next()

        async def _gen():
            for event in turn.stream_events():
                yield event

        return _gen()


class EchoTool(BaseTool):
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
        return ToolResult(success=True, data=f"echo: {kwargs.get('message', '')}")


def call(call_id: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def build_executor(turns: List[Turn], config_type: str) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(EchoTool())
    llm = ScriptedLLM(turns, config_type=config_type)
    return ToolExecutor(tool_registry=registry, llm_service=llm)


async def run_non_stream(turns: List[Turn]):
    executor = build_executor(turns, "ollama")
    response, _ = await executor.execute_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        llm_id="model-1",
        system_message="sys",
        tool_context=ToolContext(user_id="u1"),
    )
    return response


async def run_stream(turns: List[Turn], config_type: str) -> Dict[str, Any]:
    """config_type "native" forces the legacy buffered-per-iteration stream;
    anything else takes the live per-token stream (see
    `ToolExecutor._force_prompt_tools_for`)."""
    executor = build_executor(turns, config_type)
    done_data: Dict[str, Any] = {}
    async for event in executor.execute_with_tools_stream(
        messages=[{"role": "user", "content": "hi"}],
        llm_id="model-1",
        system_message="sys",
        tool_context=ToolContext(user_id="u1"),
    ):
        if event["type"] == "done":
            done_data = event["data"]
    return done_data


# ---------------------------------------------------------------------------
# A tool-dispatch round's completion never surfaces; the final answer's does
# ---------------------------------------------------------------------------

TOOL_THEN_ANSWER = [
    Turn(tool_calls=[call("call_1", "echo", {"message": "hi"})], completion=LENGTH),
    Turn(content="final answer", completion=STOP),
]


class TestToolRoundNeverSurfacesItsCompletion:
    @pytest.mark.asyncio
    async def test_buffered(self):
        response = await run_non_stream(TOOL_THEN_ANSWER)
        assert response.completion == STOP

    @pytest.mark.asyncio
    async def test_live_stream(self):
        done = await run_stream(TOOL_THEN_ANSWER, "ollama")
        assert done["completion"] == STOP

    @pytest.mark.asyncio
    async def test_legacy_stream(self):
        done = await run_stream(TOOL_THEN_ANSWER, "native")
        assert done["completion"] == STOP


# ---------------------------------------------------------------------------
# A plain-text final answer that hit the output limit persists "length"
# ---------------------------------------------------------------------------

LENGTH_ANSWER = [Turn(content="this got cut off mid", completion=LENGTH)]


class TestFinalAnswerLengthIsPersisted:
    @pytest.mark.asyncio
    async def test_buffered(self):
        response = await run_non_stream(LENGTH_ANSWER)
        assert response.completion == LENGTH

    @pytest.mark.asyncio
    async def test_live_stream(self):
        done = await run_stream(LENGTH_ANSWER, "ollama")
        assert done["completion"] == LENGTH

    @pytest.mark.asyncio
    async def test_legacy_stream(self):
        done = await run_stream(LENGTH_ANSWER, "native")
        assert done["completion"] == LENGTH


# ---------------------------------------------------------------------------
# A truncated-tool-call rescue's canned fallback is never attributed a
# completion, even though every underlying round genuinely reported "length"
# ---------------------------------------------------------------------------

_OPEN_UNCLOSED = '<tool_call>{"name": "echo", "arg'
TRUNCATED_FALLBACK = [
    Turn(content=_OPEN_UNCLOSED, completion=LENGTH),
    Turn(content=_OPEN_UNCLOSED, completion=LENGTH),
    Turn(content=_OPEN_UNCLOSED, completion=LENGTH),
]


class TestRescueFallbackNeverCarriesACompletion:
    @pytest.mark.asyncio
    async def test_buffered(self):
        response = await run_non_stream(TRUNCATED_FALLBACK)
        assert response.completion is None
        assert "cut off" in response.content

    @pytest.mark.asyncio
    async def test_live_stream(self):
        done = await run_stream(TRUNCATED_FALLBACK, "ollama")
        assert done.get("completion") is None
        assert "cut off" in done["full_content"]

    @pytest.mark.asyncio
    async def test_legacy_stream(self):
        done = await run_stream(TRUNCATED_FALLBACK, "native")
        assert done.get("completion") is None
        assert "cut off" in done["full_content"]
