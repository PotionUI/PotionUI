"""The tool loop itself, independent of how a turn is fetched or rendered.

`ToolWorkflow` owns everything the three entry points on `ToolExecutor` used
to each own a copy of: the working message history, the iteration budget, the
rescue counters, the repeat guard, the user/tool image precedence, the
iteration nudge, and the decision of what a finished assistant turn means. It
drives the loop and yields the typed events below; an adapter renders them to
its own output shape (a callback plus a response object, legacy stream chunks,
or live token events).

What it does NOT own is the turn: a turn source is asked for each assistant
turn and hands back a `ProviderTurn`. That is the whole of the difference
between the three paths — a buffered source calls the provider and returns the
response object, a live source streams tokens (yielding `AssistantDelta` as
they arrive) and may dispatch a `<tool_call>` block inline the moment it
closes.

Tool execution primitives stay on `ToolExecutor` (registry lookup, approval
gating, the tool_start/tool_end payloads); this module decides *when* they run
and what their results mean for the turn.
"""

import json
import logging
import re
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional, Protocol

from src.features.llm.tools import tool_call_rescue
from src.features.llm.tools.base import ToolContext, ToolExecution

logger = logging.getLogger(__name__)


# Regex to match <tool_call>...</tool_call> blocks (some models emit XML instead of structured calls)
_TOOL_CALL_XML_RE = re.compile(
    r'<tool_call>\s*(.*?)\s*</tool_call>', re.DOTALL
)


def parse_xml_tool_calls(content: str) -> List[Dict[str, Any]]:
    """Parse <tool_call> XML blocks from content into structured tool call dicts.

    Some LLMs (especially via Ollama) output tool calls as XML in the content
    field instead of using the structured tool_calls API field. This function
    detects and parses those into the same format as native tool calls.

    Returns a list of tool call dicts with {"function": {"name": ..., "arguments": ...}}.
    """
    tool_calls = []
    for match in _TOOL_CALL_XML_RE.finditer(content):
        raw = match.group(1).strip()
        try:
            call_data = json.loads(raw)
        except json.JSONDecodeError:
            # The same tokenizer artifact tool_call_rescue works around for
            # <tool_action> tags (quote characters round-tripped as
            # <|"|>) shows up inside a well-formed <tool_call> block too — the
            # call is complete, not a near-miss, so it's demangled and retried
            # here rather than being dropped into the rescue path.
            try:
                call_data = json.loads(tool_call_rescue.demangle_quote_tokens(raw))
            except json.JSONDecodeError:
                logger.warning(f"[ToolWorkflow] Failed to parse XML tool_call JSON: {raw[:200]}")
                continue

        # Normalise to {"function": {"name": ..., "arguments": ...}}
        if "function" in call_data:
            tool_calls.append(call_data)
        elif "name" in call_data:
            tool_calls.append({
                "function": {
                    "name": call_data["name"],
                    "arguments": call_data.get("arguments") or call_data.get("parameters") or {},
                }
            })
        else:
            logger.warning(f"[ToolWorkflow] XML tool_call missing 'name'/'function': {call_data}")
    return tool_calls


def strip_tool_call_xml(content: str) -> str:
    """Remove any <tool_call>...</tool_call> blocks from content."""
    return _TOOL_CALL_XML_RE.sub('', content).strip()


def resolve_tool_calls(content: str, structured: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """The turn's tool calls: the provider's structured ones, else any
    `<tool_call>` XML the model embedded in its content instead."""
    if structured:
        return structured
    if content:
        xml_calls = parse_xml_tool_calls(content)
        if xml_calls:
            logger.debug(f"[ToolWorkflow] Parsed {len(xml_calls)} tool call(s) from XML in content")
            return xml_calls
    return []


class ToolCallGuard:
    """Per-turn tracker for calls that already failed, and a running per-tool
    failure count.

    A model that retries the exact same failing call burns loop iterations
    without learning anything new; once (tool_name, canonical arguments) has
    failed once this turn, the identical call is refused without re-executing
    the tool. A call whose arguments differ, or that previously succeeded, is
    never blocked.
    """

    def __init__(self) -> None:
        self._failed_calls: Dict[str, str] = {}
        self.tool_failures: Dict[str, int] = {}

    @staticmethod
    def _call_key(tool_name: str, arguments: Dict[str, Any]) -> str:
        try:
            args_json = json.dumps(arguments, sort_keys=True, default=str)
        except TypeError:
            args_json = str(arguments)
        return f"{tool_name}:{args_json}"

    def blocked_repeat_error(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        """The teaching error to return without re-executing, or None to proceed."""
        original_error = self._failed_calls.get(self._call_key(tool_name, arguments))
        if original_error is None:
            return None
        return (
            f"You already called {tool_name} with these exact arguments and it "
            f"failed: {original_error}. Change the arguments or take a different approach."
        )

    def record(self, tool_name: str, arguments: Dict[str, Any], result) -> None:
        """Track *result* for repeat-detection and failure accounting."""
        if result.success:
            return
        self.tool_failures[tool_name] = self.tool_failures.get(tool_name, 0) + 1
        key = self._call_key(tool_name, arguments)
        if key not in self._failed_calls:
            self._failed_calls[key] = result.error or "unknown error"


# ---------------------------------------------------------------------------
# Events the workflow yields
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssistantDelta:
    """A fragment of visible assistant text, safe to show now."""
    content: str


@dataclass(frozen=True)
class ToolStarted:
    data: Dict[str, Any]


@dataclass(frozen=True)
class ToolFinished:
    data: Dict[str, Any]


@dataclass(frozen=True)
class IterationLimitReached:
    """The turn's tool budget ran out; a final tool-free call follows."""
    max_iterations: int


@dataclass(frozen=True)
class PendingDecision:
    """A tool needs the user's approval — the loop stopped without feeding its
    result back to the model."""
    response: Any


@dataclass(frozen=True)
class Completed:
    content: str
    # Which terminal the loop took: "answer" (the model's own reply),
    # "truncated"/"ambiguous" (a rescue gave up), or "budget" (the wrap-up call
    # after the iteration limit). Adapters render token usage per reason.
    reason: str
    usage: Optional[Dict[str, Any]]
    response: Any


# ---------------------------------------------------------------------------
# The turn contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TurnRequest:
    messages: List[Dict[str, Any]]
    image_data: Optional[str]
    # True for the tool-free wrap-up call made once the budget is exhausted.
    final: bool = False


@dataclass
class ProviderTurn:
    content: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, Any]] = None
    # The provider object itself, for adapters that return it.
    response: Any = None
    # The source already detected, appended and dispatched this turn's calls
    # (live inline `<tool_call>` dispatch) — the loop must not decide again.
    dispatched_inline: bool = False
    # An inline dispatch hit an approval-gated tool.
    pending: bool = False


class TurnSource(Protocol):
    def acquire(self, request: TurnRequest) -> AsyncGenerator[Any, None]:
        """Yield zero or more workflow events, then exactly one `ProviderTurn`."""
        ...


# ---------------------------------------------------------------------------
# Internal decisions
# ---------------------------------------------------------------------------

class _Retry:
    """The turn was steered back to the model; start the next iteration."""


RETRY = _Retry()


@dataclass(frozen=True)
class _Dispatch:
    calls: List[Dict[str, Any]]
    assistant_content: str


@dataclass(frozen=True)
class _Finish:
    content: str
    reason: str


class ToolWorkflow:
    """One turn's tool loop: iteration budget, dispatch, message history."""

    # Appended as a trailing system message on the forced final call once
    # max_iterations is hit — without it the model, cut off with no tools,
    # tends to either retry describing the tool call it can no longer make or
    # go silent instead of answering with what it already has.
    TOOL_BUDGET_EXHAUSTED_MESSAGE = (
        "Tool budget for this turn is exhausted — answer now with what you "
        "have; say plainly what remains undone."
    )

    # A model that wrote a tool call in the wrong format gets this many corrective
    # re-prompts before the turn surfaces an honest "couldn't format the call"
    # message instead of the raw markup (see tool_call_rescue).
    MAX_RESCUE_RETRIES = 2

    def __init__(
        self,
        executor,
        messages: List[Dict[str, Any]],
        tool_context: ToolContext,
        allowed_tools: Optional[List[str]],
        max_iterations: int,
        image_data: Optional[str] = None,
        forced_tool_call: Optional[Dict[str, Any]] = None,
        iteration_nudge: Optional[str] = None,
        wrap_up_on_limit: bool = True,
    ) -> None:
        self._executor = executor
        self.tool_context = tool_context
        self.allowed_tools = allowed_tools
        self.max_iterations = max_iterations
        self.forced_tool_call = forced_tool_call
        self.iteration_nudge = iteration_nudge
        # The buffered non-streaming entry point makes its wrap-up call without
        # the exhausted-budget instruction; both streaming paths send it. Kept
        # as a flag rather than quietly unified.
        self.wrap_up_on_limit = wrap_up_on_limit

        self.working_messages: List[Dict[str, Any]] = list(messages)
        self.tool_executions: List[ToolExecution] = []
        self.guard = ToolCallGuard()
        self._rescue_records: List[Dict[str, Any]] = []
        self._rescue_retries = 0
        self._any_tool_round_completed = False
        # The user's original attachment stays available for EVERY iteration of
        # the turn, not just the first: tool-enabled modes instruct the model to
        # call tools before answering, so iteration 1 is almost never the real
        # answer — it's a tool call. A tool-returned image (e.g. a render
        # preview) takes precedence over the user's image for exactly the next
        # call, then reverts.
        self._user_image_data = image_data
        self._tool_image_data: Optional[str] = None
        self._last_response: Any = None
        self._inline_assistant_msg: Optional[Dict[str, Any]] = None

    # -- state the adapters read back --------------------------------------

    @property
    def rescues(self) -> Optional[List[Dict[str, Any]]]:
        return self._rescue_records or None

    @property
    def tool_failures(self) -> Optional[Dict[str, int]]:
        return dict(self.guard.tool_failures) if self.guard.tool_failures else None

    # -- the loop -----------------------------------------------------------

    async def run(self, source: TurnSource) -> AsyncGenerator[Any, None]:
        async for event in self._forced_round():
            yield event

        for iteration in range(self.max_iterations):
            logger.debug(f"[ToolWorkflow] Iteration {iteration + 1}/{self.max_iterations}")
            turn: Optional[ProviderTurn] = None
            # Owns the turn source's generator: this loop is the ONLY thing
            # that can decide the round is over, so it is also the thing
            # that must close `source.acquire(...)` — closing THIS workflow
            # generator while suspended at one of the `yield item`s below
            # must reach the source (and, through it, the provider) instead
            # of abandoning it to the loop's async-generator finalizer.
            async with aclosing(source.acquire(self._next_request())) as agen:
                async for item in agen:
                    if isinstance(item, ProviderTurn):
                        turn = item
                    else:
                        yield item
            if turn.response is not None:
                self._last_response = turn.response

            if turn.pending:
                yield PendingDecision(self._last_response)
                return

            if turn.dispatched_inline:
                self._any_tool_round_completed = True
                continue

            decision = self._decide(turn.content, turn.tool_calls)
            if decision is RETRY:
                continue
            if isinstance(decision, _Finish):
                logger.debug(f"[ToolWorkflow] Turn finished after {iteration + 1} iteration(s)")
                yield Completed(decision.content, decision.reason, turn.usage, self._last_response)
                return

            pending = False
            async for item in self._dispatch(decision.calls, decision.assistant_content):
                if isinstance(item, bool):
                    pending = item
                    continue
                yield item
            if pending:
                yield PendingDecision(self._last_response)
                return

            self._any_tool_round_completed = True

        logger.warning(
            f"[ToolWorkflow] Max iterations ({self.max_iterations}) reached, "
            "making final call without tools"
        )
        yield IterationLimitReached(self.max_iterations)

        turn = None
        async with aclosing(source.acquire(self._final_request())) as agen:
            async for item in agen:
                if isinstance(item, ProviderTurn):
                    turn = item
                else:
                    yield item
        yield Completed(strip_tool_call_xml(turn.content or ""), "budget", turn.usage, turn.response)

    # -- turn requests -------------------------------------------------------

    def _next_request(self) -> TurnRequest:
        image_data = self._tool_image_data if self._tool_image_data is not None else self._user_image_data
        # One-shot consumed now, before a dispatch below may set it again.
        self._tool_image_data = None
        messages = self.working_messages
        if self.iteration_nudge and self._any_tool_round_completed:
            # Synthesized fresh per call rather than stored in
            # `working_messages`, so it is always the most recent message
            # without ever needing to be removed or deduplicated.
            messages = self.working_messages + [{"role": "system", "content": self.iteration_nudge}]
        self._inline_assistant_msg = None
        return TurnRequest(messages=messages, image_data=image_data)

    def _final_request(self) -> TurnRequest:
        messages = self.working_messages
        if self.wrap_up_on_limit:
            messages = messages + [{"role": "system", "content": self.TOOL_BUDGET_EXHAUSTED_MESSAGE}]
        return TurnRequest(messages=messages, image_data=None, final=True)

    def reset_inline(self) -> None:
        """Forget the assistant message an inline dispatch would extend — a
        source calls this at the start of each empty-response retry attempt."""
        self._inline_assistant_msg = None

    # -- what a finished turn means -----------------------------------------

    def _decide(self, content: str, structured_tool_calls: Optional[List[Dict[str, Any]]]):
        tool_calls = resolve_tool_calls(content or "", structured_tool_calls)
        if tool_calls:
            return _Dispatch(tool_calls, strip_tool_call_xml(content or ""))

        # An opened `<tool_call>` that never closed is a truncated generation,
        # not a wrong-format near-miss — nothing to repair, only the same
        # bounded retry, so it's checked first.
        truncated = tool_call_rescue.find_truncated_tool_call(content or "")
        if truncated:
            cleaned = tool_call_rescue.strip_spans(content or "", [truncated.span])
            if self._rescue_retries < self.MAX_RESCUE_RETRIES:
                self._steer(cleaned, tool_call_rescue.truncated_retry_nudge(truncated.tool_name))
                return RETRY
            return _Finish(tool_call_rescue.truncated_fallback_message(truncated.tool_name), "truncated")

        # No parsed call — the content may still be a near-miss invocation
        # (wrong tag / fence / bare JSON) that must not reach the user raw.
        repaired, ambiguous, problems, records, cleaned = self._executor._rescue_final_content(
            content or "", self.allowed_tools
        )
        if repaired:
            self._rescue_records.extend(records)
            return _Dispatch(repaired, strip_tool_call_xml(cleaned or ""))
        if ambiguous:
            if self._rescue_retries < self.MAX_RESCUE_RETRIES:
                self._steer(cleaned, tool_call_rescue.retry_nudge(ambiguous, problems))
                return RETRY
            return _Finish(tool_call_rescue.fallback_message(ambiguous), "ambiguous")
        return _Finish(strip_tool_call_xml(content or ""), "answer")

    def _steer(self, cleaned: str, nudge: str) -> None:
        """Put the cleaned attempt and a corrective instruction back in front of
        the model, spending one of the turn's bounded rescue retries."""
        self._rescue_retries += 1
        self.working_messages.append({"role": "assistant", "content": cleaned})
        self.working_messages.append({"role": "system", "content": nudge})

    # -- dispatch ------------------------------------------------------------

    async def _forced_round(self) -> AsyncGenerator[Any, None]:
        if not self.forced_tool_call:
            return
        yield ToolStarted({
            "tool_name": self.forced_tool_call["name"],
            "arguments": self.forced_tool_call.get("arguments", {}),
        })
        execution = await self._executor._execute_forced_tool(
            self.forced_tool_call, self.tool_context, self.working_messages,
            self.tool_executions, self.allowed_tools,
        )
        yield ToolFinished(self._executor._tool_end_event_data(execution))
        if execution.result.image_data:
            self._tool_image_data = execution.result.image_data
        self._any_tool_round_completed = True

    async def _dispatch(
        self, calls: List[Dict[str, Any]], assistant_content: str
    ) -> AsyncGenerator[Any, None]:
        """Append the assistant tool_call message, then run every call."""
        self.working_messages.append({
            "role": "assistant",
            "content": assistant_content,
            "tool_calls": calls,
        })
        async for item in self._run_calls(calls):
            yield item

    async def dispatch_inline(self, call: Dict[str, Any]) -> AsyncGenerator[Any, None]:
        """Run a single call a source detected mid-turn, extending (or opening)
        this iteration's assistant tool_call message."""
        if self._inline_assistant_msg is None:
            self._inline_assistant_msg = {"role": "assistant", "content": "", "tool_calls": []}
            self.working_messages.append(self._inline_assistant_msg)
        self._inline_assistant_msg["tool_calls"].append(call)
        async for item in self._run_calls([call]):
            yield item

    def finish_inline(self, content: str) -> None:
        """Patch the surrounding visible text into the inline assistant message
        once the turn's generation is complete."""
        if self._inline_assistant_msg is not None:
            self._inline_assistant_msg["content"] = strip_tool_call_xml(content)

    @property
    def dispatched_inline(self) -> bool:
        return self._inline_assistant_msg is not None

    async def _run_calls(self, calls: List[Dict[str, Any]]) -> AsyncGenerator[Any, None]:
        """Execute *calls* in order, yielding ToolStarted/ToolFinished and, last,
        a bare bool: whether the run stopped on an approval-gated tool."""
        async for event in self._executor._run_tool_calls_stream(
            calls, self.tool_context, self.working_messages,
            self.tool_executions, self.allowed_tools, self.guard,
        ):
            if event["type"] == "_control":
                if event["tool_image_data"]:
                    self._tool_image_data = event["tool_image_data"]
                yield bool(event["pending"])
                continue
            yield ToolStarted(event["data"]) if event["type"] == "tool_start" else ToolFinished(event["data"])
