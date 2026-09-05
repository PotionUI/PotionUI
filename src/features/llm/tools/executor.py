"""Tool executor - the three entry points onto the tool loop.

The loop itself lives in `workflow.ToolWorkflow`; this module owns the tool
execution primitives it drives (registry lookup, approval gating, the
tool_start/tool_end payloads) and the three adapters that render the
workflow's events: a buffered non-streaming turn returning an `LLMResponse`,
the legacy buffered-per-iteration stream, and the live per-token stream.
"""

import inspect
import json
import logging
import time
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

from src.features.llm.tools.base import (
    BaseTool,
    ToolContext,
    ToolExecution,
    ToolResult,
    serialize_approval_preview,
)
from src.features.llm.tools.registry import ToolRegistry
from src.features.llm.tools import tool_call_rescue
from src.features.llm.tools.errors import unexpected
from src.features.llm.tools.workflow import (
    AssistantDelta,
    Completed,
    IterationLimitReached,
    PendingDecision,
    ProviderTurn,
    ToolCallGuard as _ToolCallGuard,
    ToolFinished,
    ToolStarted,
    ToolWorkflow,
    TurnRequest,
    parse_xml_tool_calls as _parse_xml_tool_calls,
    strip_tool_call_xml,
)

logger = logging.getLogger(__name__)


# A tool result feeds into the working message list on every remaining
# iteration of the turn's loop — an unbounded dump (a big JSON payload, a long
# listing) burns context on every one of them, so it's capped with a marker
# that teaches the model to narrow its own next call instead.
_MAX_TOOL_RESULT_CHARS = 8000


def _bound_tool_result_content(content: str) -> str:
    """Cap a tool result string at `_MAX_TOOL_RESULT_CHARS`, appending a
    teaching marker when it was truncated."""
    if content is None or len(content) <= _MAX_TOOL_RESULT_CHARS:
        return content
    return (
        content[:_MAX_TOOL_RESULT_CHARS]
        + f"\n\n[Result truncated at {_MAX_TOOL_RESULT_CHARS} characters. "
        "Refine the call (smaller limit, narrower filters) if you need more.]"
    )


class _StreamToolCallFilter:
    """Withholds `<tool_call>...</tool_call>` spans, and a `<tool_action ...>`
    span whose `type` names a registered tool, from a live token stream.

    Suppress-at-source for `execute_with_tools_stream`'s native per-token
    loop: nothing inside a tool call belongs in the visible reply, complete
    or not, so ordinary text is forwarded as it arrives.

    A `<tool_call>` span is this module's own invocation syntax: the moment
    it opens, everything is buffered instead — never yielded — until the
    matching `</tool_call>` closes, at which point the whole block is handed
    back to the caller to parse and dispatch immediately, without waiting
    for the rest of that turn's generation to finish.

    A `<tool_action ...>` span is a near-miss a model reaches for instead
    (see `tool_call_rescue`'s module docstring) — but the same tag is also a
    real frontend convention (`update_segment`/`update_director_segment`)
    that must stream normally. So on `<tool_action` its own opening tag is
    buffered (not yet forwarded) until its closing `>` arrives; only once
    `type` is known to name a *registered_tool_names* entry does suppression
    continue through `</tool_action>`. A non-registered `type` (or one that
    never resolves before the tag's own `>` — plain prose that happens to
    contain the substring) flushes the buffered open tag as text and resumes
    normal streaming. Unlike `<tool_call>`, a suppressed `<tool_action>` span
    is never dispatched inline here — it stays in the iteration's
    independently-accumulated `full_iter_content` for
    `ToolExecutor._rescue_final_content` to detect, repair, and dispatch once
    the iteration ends, exactly as it already does for a buffered response.
    """

    _OPEN = "<tool_call>"
    _CLOSE = "</tool_call>"
    _ACTION_PREFIX = "<tool_action"

    def __init__(self, registered_tool_names: Optional[set] = None) -> None:
        self._buf = ""
        self._registered = set(registered_tool_names or ())
        # "idle" | "peek_action" (saw `<tool_action`, its own `>` hasn't
        # arrived yet, so it isn't known whether `type` names a registered
        # tool) | "suppress_call" | "suppress_action"
        self._state = "idle"

    @property
    def suppressing(self) -> bool:
        return self._state != "idle"

    @staticmethod
    def _partial_open_suffix_len(buf: str) -> int:
        """Length of the longest suffix of *buf* that is also a proper prefix
        of one of the two opening markers — 0 when the buffer's tail couldn't
        possibly be the start of either. A real, complete match is found
        separately via `find`; this only catches a tag split across two token
        chunks, so ordinary text is never held back waiting for a match that
        will never come."""
        prefixes = (_StreamToolCallFilter._OPEN, _StreamToolCallFilter._ACTION_PREFIX)
        max_len = min(len(buf), max(len(p) for p in prefixes) - 1)
        for length in range(max_len, 0, -1):
            suffix = buf[-length:]
            if any(prefix.startswith(suffix) for prefix in prefixes):
                return length
        return 0

    def feed(self, text: str) -> List[Tuple[str, str]]:
        """Returns an ordered list of ``("text", chunk)`` / ``("block", raw)``
        pairs for *text*, in the exact order they occur — a "text" chunk is
        safe to forward as a token now, a "block" is a complete suppressed
        span (open tag through close tag; the caller drops a `<tool_action>`
        block on the floor since `_parse_xml_tool_calls` finds nothing in it,
        and dispatches a `<tool_call>` block). Preserving order (rather than
        collecting text and blocks into two separate lists) matters: it lets
        a caller stop partway through — e.g. once a dispatched block turns
        out to need approval — without forwarding or dispatching anything
        that came after it in this same call."""
        self._buf += text
        segments: List[Tuple[str, str]] = []
        while True:
            if self._state == "idle":
                idx_call = self._buf.find(self._OPEN)
                idx_action = self._buf.find(self._ACTION_PREFIX)
                candidates = [i for i in (idx_call, idx_action) if i != -1]
                if not candidates:
                    keep = self._partial_open_suffix_len(self._buf)
                    if len(self._buf) > keep:
                        cut = len(self._buf) - keep
                        segments.append(("text", self._buf[:cut]))
                        self._buf = self._buf[cut:]
                    break
                idx = min(candidates)
                if idx:
                    segments.append(("text", self._buf[:idx]))
                self._buf = self._buf[idx:]
                self._state = "suppress_call" if self._buf.startswith(self._OPEN) else "peek_action"
                continue

            if self._state == "peek_action":
                match = tool_call_rescue.match_tool_action_open(self._buf)
                if match is None:
                    break
                if tool_call_rescue.tool_action_type(match) in self._registered:
                    self._state = "suppress_action"
                    continue
                segments.append(("text", match.group(0)))
                self._buf = self._buf[match.end():]
                self._state = "idle"
                continue

            if self._state == "suppress_call":
                idx = self._buf.find(self._CLOSE)
                if idx == -1:
                    break
                end = idx + len(self._CLOSE)
                segments.append(("block", self._buf[:end]))
                self._buf = self._buf[end:]
                self._state = "idle"
                continue

            # suppress_action
            close = tool_call_rescue.find_tool_action_close(self._buf)
            if close is None:
                break
            segments.append(("block", self._buf[:close.end()]))
            self._buf = self._buf[close.end():]
            self._state = "idle"
        return segments

    def flush(self) -> str:
        """Remaining safe text once the stream ends. Returns "" while a span
        is open or being peeked — that text belongs to an unresolved tag and
        must stay hidden; the truncation/near-miss handling takes it from
        there."""
        if self._state != "idle":
            return ""
        text, self._buf = self._buf, ""
        return text


def _usage_fields(usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The token-count, thinking-mode and completion keys a `done` event
    carries, from either a provider response or a stream's usage event.
    `thinking_mode` is `None` for any provider that doesn't report it (see
    `LLMResponse.thinking_mode`); `completion` is the normalized outcome from
    `clients.completion` (`None` if the round's turn never carried one). A
    rescue's fallback message never reaches here since callers only merge
    this in on a genuine LLM completion — see the `event.reason in ("answer",
    "budget")` guards at both call sites below."""
    usage = usage or {}
    return {
        "tokens_used": usage.get("tokens_used"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "thinking_mode": usage.get("thinking_mode"),
        "completion": usage.get("completion"),
    }



class _BufferedTurnSource:
    """Fetches each turn with one non-streaming `generate_with_tools` call.

    Shared by `execute_with_tools` and `_execute_with_tools_stream_legacy`:
    both need the whole response before they can tell a tool call apart from
    an answer, and differ only in how they render it.
    """

    def __init__(self, executor, llm_id, system_message, tool_schemas, mode, llm_options):
        self._executor = executor
        self._llm_id = llm_id
        self._system_message = system_message
        self._tool_schemas = tool_schemas
        self._mode = mode
        self._llm_options = llm_options

    async def acquire(self, request: TurnRequest) -> AsyncGenerator[Any, None]:
        kwargs: Dict[str, Any] = {
            "messages": request.messages,
            "llm_id": self._llm_id,
            "tools": [] if request.final else self._tool_schemas,
            "custom_system_message": self._system_message,
            "mode": self._mode,
            "options_override": self._llm_options,
        }
        if not request.final:
            kwargs["image_data"] = request.image_data
        response = await self._executor.llm_service.generate_with_tools(**kwargs)
        yield ProviderTurn(
            content=response.content or "",
            tool_calls=response.tool_calls,
            usage={
                "tokens_used": response.tokens_used,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "thinking_mode": getattr(response, "thinking_mode", None),
                "completion": getattr(response, "completion", None),
            },
            response=response,
        )


class _LiveTurnSource:
    """Streams each turn token by token, dispatching a `<tool_call>` block the
    moment it closes.

    Nothing inside a tool call reaches the caller: `_StreamToolCallFilter`
    withholds a `<tool_call>` span and a registered-tool `<tool_action>` span
    at the source. A closed `<tool_call>` is parsed and dispatched immediately
    through the workflow, without waiting for the rest of the turn; a
    `<tool_action>` stays hidden and is picked up by the workflow's near-miss
    handling from the turn's accumulated content. A span that never closes was
    never shown either way, so the workflow's bounded retry applies.
    """

    def __init__(self, executor, workflow, llm_id, system_message, tool_schemas,
                 registered, mode, llm_options):
        self._executor = executor
        self._workflow = workflow
        self._llm_id = llm_id
        self._system_message = system_message
        self._tool_schemas = tool_schemas
        self._registered = registered
        self._mode = mode
        self._llm_options = llm_options

    def _stream(self, messages, tools, image_data):
        return self._executor.llm_service.stream_with_tools(
            messages=messages,
            llm_id=self._llm_id,
            tools=tools,
            image_data=image_data,
            custom_system_message=self._system_message,
            mode=self._mode,
            options_override=self._llm_options,
        )

    async def acquire(self, request: TurnRequest) -> AsyncGenerator[Any, None]:
        if request.final:
            async for item in self._acquire_final(request):
                yield item
            return

        workflow = self._workflow
        content_parts: List[str] = []
        tool_calls: Optional[List[Dict[str, Any]]] = None
        usage: Dict[str, Any] = {}
        draining = False

        for attempt in range(self._executor._EMPTY_RESPONSE_MAX_RETRIES):
            workflow.reset_inline()
            content_parts = []
            tool_calls = None
            usage = {}
            pending = False
            # A pending approval fired inline; keep consuming the generator,
            # forward nothing more.
            draining = False
            stream_filter = _StreamToolCallFilter(self._registered)

            async for event in self._stream(request.messages, self._tool_schemas, request.image_data):
                event_type = event.get("type")
                if event_type == "token":
                    content_parts.append(event["content"])
                    if draining:
                        continue
                    for kind, value in stream_filter.feed(event["content"]):
                        if draining:
                            # A call earlier in this SAME feed() result already
                            # turned out to need approval — nothing after it,
                            # text or another call, is forwarded or dispatched.
                            break
                        if kind == "text":
                            if value:
                                yield AssistantDelta(value)
                            continue
                        for call in _parse_xml_tool_calls(value):
                            async for item in workflow.dispatch_inline(call):
                                if isinstance(item, bool):
                                    pending = item
                                    continue
                                yield item
                            if pending:
                                draining = True
                                break
                elif event_type == "tool_calls":
                    tool_calls = event["tool_calls"]
                elif event_type == "usage":
                    usage = event

            if not draining:
                trailing = stream_filter.flush()
                if trailing:
                    yield AssistantDelta(trailing)

            if content_parts or tool_calls or workflow.dispatched_inline:
                break
            if attempt < self._executor._EMPTY_RESPONSE_MAX_RETRIES - 1:
                logger.warning(
                    f"[ToolExecutor] Empty streamed response "
                    f"(attempt {attempt + 1}/{self._executor._EMPTY_RESPONSE_MAX_RETRIES}), retrying"
                )

        full_iter_content = "".join(content_parts)

        if draining:
            yield ProviderTurn(pending=True)
            return
        if workflow.dispatched_inline:
            workflow.finish_inline(full_iter_content)
            yield ProviderTurn(dispatched_inline=True)
            return
        yield ProviderTurn(content=full_iter_content, tool_calls=tool_calls, usage=usage)

    async def _acquire_final(self, request: TurnRequest) -> AsyncGenerator[Any, None]:
        content_parts: List[str] = []
        usage: Dict[str, Any] = {}
        async for event in self._stream(request.messages, None, None):
            event_type = event.get("type")
            if event_type == "token":
                content_parts.append(event["content"])
                yield AssistantDelta(event["content"])
            elif event_type == "usage":
                usage = event
        yield ProviderTurn(content="".join(content_parts), usage=usage)



class ToolExecutor:
    """Executes the tool calling loop between the LLM and registered tools.

    Manages the iterative process of:
    1. Sending messages + tool schemas to LLM
    2. Parsing tool_calls from response
    3. Executing tools and appending results
    4. Re-calling LLM until it produces a final text response

    Tools with `requires_approval=True` pause the loop after `execute()` returns
    a preview.  The caller receives the partial state and must call
    `execute_tool_confirmed()` after the user approves.
    """

    # Some models non-deterministically stream an empty completion (no tokens,
    # no tool_calls); retry before accepting emptiness as the turn's answer.
    _EMPTY_RESPONSE_MAX_RETRIES = 3

    def __init__(self, tool_registry: ToolRegistry, llm_service: Any):
        self.tool_registry = tool_registry
        self.llm_service = llm_service

    def _registered_allowed(self, allowed_tools: Optional[List[str]]) -> set:
        """Registered tool names, narrowed to the session's allowed set."""
        names = {tool.name for tool in self.tool_registry.get_all()}
        if allowed_tools is not None:
            names &= set(allowed_tools)
        return names

    def _rescue_final_content(
        self, content: str, allowed_tools: Optional[List[str]]
    ) -> Tuple[List[Dict[str, Any]], List[str], List[str], List[Dict[str, Any]], str]:
        """Scan a would-be-final assistant message for near-miss tool invocations.

        Returns ``(repaired_calls, ambiguous_names, problems, records,
        cleaned_content)``: near-misses whose arguments parse and satisfy the
        tool schema become real tool_call dicts; the rest are named for a
        corrective retry, carrying the parse failures that made them ambiguous.
        Every detected span is stripped from ``cleaned_content`` so no markup
        ever surfaces.
        """
        registered = self._registered_allowed(allowed_tools)
        near = tool_call_rescue.find_near_miss_invocations(content or "", registered)
        if not near:
            return [], [], [], [], content

        repaired: List[Dict[str, Any]] = []
        ambiguous: List[str] = []
        problems: List[str] = []
        records: List[Dict[str, Any]] = []
        spans: List[Tuple[int, int]] = []
        for index, nm in enumerate(near):
            spans.append(nm.span)
            tool = self.tool_registry.get(nm.tool_name)
            schema = tool.parameters if tool else {}
            if nm.arguments is not None and tool_call_rescue.validate_arguments(nm.arguments, schema):
                repaired.append({
                    "id": f"rescue_{index}",
                    "type": "function",
                    # Canonical in-process shape: object arguments (see
                    # clients.tool_call_shape). Each client re-serializes to its wire form.
                    "function": {"name": nm.tool_name, "arguments": nm.arguments},
                })
                records.append({
                    "tool_name": nm.tool_name,
                    "repaired": True,
                    "original_format": nm.original_format,
                })
                logger.debug(
                    f"[ToolExecutor] Repaired near-miss {nm.original_format} call to "
                    f"'{nm.tool_name}' into a real tool call"
                )
            else:
                ambiguous.append(nm.tool_name)
                if nm.problem:
                    problems.append(nm.problem)
        return repaired, ambiguous, problems, records, tool_call_rescue.strip_spans(content, spans)

    @staticmethod
    def _forced_assistant_message(name: str, arguments: Dict[str, Any], call_id: str) -> Dict[str, Any]:
        """Synthesize the assistant tool_call message that seeds a forced call.

        Shaped like a provider's native tool_calls entry (id/type/function) with
        the canonical object arguments; each client re-serializes to its wire
        shape at the request boundary (see clients.tool_call_shape).
        """
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments or {}},
            }],
        }

    async def _execute_forced_tool(
        self,
        forced_tool_call: Dict[str, Any],
        tool_context: ToolContext,
        working_messages: List[Dict[str, Any]],
        tool_executions: List[ToolExecution],
        allowed_tools: Optional[List[str]],
    ) -> ToolExecution:
        """Run a caller-forced tool call as the turn's first action.

        Used by callers that must invoke a specific tool deterministically
        instead of hoping the model chooses it. The tool runs, then the normal
        loop lets the model present the result — the same code path as a
        model-chosen call. Approval is bypassed: the caller invoked the tool
        explicitly, so there is nothing to confirm.

        Appends the assistant tool_call message and the tool result message to
        ``working_messages`` and the record to ``tool_executions`` (both mutated
        in place), and returns the execution record so the caller can emit the
        matching tool_start/tool_end wire events.
        """
        name = forced_tool_call["name"]
        arguments = forced_tool_call.get("arguments", {}) or {}
        call_id = "forced_call_0"

        working_messages.append(self._forced_assistant_message(name, arguments, call_id))

        start_time = time.monotonic()
        result, _is_pending = await self._execute_tool(name, tool_context, arguments, allowed_tools=allowed_tools)
        duration_ms = int((time.monotonic() - start_time) * 1000)

        execution = ToolExecution(
            tool_name=name,
            arguments=arguments,
            result=result,
            duration_ms=duration_ms,
            pending_approval=False,
        )
        tool_executions.append(execution)

        working_messages.append({
            "role": "tool",
            "content": _bound_tool_result_content(result.data if result.success else f"Error: {result.error}"),
            "tool_call_id": call_id,
            "name": name,
        })
        return execution

    @staticmethod
    def _tool_end_event_data(execution: ToolExecution) -> Dict[str, Any]:
        """Build the tool_end event payload for a completed execution."""
        data: Dict[str, Any] = {
            "tool_name": execution.tool_name,
            "success": execution.result.success,
            "duration_ms": execution.duration_ms,
            "pending_approval": execution.pending_approval,
        }
        if execution.result.sources:
            data["sources"] = [
                {
                    "source_type": s.source_type,
                    "title": s.title,
                    "subtitle": s.subtitle,
                    "description": s.description,
                    "url": s.url,
                    "icon": s.icon,
                }
                for s in execution.result.sources
            ]
        return data

    async def execute_with_tools(
        self,
        messages: List[Dict],
        llm_id: str,
        system_message: str,
        tool_context: ToolContext,
        mode: Optional[str] = None,
        image_data: Optional[str] = None,
        max_iterations: int = 20,
        on_tool_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        allowed_tools: Optional[List[str]] = None,
        llm_options: Optional[Dict[str, Any]] = None,
        forced_tool_call: Optional[Dict[str, Any]] = None,
        iteration_nudge: Optional[str] = None,
    ) -> Tuple[Any, List[ToolExecution]]:
        """Run the tool loop buffered. Returns final LLM response + execution records.

        Args:
            messages: Conversation history
            llm_id: LLM configuration ID
            system_message: System message for the LLM
            tool_context: Context with service references for tool execution
            mode: Optional chat mode id (pass-through for logging/hooks)
            image_data: Optional base64 image data
            max_iterations: Maximum tool call iterations (safety limit)
            on_tool_event: Optional callback for tool events
            allowed_tools: Pre-resolved tool names for this session (None = all)
            llm_options: Optional per-mode sampling/thinking overrides
            forced_tool_call: Optional ``{"name", "arguments"}`` run before the
                loop's first LLM turn, so a slash command reaches the model as
                an ordinary already-executed tool call it must present.
            iteration_nudge: Optional reminder text appended as a trailing
                system message on every LLM call once at least one tool round
                has completed this turn.

        Unlike the two streaming entry points, this one makes its post-budget
        wrap-up call without the exhausted-budget instruction and emits no
        budget signal — `IterationLimitReached` has nowhere to go on a path
        whose only output is the returned response.

        Returns:
            Tuple of (final LLMResponse, list of ToolExecution records)
        """
        workflow = ToolWorkflow(
            self, messages, tool_context, allowed_tools, max_iterations,
            image_data=image_data, forced_tool_call=forced_tool_call,
            iteration_nudge=iteration_nudge, wrap_up_on_limit=False,
        )
        source = _BufferedTurnSource(
            self, llm_id, system_message, self.tool_registry.get_schemas(allowed_tools),
            mode, llm_options,
        )

        terminal: Any = None
        async for event in workflow.run(source):
            if isinstance(event, ToolStarted):
                if on_tool_event:
                    on_tool_event("tool_start", event.data)
            elif isinstance(event, ToolFinished):
                if on_tool_event:
                    on_tool_event("tool_end", event.data)
            elif isinstance(event, (PendingDecision, Completed)):
                terminal = event

        response = terminal.response
        response.content = "" if isinstance(terminal, PendingDecision) else terminal.content
        # Same "answer"/"budget" guard as the streaming entry points: a
        # rescue's fallback message (truncated/ambiguous) is never the
        # model's own completion, so the round's completion outcome must not
        # be attributed to it even though `response` is still that round's
        # real API object.
        if isinstance(terminal, Completed) and terminal.reason not in ("answer", "budget"):
            response.completion = None
        response.rescues = workflow.rescues
        response.tool_failures = workflow.tool_failures
        return response, workflow.tool_executions


    async def _force_prompt_tools_for(self, llm_id: str) -> bool:
        """Whether this config renders tools into the system prompt as XML instead of native tool calling.

        force_prompt_tools models emit tool calls as `<tool_call>` XML embedded
        in ordinary streamed content; safely telling that apart from a real
        answer requires the whole response, so those configs keep the older
        buffered-per-iteration streaming behavior (see
        `_execute_with_tools_stream_legacy`) rather than the live per-iteration
        streaming `execute_with_tools_stream` otherwise uses.

        A `type == "native"` config is ALWAYS this shape — NativeLLMClient has
        no structured tool-calling path at all (see its module docstring) and
        never populates `response.tool_calls`, so it always emits `<tool_call>`
        XML in content. That is a fact about the client, not a per-deployment
        preference, so it forces the buffered path outright rather than relying
        on an admin to also set `provider_options.force_prompt_tools` — leaving
        it opt-in let a native config fall through to the live-streaming path,
        where an XML tool call (complete or not) is streamed to the user as
        raw tokens before this executor ever gets to look at it.

        `get_configuration` is synchronous on the real repository; the
        `inspect.isawaitable` check just lets test doubles built as a blanket
        AsyncMock keep working.
        """
        try:
            config = self.llm_service.repository.get_configuration(llm_id)
            if inspect.isawaitable(config):
                config = await config
        except Exception:
            return False
        if not config:
            return False
        if getattr(config, "type", None) == "native":
            return True
        return bool((getattr(config, "provider_options", None) or {}).get("force_prompt_tools", False))

    async def _run_tool_calls_stream(
        self,
        tool_calls: List[Dict[str, Any]],
        tool_context: ToolContext,
        working_messages: List[Dict[str, Any]],
        tool_executions: List[ToolExecution],
        allowed_tools: Optional[List[str]],
        guard: _ToolCallGuard,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute one iteration's tool calls, yielding tool_start/tool_end events.

        Appends tool result messages to `working_messages` and executions to
        `tool_executions` (both mutated in place). The final yielded event is
        always ``{"type": "_control", "pending": bool, "tool_image_data": ...}``
        — callers must consume it and must not forward it as a wire event.
        """
        tool_image_data = None
        pending = False

        for tool_call in tool_calls:
            tool_name = tool_call.get("function", {}).get("name", "")
            tool_call_id = tool_call.get("id", "")
            raw_args = tool_call.get("function", {}).get("arguments", "{}")

            try:
                if isinstance(raw_args, str):
                    arguments = json.loads(raw_args)
                else:
                    arguments = raw_args
            except json.JSONDecodeError:
                arguments = {}

            yield {"type": "tool_start", "data": {"tool_name": tool_name, "arguments": arguments}}

            start_time = time.monotonic()
            result, is_pending = await self._execute_tool_guarded(
                tool_name, tool_context, arguments, allowed_tools, guard
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)

            execution = ToolExecution(
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                duration_ms=duration_ms,
                pending_approval=is_pending,
            )
            tool_executions.append(execution)

            tool_end_data: Dict[str, Any] = {
                "tool_name": tool_name,
                "success": result.success,
                "duration_ms": duration_ms,
                "pending_approval": is_pending,
            }
            if is_pending:
                tool_end_data["arguments"] = arguments
                preview = serialize_approval_preview(result.preview)
                if preview:
                    tool_end_data["preview"] = preview
            if result.sources:
                tool_end_data["sources"] = [
                    {
                        "source_type": s.source_type,
                        "title": s.title,
                        "subtitle": s.subtitle,
                        "description": s.description,
                        "url": s.url,
                        "icon": s.icon,
                    }
                    for s in result.sources
                ]
            yield {"type": "tool_end", "data": tool_end_data}

            if is_pending:
                logger.info(f"[ToolExecutor] Tool '{tool_name}' requires approval — pausing stream loop")
                pending = True
                break

            working_messages.append({
                "role": "tool",
                "content": _bound_tool_result_content(result.data if result.success else f"Error: {result.error}"),
                "tool_call_id": tool_call_id,
                "name": tool_name,
            })

            if result.image_data:
                tool_image_data = result.image_data

        yield {"type": "_control", "pending": pending, "tool_image_data": tool_image_data}

    async def execute_with_tools_stream(
        self,
        messages: List[Dict],
        llm_id: str,
        system_message: str,
        tool_context: ToolContext,
        mode: Optional[str] = None,
        image_data: Optional[str] = None,
        max_iterations: int = 20,
        allowed_tools: Optional[List[str]] = None,
        llm_options: Optional[Dict[str, Any]] = None,
        forced_tool_call: Optional[Dict[str, Any]] = None,
        iteration_nudge: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Run the tool loop, streaming tokens live as the LLM produces them.

        Yields events:
        - {"type": "tool_start", "data": {"tool_name": ..., "arguments": ...}}
        - {"type": "tool_end", "data": {"tool_name": ..., "success": ..., "duration_ms": ..., "pending_approval": ...}}
        - {"type": "token", "data": {"content": ...}}
        - {"type": "status", "data": {"step": "tool_budget_exhausted", ...}}
        - {"type": "done", "data": {"tool_executions": [...], "full_content": ..., "pending_tool_approval": ...}}

        When a tool with requires_approval=True is executed, the generator emits a
        "done" event with pending_tool_approval=True and then returns immediately
        without feeding the result to the LLM.

        force_prompt_tools configurations (see `_force_prompt_tools_for`) are
        delegated to `_execute_with_tools_stream_legacy`, which buffers each
        iteration's full response before deciding whether it was a tool call —
        the same behavior this method used before it streamed natively.
        """
        if await self._force_prompt_tools_for(llm_id):
            async for event in self._execute_with_tools_stream_legacy(
                messages=messages,
                llm_id=llm_id,
                system_message=system_message,
                tool_context=tool_context,
                mode=mode,
                image_data=image_data,
                max_iterations=max_iterations,
                allowed_tools=allowed_tools,
                llm_options=llm_options,
                forced_tool_call=forced_tool_call,
                iteration_nudge=iteration_nudge,
            ):
                yield event
            return

        workflow = ToolWorkflow(
            self, messages, tool_context, allowed_tools, max_iterations,
            image_data=image_data, forced_tool_call=forced_tool_call,
            iteration_nudge=iteration_nudge,
        )
        source = _LiveTurnSource(
            self, workflow, llm_id, system_message,
            self.tool_registry.get_schemas(allowed_tools),
            self._registered_allowed(allowed_tools), mode, llm_options,
        )

        async for event in workflow.run(source):
            if isinstance(event, AssistantDelta):
                yield {"type": "token", "data": {"content": event.content}}
            elif isinstance(event, ToolStarted):
                yield {"type": "tool_start", "data": event.data}
            elif isinstance(event, ToolFinished):
                yield {"type": "tool_end", "data": event.data}
            elif isinstance(event, IterationLimitReached):
                yield {"type": "status", "data": {
                    "step": "tool_budget_exhausted", "state": "completed",
                    "detail": {"max_iterations": event.max_iterations},
                }}
            elif isinstance(event, PendingDecision):
                yield {"type": "done", "data": self._done_data(workflow, "", True)}
            elif isinstance(event, Completed):
                data = self._done_data(workflow, event.content, False)
                # The text already reached the caller as tokens, so only a
                # terminal the model actually generated carries usage; a
                # rescue's fallback message was never a completion.
                if event.reason in ("answer", "budget"):
                    data.update(_usage_fields(event.usage))
                yield {"type": "done", "data": data}


    async def _execute_with_tools_stream_legacy(
        self,
        messages: List[Dict],
        llm_id: str,
        system_message: str,
        tool_context: ToolContext,
        mode: Optional[str] = None,
        image_data: Optional[str] = None,
        max_iterations: int = 20,
        allowed_tools: Optional[List[str]] = None,
        llm_options: Optional[Dict[str, Any]] = None,
        forced_tool_call: Optional[Dict[str, Any]] = None,
        iteration_nudge: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Run the tool loop with a non-streaming decision call per iteration.

        Used for force_prompt_tools configurations, where tool calls are XML
        embedded in ordinary content and can't be safely told apart from a real
        answer until the whole response is in. Because nothing is shown until
        the turn is decided, the visible text arrives as a single token event
        just before `done`.

        Yields the same event shapes as `execute_with_tools_stream`.
        """
        workflow = ToolWorkflow(
            self, messages, tool_context, allowed_tools, max_iterations,
            image_data=image_data, forced_tool_call=forced_tool_call,
            iteration_nudge=iteration_nudge,
        )
        source = _BufferedTurnSource(
            self, llm_id, system_message, self.tool_registry.get_schemas(allowed_tools),
            mode, llm_options,
        )

        async for event in workflow.run(source):
            if isinstance(event, ToolStarted):
                yield {"type": "tool_start", "data": event.data}
            elif isinstance(event, ToolFinished):
                yield {"type": "tool_end", "data": event.data}
            elif isinstance(event, IterationLimitReached):
                yield {"type": "status", "data": {
                    "step": "tool_budget_exhausted", "state": "completed",
                    "detail": {"max_iterations": event.max_iterations},
                }}
            elif isinstance(event, PendingDecision):
                yield {"type": "done", "data": self._done_data(workflow, "", True)}
            elif isinstance(event, Completed):
                if event.content:
                    yield {"type": "token", "data": {"content": event.content}}
                data = self._done_data(workflow, event.content, False)
                # Same "answer"/"budget" guard as execute_with_tools_stream:
                # a rescue's fallback message (truncated/ambiguous) is never a
                # genuine LLM completion, so its round's usage/completion must
                # not be attributed to it.
                if event.reason in ("answer", "budget"):
                    data.update(_usage_fields(event.usage))
                yield {"type": "done", "data": data}

    @staticmethod
    def _done_data(workflow: ToolWorkflow, full_content: str, pending: bool) -> Dict[str, Any]:
        return {
            "tool_executions": workflow.tool_executions,
            "full_content": full_content,
            "pending_tool_approval": pending,
            "rescues": workflow.rescues,
            "tool_failures": workflow.tool_failures,
        }


    _PRESENTATION_RETRY_NUDGE = (
        "\n\n---\nReminder: no tools are available this turn. Reply in plain "
        "text only — do not emit a <tool_call> block or any tool-call syntax."
    )

    async def present_tool_outcome(
        self,
        messages: List[Dict[str, Any]],
        llm_id: str,
        system_message: str,
        mode: Optional[str] = None,
        llm_options: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Run one buffered, tool-free completion presenting an applied outcome.

        Used to continue the conversation after a paused approval is resolved:
        the caller seeds `messages` so it ends with the assistant tool_call
        message and a tool-result message describing what happened, and this
        makes the model narrate it to the user. Tools are disabled so the turn
        can only produce text (it never re-triggers another approval-gated
        action). `system_message` is expected to already be presentation-only
        (no tool-calling instructions) — see `ToolCallDispatcher` in
        `src.features.chat.tool_dispatcher`, which builds it. A
        non-deterministically empty completion is retried with the same
        discipline the streaming loop uses, nudging harder on each retry.
        """
        response = None
        for attempt in range(self._EMPTY_RESPONSE_MAX_RETRIES):
            attempt_system_message = system_message
            if attempt > 0:
                attempt_system_message = f"{system_message}{self._PRESENTATION_RETRY_NUDGE}"
            response = await self.llm_service.generate_with_tools(
                messages=messages,
                llm_id=llm_id,
                tools=[],
                custom_system_message=attempt_system_message,
                mode=mode,
                options_override=llm_options,
            )
            content = strip_tool_call_xml(response.content or "")
            if content:
                response.content = content
                return response
            if attempt < self._EMPTY_RESPONSE_MAX_RETRIES - 1:
                logger.warning(
                    f"[ToolExecutor] Empty presentation completion "
                    f"(attempt {attempt + 1}/{self._EMPTY_RESPONSE_MAX_RETRIES}), retrying"
                )
        if response is not None:
            response.content = strip_tool_call_xml(response.content or "")
        return response

    async def _execute_tool(
        self, tool_name: str, context: ToolContext, arguments: Dict[str, Any],
        allowed_tools: Optional[List[str]] = None,
    ) -> Tuple[ToolResult, bool]:
        """Execute a single tool by name.

        Returns:
            Tuple of (ToolResult, pending_approval flag).
            pending_approval is True only when the tool has requires_approval=True
            AND the execution succeeded (i.e. produced a valid preview).
        """
        # Defense in depth: reject tools not in the allowed set. Name the tools
        # that ARE available so a small model can recover in one step instead of
        # retrying the same disabled tool.
        if allowed_tools is not None and tool_name not in allowed_tools:
            logger.warning(f"[ToolExecutor] Tool '{tool_name}' not in allowed_tools, rejecting")
            available = ", ".join(sorted(allowed_tools)) if allowed_tools else "none"
            return ToolResult(
                success=False,
                data="",
                error=(
                    f"Tool '{tool_name}' is not enabled for this session. "
                    f"Available tools: {available}. Use one of these instead."
                ),
            ), False

        tool = self.tool_registry.get(tool_name)
        if not tool:
            logger.error(f"[ToolExecutor] Tool not found: {tool_name}")
            return ToolResult(
                success=False,
                data="",
                error=f"Tool '{tool_name}' not found",
            ), False

        try:
            logger.debug(f"[ToolExecutor] Executing tool: {tool_name} with args: {arguments}")
            result = await tool.execute(context, **arguments)
            logger.debug(f"[ToolExecutor] Tool {tool_name} completed: success={result.success}")
            pending = tool.requires_approval and result.success
            return result, pending
        except Exception as e:
            logger.error(f"[ToolExecutor] Tool {tool_name} failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                data="",
                error=unexpected(tool_name, "run", e),
            ), False

    async def _execute_tool_guarded(
        self,
        tool_name: str,
        context: ToolContext,
        arguments: Dict[str, Any],
        allowed_tools: Optional[List[str]],
        guard: _ToolCallGuard,
    ) -> Tuple[ToolResult, bool]:
        """`_execute_tool`, short-circuiting an exact repeat of a call that
        already failed this turn (see `_ToolCallGuard`) instead of re-running it."""
        blocked = guard.blocked_repeat_error(tool_name, arguments)
        if blocked is not None:
            result = ToolResult(success=False, data="", error=blocked)
            guard.record(tool_name, arguments, result)
            return result, False
        result, is_pending = await self._execute_tool(tool_name, context, arguments, allowed_tools=allowed_tools)
        guard.record(tool_name, arguments, result)
        return result, is_pending

    async def execute_tool_confirmed(
        self, tool_name: str, context: ToolContext, arguments: Dict[str, Any]
    ) -> ToolResult:
        """Execute a tool's confirmed action after user approval.

        This delegates to `tool.execute_confirmed()` which applies the action
        that was previewed during the initial `execute()` call.

        Args:
            tool_name: Name of the tool to confirm
            context: Tool execution context
            arguments: Original arguments passed to `execute()`

        Returns:
            ToolResult from the confirmed execution
        """
        tool = self.tool_registry.get(tool_name)
        if not tool:
            return ToolResult(
                success=False,
                data="",
                error=f"Tool '{tool_name}' not found",
            )
        if not tool.requires_approval:
            return ToolResult(
                success=False,
                data="",
                error=f"Tool '{tool_name}' does not require approval",
            )
        try:
            logger.debug(f"[ToolExecutor] Executing confirmed tool: {tool_name} with args: {arguments}")
            result = await tool.execute_confirmed(context, **arguments)
            logger.debug(f"[ToolExecutor] Confirmed tool {tool_name} completed: success={result.success}")
            return result
        except Exception as e:
            logger.error(f"[ToolExecutor] Tool {tool_name} execute_confirmed failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                data="",
                error=unexpected(tool_name, "confirm", e),
            )
