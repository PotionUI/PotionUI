"""Frozen traces for the tool loop's three entry paths.

The executor drives the same tool workflow three ways: a buffered
non-streaming turn (`execute_with_tools`), a buffered turn rendered as stream
events (`execute_with_tools_stream` on a force_prompt_tools/native config,
which delegates to the legacy generator), and a live per-token stream
(`execute_with_tools_stream` on every other config).

Each test here pins the exact ordered event sequence AND the exact message
history the provider is handed on every call, for one scenario on one path.
`test_paths_agree_*` then asserts the paths agree once token granularity is
collapsed, and names the two places they deliberately do not.

A scripted provider double supplies deterministic turns; a scenario that runs
out of scripted turns fails loudly rather than hanging or improvising.
"""

import copy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.features.llm.clients.base import LLMResponse
from src.features.llm.tools.base import (
    BaseTool,
    ToolApprovalPreview,
    ToolContext,
    ToolResult,
)
from src.features.llm.tools.executor import ToolExecutor
from src.features.llm.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Scripted provider
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    """One scripted assistant turn, renderable to either provider surface."""

    content: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    # Token split for the live path; defaults to the whole content as one token.
    chunks: Optional[List[str]] = None
    tokens_used: Optional[int] = None

    def stream_events(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for chunk in (self.chunks if self.chunks is not None else ([self.content] if self.content else [])):
            events.append({"type": "token", "content": chunk})
        if self.tool_calls:
            events.append({"type": "tool_calls", "tool_calls": copy.deepcopy(self.tool_calls)})
        if self.tokens_used is not None:
            events.append({"type": "usage", "tokens_used": self.tokens_used,
                           "prompt_tokens": 1, "completion_tokens": 2})
        return events


class ScriptedLLM:
    """Provider double that hands out pre-scripted turns and records every call."""

    def __init__(self, turns: List[Turn], config_type: str = "ollama"):
        self._turns = list(turns)
        self._index = 0
        self.calls: List[Dict[str, Any]] = []
        config = SimpleNamespace(type=config_type, provider_options={})
        self.repository = SimpleNamespace(get_configuration=lambda llm_id: config)

    def _next(self, kind: str, messages, tools, image_data) -> Turn:
        self.calls.append({
            "kind": kind,
            "messages": condense(messages),
            "tools_offered": None if tools is None else len(tools),
            "image_data": image_data,
        })
        if self._index >= len(self._turns):
            raise AssertionError(
                f"scripted provider exhausted: call {self._index + 1} ({kind}) has no scripted turn"
            )
        turn = self._turns[self._index]
        self._index += 1
        return turn

    async def generate_with_tools(self, messages, llm_id, tools=None, image_data=None,
                                  custom_system_message=None, mode=None, options_override=None):
        turn = self._next("buffered", messages, tools, image_data)
        return LLMResponse(
            content=turn.content,
            model="model-1",
            provider_id="p",
            tool_calls=copy.deepcopy(turn.tool_calls) or [],
            tokens_used=turn.tokens_used,
        )

    def stream_with_tools(self, messages, llm_id, tools=None, image_data=None,
                          custom_system_message=None, mode=None, options_override=None):
        turn = self._next("stream", messages, tools, image_data)

        async def _gen():
            for event in turn.stream_events():
                yield event

        return _gen()


def condense(messages) -> List[tuple]:
    """A message history reduced to the parts the loop's behaviour depends on."""
    out = []
    for message in messages:
        calls = message.get("tool_calls")
        call_shape = tuple(
            (call.get("id"), call.get("function", {}).get("name"))
            for call in calls
        ) if calls else None
        out.append((
            message.get("role"),
            message.get("content"),
            call_shape,
            message.get("tool_call_id"),
        ))
    return out


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

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


class BoomTool(BaseTool):
    @property
    def name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "Always fails."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        return ToolResult(success=False, data="", error="boom failed")


class ApprovalTool(BaseTool):
    @property
    def name(self) -> str:
        return "apply_change"

    @property
    def description(self) -> str:
        return "Applies a change after approval."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    @property
    def requires_approval(self) -> bool:
        return True

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            data="preview only",
            preview=ToolApprovalPreview(action="Apply", target="the form", items=["value"]),
        )

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        return ToolResult(success=True, data="applied")


def call(call_id: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


# ---------------------------------------------------------------------------
# Runners — one per path, all producing the same normalised trace shape
# ---------------------------------------------------------------------------

def build_executor(turns: List[Turn], config_type: str, tools: List[BaseTool]):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    llm = ScriptedLLM(turns, config_type=config_type)
    return ToolExecutor(tool_registry=registry, llm_service=llm), llm


def norm_tool_event(kind: str, data: Dict[str, Any]) -> tuple:
    """A tool_start/tool_end payload minus its wall-clock duration."""
    if kind == "tool_start":
        return ("tool_start", data["tool_name"], data.get("arguments"))
    return (
        "tool_end",
        data["tool_name"],
        data["success"],
        data["pending_approval"],
        data.get("arguments"),
        (data.get("preview") or {}).get("action"),
    )


async def run_non_stream(turns, tools, **kwargs) -> Dict[str, Any]:
    executor, llm = build_executor(turns, "ollama", tools)
    trace: List[tuple] = []
    response, executions = await executor.execute_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        llm_id="model-1",
        system_message="sys",
        tool_context=ToolContext(user_id="u1"),
        on_tool_event=lambda kind, data: trace.append(norm_tool_event(kind, data)),
        **kwargs,
    )
    # The buffered path has no pending flag on its return value — a paused
    # turn is signalled by the last execution's `pending_approval` and an
    # empty content. Folded in here so the terminal entry is comparable with
    # the streaming paths' `done` event.
    pending = bool(executions and executions[-1].pending_approval)
    trace.append(("final", response.content, response.rescues, response.tool_failures, pending))
    return {"trace": trace, "calls": llm.calls, "executions": executions, "response": response}


async def _run_stream(turns, tools, config_type, **kwargs) -> Dict[str, Any]:
    executor, llm = build_executor(turns, config_type, tools)
    trace: List[tuple] = []
    executions: List[Any] = []
    async for event in executor.execute_with_tools_stream(
        messages=[{"role": "user", "content": "hi"}],
        llm_id="model-1",
        system_message="sys",
        tool_context=ToolContext(user_id="u1"),
        **kwargs,
    ):
        kind = event["type"]
        data = event["data"]
        if kind == "token":
            trace.append(("token", data["content"]))
        elif kind == "status":
            trace.append(("status", data["step"], data["state"]))
        elif kind == "done":
            executions = data["tool_executions"]
            trace.append((
                "done",
                data["full_content"],
                data["pending_tool_approval"],
                data.get("rescues"),
                data.get("tool_failures"),
            ))
        else:
            trace.append(norm_tool_event(kind, data))
    return {"trace": trace, "calls": llm.calls, "executions": executions}


async def run_legacy(turns, tools, **kwargs) -> Dict[str, Any]:
    return await _run_stream(turns, tools, "native", **kwargs)


async def run_live(turns, tools, **kwargs) -> Dict[str, Any]:
    return await _run_stream(turns, tools, "ollama", **kwargs)


def decisions(trace: List[tuple]) -> List[tuple]:
    """A trace with token granularity collapsed and the terminal entry reduced
    to (content, pending) — the part every path is expected to agree on."""
    out: List[tuple] = []
    text = ""
    for entry in trace:
        if entry[0] == "token":
            text += entry[1]
            continue
        if entry[0] == "done":
            out.append(("end", entry[1], entry[2], entry[3], entry[4]))
            continue
        if entry[0] == "final":
            out.append(("end", entry[1], entry[4], entry[2], entry[3]))
            continue
        out.append(entry)
    return out


USER = ("user", "hi", None, None)


# ---------------------------------------------------------------------------
# Scenario scripts
# ---------------------------------------------------------------------------

ECHO_CALL = call("call_1", "echo", {"message": "hi"})

NORMAL = [
    Turn(tool_calls=[ECHO_CALL]),
    Turn(content="final answer", chunks=["final", " answer"], tokens_used=7),
]

FOLLOW_UP = [
    Turn(tool_calls=[call("call_1", "echo", {"message": "one"})]),
    Turn(tool_calls=[call("call_2", "echo", {"message": "two"})]),
    Turn(content="done twice"),
]

FAILURE = [
    Turn(tool_calls=[call("call_1", "boom", {"x": "a"})]),
    Turn(content="that failed"),
]

REPEAT = [
    Turn(tool_calls=[call("call_1", "boom", {"x": "a"})]),
    Turn(tool_calls=[call("call_2", "boom", {"x": "a"})]),
    Turn(content="giving up"),
]

RESCUE = [
    Turn(content='sure <tool_action type="echo">{"message": "hi"}</tool_action>'),
    Turn(content="rescued answer"),
]

APPROVAL = [
    Turn(tool_calls=[call("call_1", "apply_change", {"value": "v"})]),
]

LIMIT = [
    Turn(tool_calls=[call("call_1", "echo", {"message": "a"})]),
    Turn(tool_calls=[call("call_2", "echo", {"message": "b"})]),
    Turn(content="wrapped up"),
]

FORCED_KWARGS = {"forced_tool_call": {"name": "echo", "arguments": {"message": "forced"}}}
FORCED = [Turn(content="presented the forced result")]


# ---------------------------------------------------------------------------
# Frozen traces — non-streaming path
# ---------------------------------------------------------------------------

class TestNonStreamTraces:
    @pytest.mark.asyncio
    async def test_normal_tool_call(self):
        out = await run_non_stream(NORMAL, [EchoTool()])
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "hi"}),
            ("tool_end", "echo", True, False, None, None),
            ("final", "final answer", None, None, False),
        ]
        assert [c["messages"] for c in out["calls"]] == [
            [USER],
            [USER,
             ("assistant", "", (("call_1", "echo"),), None),
             ("tool", "echo: hi", None, "call_1")],
        ]

    @pytest.mark.asyncio
    async def test_forced_tool_call_runs_before_the_first_turn(self):
        out = await run_non_stream(FORCED, [EchoTool()], **FORCED_KWARGS)
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "forced"}),
            ("tool_end", "echo", True, False, None, None),
            ("final", "presented the forced result", None, None, False),
        ]
        assert out["calls"][0]["messages"] == [
            USER,
            ("assistant", "", (("forced_call_0", "echo"),), None),
            ("tool", "echo: forced", None, "forced_call_0"),
        ]

    @pytest.mark.asyncio
    async def test_rescued_near_miss_call(self):
        out = await run_non_stream(RESCUE, [EchoTool()])
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "hi"}),
            ("tool_end", "echo", True, False, None, None),
            ("final", "rescued answer",
             [{"tool_name": "echo", "repaired": True, "original_format": "tool_action_tag"}], None, False),
        ]
        assert out["calls"][1]["messages"] == [
            USER,
            ("assistant", "sure", (("rescue_0", "echo"),), None),
            ("tool", "echo: hi", None, "rescue_0"),
        ]

    @pytest.mark.asyncio
    async def test_follow_up_turns_carry_the_iteration_nudge(self):
        out = await run_non_stream(FOLLOW_UP, [EchoTool()], iteration_nudge="wrap up")
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "one"}),
            ("tool_end", "echo", True, False, None, None),
            ("tool_start", "echo", {"message": "two"}),
            ("tool_end", "echo", True, False, None, None),
            ("final", "done twice", None, None, False),
        ]
        nudge = ("system", "wrap up", None, None)
        assert out["calls"][0]["messages"][-1] != nudge
        assert out["calls"][1]["messages"][-1] == nudge
        assert out["calls"][2]["messages"][-1] == nudge

    @pytest.mark.asyncio
    async def test_tool_failure(self):
        out = await run_non_stream(FAILURE, [BoomTool()])
        assert out["trace"] == [
            ("tool_start", "boom", {"x": "a"}),
            ("tool_end", "boom", False, False, None, None),
            ("final", "that failed", None, {"boom": 1}, False),
        ]
        assert out["calls"][1]["messages"][-1] == ("tool", "Error: boom failed", None, "call_1")

    @pytest.mark.asyncio
    async def test_repeat_suppression_refuses_the_identical_failing_call(self):
        out = await run_non_stream(REPEAT, [BoomTool()])
        assert out["trace"] == [
            ("tool_start", "boom", {"x": "a"}),
            ("tool_end", "boom", False, False, None, None),
            ("tool_start", "boom", {"x": "a"}),
            ("tool_end", "boom", False, False, None, None),
            ("final", "giving up", None, {"boom": 2}, False),
        ]
        second_result = out["calls"][2]["messages"][-1]
        assert second_result[0] == "tool"
        assert "You already called boom with these exact arguments" in second_result[1]

    @pytest.mark.asyncio
    async def test_pending_user_decision_pauses_the_loop(self):
        out = await run_non_stream(APPROVAL, [ApprovalTool()])
        assert out["trace"] == [
            ("tool_start", "apply_change", {"value": "v"}),
            ("tool_end", "apply_change", True, True, {"value": "v"}, "Apply"),
            ("final", "", None, None, True),
        ]
        assert len(out["calls"]) == 1
        assert out["executions"][0].pending_approval is True

    @pytest.mark.asyncio
    async def test_iteration_limit_makes_a_final_tool_free_call(self):
        out = await run_non_stream(LIMIT, [EchoTool()], max_iterations=2)
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "a"}),
            ("tool_end", "echo", True, False, None, None),
            ("tool_start", "echo", {"message": "b"}),
            ("tool_end", "echo", True, False, None, None),
            ("final", "wrapped up", None, None, False),
        ]
        assert [c["tools_offered"] for c in out["calls"]] == [1, 1, 0]
        # The buffered path adds no wrap-up instruction and emits no status
        # event; both streaming paths do. See test_paths_agree_on_the_iteration_limit.
        assert out["calls"][2]["messages"][-1][0] == "tool"


# ---------------------------------------------------------------------------
# Frozen traces — legacy (buffered-per-iteration) streaming path
# ---------------------------------------------------------------------------

class TestLegacyStreamTraces:
    @pytest.mark.asyncio
    async def test_normal_tool_call(self):
        out = await run_legacy(NORMAL, [EchoTool()])
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "hi"}),
            ("tool_end", "echo", True, False, None, None),
            ("token", "final answer"),
            ("done", "final answer", False, None, None),
        ]
        assert [c["kind"] for c in out["calls"]] == ["buffered", "buffered"]

    @pytest.mark.asyncio
    async def test_forced_tool_call(self):
        out = await run_legacy(FORCED, [EchoTool()], **FORCED_KWARGS)
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "forced"}),
            ("tool_end", "echo", True, False, None, None),
            ("token", "presented the forced result"),
            ("done", "presented the forced result", False, None, None),
        ]

    @pytest.mark.asyncio
    async def test_rescued_near_miss_call(self):
        out = await run_legacy(RESCUE, [EchoTool()])
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "hi"}),
            ("tool_end", "echo", True, False, None, None),
            ("token", "rescued answer"),
            ("done", "rescued answer", False,
             [{"tool_name": "echo", "repaired": True, "original_format": "tool_action_tag"}], None),
        ]

    @pytest.mark.asyncio
    async def test_tool_failure(self):
        out = await run_legacy(FAILURE, [BoomTool()])
        assert out["trace"] == [
            ("tool_start", "boom", {"x": "a"}),
            ("tool_end", "boom", False, False, None, None),
            ("token", "that failed"),
            ("done", "that failed", False, None, {"boom": 1}),
        ]

    @pytest.mark.asyncio
    async def test_pending_user_decision_pauses_the_loop(self):
        out = await run_legacy(APPROVAL, [ApprovalTool()])
        assert out["trace"] == [
            ("tool_start", "apply_change", {"value": "v"}),
            ("tool_end", "apply_change", True, True, {"value": "v"}, "Apply"),
            ("done", "", True, None, None),
        ]

    @pytest.mark.asyncio
    async def test_iteration_limit_signals_and_instructs(self):
        out = await run_legacy(LIMIT, [EchoTool()], max_iterations=2)
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "a"}),
            ("tool_end", "echo", True, False, None, None),
            ("tool_start", "echo", {"message": "b"}),
            ("tool_end", "echo", True, False, None, None),
            ("status", "tool_budget_exhausted", "completed"),
            ("token", "wrapped up"),
            ("done", "wrapped up", False, None, None),
        ]
        final_messages = out["calls"][2]["messages"]
        assert final_messages[-1][0] == "system"
        assert "Tool budget for this turn is exhausted" in final_messages[-1][1]
        assert out["calls"][2]["tools_offered"] == 0


# ---------------------------------------------------------------------------
# Frozen traces — live per-token streaming path
# ---------------------------------------------------------------------------

class TestLiveStreamTraces:
    @pytest.mark.asyncio
    async def test_normal_tool_call_streams_the_answer_token_by_token(self):
        out = await run_live(NORMAL, [EchoTool()])
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "hi"}),
            ("tool_end", "echo", True, False, None, None),
            ("token", "final"),
            ("token", " answer"),
            ("done", "final answer", False, None, None),
        ]
        assert [c["kind"] for c in out["calls"]] == ["stream", "stream"]

    @pytest.mark.asyncio
    async def test_forced_tool_call(self):
        out = await run_live(FORCED, [EchoTool()], **FORCED_KWARGS)
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "forced"}),
            ("tool_end", "echo", True, False, None, None),
            ("token", "presented the forced result"),
            ("done", "presented the forced result", False, None, None),
        ]

    @pytest.mark.asyncio
    async def test_rescued_near_miss_call_is_never_streamed_raw(self):
        out = await run_live(RESCUE, [EchoTool()])
        assert out["trace"] == [
            ("token", "sure "),
            ("tool_start", "echo", {"message": "hi"}),
            ("tool_end", "echo", True, False, None, None),
            ("token", "rescued answer"),
            ("done", "rescued answer", False,
             [{"tool_name": "echo", "repaired": True, "original_format": "tool_action_tag"}], None),
        ]

    @pytest.mark.asyncio
    async def test_tool_failure(self):
        out = await run_live(FAILURE, [BoomTool()])
        assert out["trace"] == [
            ("tool_start", "boom", {"x": "a"}),
            ("tool_end", "boom", False, False, None, None),
            ("token", "that failed"),
            ("done", "that failed", False, None, {"boom": 1}),
        ]

    @pytest.mark.asyncio
    async def test_pending_user_decision_pauses_the_loop(self):
        out = await run_live(APPROVAL, [ApprovalTool()])
        assert out["trace"] == [
            ("tool_start", "apply_change", {"value": "v"}),
            ("tool_end", "apply_change", True, True, {"value": "v"}, "Apply"),
            ("done", "", True, None, None),
        ]

    @pytest.mark.asyncio
    async def test_iteration_limit_signals_and_instructs(self):
        out = await run_live(LIMIT, [EchoTool()], max_iterations=2)
        assert out["trace"] == [
            ("tool_start", "echo", {"message": "a"}),
            ("tool_end", "echo", True, False, None, None),
            ("tool_start", "echo", {"message": "b"}),
            ("tool_end", "echo", True, False, None, None),
            ("status", "tool_budget_exhausted", "completed"),
            ("token", "wrapped up"),
            ("done", "wrapped up", False, None, None),
        ]
        final_messages = out["calls"][2]["messages"]
        assert final_messages[-1][0] == "system"
        assert "Tool budget for this turn is exhausted" in final_messages[-1][1]
        assert out["calls"][2]["tools_offered"] is None

    @pytest.mark.asyncio
    async def test_inline_tool_call_block_dispatches_before_the_turn_ends(self):
        """Live-only: a `<tool_call>` closing mid-stream runs immediately, and
        nothing inside the block is forwarded as a token."""
        turns = [
            Turn(content='thinking<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>tail',
                 chunks=['thinking<tool_call>{"name": "echo", ',
                         '"arguments": {"message": "hi"}}</tool_call>',
                         'tail']),
            Turn(content="after inline"),
        ]
        out = await run_live(turns, [EchoTool()])
        assert out["trace"] == [
            ("token", "thinking"),
            ("tool_start", "echo", {"message": "hi"}),
            ("tool_end", "echo", True, False, None, None),
            ("token", "tail"),
            ("token", "after inline"),
            ("done", "after inline", False, None, None),
        ]
        assert out["calls"][1]["messages"] == [
            USER,
            ("assistant", "thinkingtail", ((None, "echo"),), None),
            ("tool", "echo: hi", None, ""),
        ]

    @pytest.mark.asyncio
    async def test_empty_streamed_turn_is_retried_before_being_believed(self):
        """Live-only: an empty completion is retried up to three times."""
        turns = [Turn(), Turn(), Turn(content="third time")]
        out = await run_live(turns, [EchoTool()])
        assert out["trace"] == [
            ("token", "third time"),
            ("done", "third time", False, None, None),
        ]
        assert len(out["calls"]) == 3


# ---------------------------------------------------------------------------
# Cross-path equivalence
# ---------------------------------------------------------------------------

EQUIVALENT_SCENARIOS = [
    ("normal", NORMAL, [EchoTool()], {}),
    ("forced", FORCED, [EchoTool()], FORCED_KWARGS),
    ("rescued", RESCUE, [EchoTool()], {}),
    ("follow_up", FOLLOW_UP, [EchoTool()], {"iteration_nudge": "wrap up"}),
    ("failure", FAILURE, [BoomTool()], {}),
    ("repeat_suppression", REPEAT, [BoomTool()], {}),
    ("pending_decision", APPROVAL, [ApprovalTool()], {}),
]


class TestPathsAgree:
    @pytest.mark.parametrize("name,turns,tools,kwargs", EQUIVALENT_SCENARIOS,
                             ids=[s[0] for s in EQUIVALENT_SCENARIOS])
    @pytest.mark.asyncio
    async def test_all_three_paths_make_the_same_decisions(self, name, turns, tools, kwargs):
        non_stream = decisions((await run_non_stream(turns, tools, **kwargs))["trace"])
        legacy = decisions((await run_legacy(turns, tools, **kwargs))["trace"])
        live = decisions((await run_live(turns, tools, **kwargs))["trace"])
        assert legacy == live
        assert non_stream == legacy

    @pytest.mark.parametrize("name,turns,tools,kwargs", EQUIVALENT_SCENARIOS,
                             ids=[s[0] for s in EQUIVALENT_SCENARIOS])
    @pytest.mark.asyncio
    async def test_all_three_paths_build_the_same_message_history(self, name, turns, tools, kwargs):
        non_stream = [c["messages"] for c in (await run_non_stream(turns, tools, **kwargs))["calls"]]
        legacy = [c["messages"] for c in (await run_legacy(turns, tools, **kwargs))["calls"]]
        live = [c["messages"] for c in (await run_live(turns, tools, **kwargs))["calls"]]
        assert non_stream == legacy == live

    @pytest.mark.asyncio
    async def test_paths_agree_on_the_iteration_limit_except_for_the_wrap_up_signal(self):
        """The one intentional divergence: only the streaming paths emit the
        budget status event and append the wrap-up instruction to the forced
        final call. The buffered path returns its response object instead."""
        non_stream = await run_non_stream(LIMIT, [EchoTool()], max_iterations=2)
        legacy = await run_legacy(LIMIT, [EchoTool()], max_iterations=2)
        live = await run_live(LIMIT, [EchoTool()], max_iterations=2)

        assert decisions(legacy["trace"]) == decisions(live["trace"])
        assert [c["messages"] for c in legacy["calls"]] == [c["messages"] for c in live["calls"]]

        without_status = [e for e in decisions(legacy["trace"]) if e[0] != "status"]
        assert decisions(non_stream["trace"]) == without_status
        assert len(non_stream["calls"][2]["messages"]) + 1 == len(legacy["calls"][2]["messages"])
