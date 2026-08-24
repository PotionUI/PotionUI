"""Tool executor - manages the tool calling loop with LLMs."""

import inspect
import json
import logging
import re
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

logger = logging.getLogger(__name__)

# Regex to match <tool_call>...</tool_call> blocks (some models emit XML instead of structured calls)
_TOOL_CALL_XML_RE = re.compile(
    r'<tool_call>\s*(.*?)\s*</tool_call>', re.DOTALL
)


def _parse_xml_tool_calls(content: str) -> List[Dict[str, Any]]:
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
                logger.warning(f"[ToolExecutor] Failed to parse XML tool_call JSON: {raw[:200]}")
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
            logger.warning(f"[ToolExecutor] XML tool_call missing 'name'/'function': {call_data}")
    return tool_calls


def strip_tool_call_xml(content: str) -> str:
    """Remove any <tool_call>...</tool_call> blocks from content."""
    return _TOOL_CALL_XML_RE.sub('', content).strip()


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
    """Withholds `<tool_call>...</tool_call>` spans from a live token stream.

    Suppress-at-source for `execute_with_tools_stream`'s native per-token
    loop: nothing inside a tool call belongs in the visible reply, complete
    or not, so ordinary text is forwarded as it arrives and the moment
    `<tool_call>` opens, everything is buffered instead — never yielded —
    until the matching `</tool_call>` closes, at which point the whole block
    is handed back to the caller to parse and dispatch immediately, without
    waiting for the rest of that turn's generation to finish.
    """

    _OPEN = "<tool_call>"
    _CLOSE = "</tool_call>"

    def __init__(self) -> None:
        self._buf = ""
        self.suppressing = False

    @staticmethod
    def _partial_open_suffix_len(buf: str) -> int:
        """Length of the longest suffix of *buf* that is also a proper prefix
        of the opening tag — 0 when the buffer's tail couldn't possibly be the
        start of one. A real, complete match is found separately via `find`;
        this only catches a tag split across two token chunks, so ordinary
        text is never held back waiting for a match that will never come."""
        max_len = min(len(buf), len(_StreamToolCallFilter._OPEN) - 1)
        for length in range(max_len, 0, -1):
            if buf.endswith(_StreamToolCallFilter._OPEN[:length]):
                return length
        return 0

    def feed(self, text: str) -> List[Tuple[str, str]]:
        """Returns an ordered list of ``("text", chunk)`` / ``("block", raw)``
        pairs for *text*, in the exact order they occur — a "text" chunk is
        safe to forward as a token now, a "block" is a complete `<tool_call>`
        span (open tag through close tag). Preserving order (rather than
        collecting text and blocks into two separate lists) matters: it lets
        a caller stop partway through — e.g. once a dispatched block turns
        out to need approval — without forwarding or dispatching anything
        that came after it in this same call."""
        self._buf += text
        segments: List[Tuple[str, str]] = []
        while True:
            if not self.suppressing:
                idx = self._buf.find(self._OPEN)
                if idx == -1:
                    keep = self._partial_open_suffix_len(self._buf)
                    if len(self._buf) > keep:
                        cut = len(self._buf) - keep
                        segments.append(("text", self._buf[:cut]))
                        self._buf = self._buf[cut:]
                    break
                if idx:
                    segments.append(("text", self._buf[:idx]))
                self._buf = self._buf[idx:]
                self.suppressing = True
                continue
            idx = self._buf.find(self._CLOSE)
            if idx == -1:
                break
            end = idx + len(self._CLOSE)
            segments.append(("block", self._buf[:end]))
            self._buf = self._buf[end:]
            self.suppressing = False
        return segments

    def flush(self) -> str:
        """Remaining safe text once the stream ends. Returns "" while
        `suppressing` — that text belongs to an unclosed block and must stay
        hidden; the truncation path takes it from there."""
        if self.suppressing:
            return ""
        text, self._buf = self._buf, ""
        return text


class _ToolCallGuard:
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

    def record(self, tool_name: str, arguments: Dict[str, Any], result: ToolResult) -> None:
        """Track *result* for repeat-detection and failure accounting."""
        if result.success:
            return
        self.tool_failures[tool_name] = self.tool_failures.get(tool_name, 0) + 1
        key = self._call_key(tool_name, arguments)
        if key not in self._failed_calls:
            self._failed_calls[key] = result.error or "unknown error"


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

    # A model that wrote a tool call in the wrong format gets this many corrective
    # re-prompts before the turn surfaces an honest "couldn't format the call"
    # message instead of the raw markup (see tool_call_rescue).
    _MAX_RESCUE_RETRIES = 2

    # Appended as a trailing system message on the forced final call once
    # max_iterations is hit — without it the model, cut off with tools=[],
    # tends to either retry describing the tool call it can no longer make or
    # go silent instead of answering with what it already has.
    _TOOL_BUDGET_EXHAUSTED_MESSAGE = (
        "Tool budget for this turn is exhausted — answer now with what you "
        "have; say plainly what remains undone."
    )

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

    def _resolve_tool_calls(self, response) -> List[Dict[str, Any]]:
        """Get tool calls from response, falling back to XML parsing if needed."""
        if response.tool_calls:
            return response.tool_calls

        # Fallback: some models emit <tool_call> XML in the content
        if response.content:
            xml_calls = _parse_xml_tool_calls(response.content)
            if xml_calls:
                logger.debug(
                    f"[ToolExecutor] Parsed {len(xml_calls)} tool call(s) from XML in content"
                )
                return xml_calls

        return []

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
        """Run the tool loop. Returns final LLM response + execution records.

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
                loop's first LLM turn (see `_execute_forced_tool`), so a slash
                command reaches the model as an ordinary already-executed tool
                call it must present.
            iteration_nudge: Optional reminder text appended as a trailing
                system message on every LLM call once at least one tool round
                has completed this turn (see the class docstring note on
                recency). Synthesized fresh per call rather than stored in
                `working_messages`, so it is always the most recent message
                without ever needing to be removed or deduplicated.

        Returns:
            Tuple of (final LLMResponse, list of ToolExecution records)
        """
        tool_schemas = self.tool_registry.get_schemas(allowed_tools)
        tool_executions: List[ToolExecution] = []
        working_messages = list(messages)  # Don't mutate original

        rescue_records: List[Dict[str, Any]] = []
        rescue_retries = 0
        pending_approval = False
        guard = _ToolCallGuard()
        # The user's original attachment stays available for EVERY iteration of
        # the turn, not just the first. Tool-enabled modes instruct the model to
        # call tools before answering (see modes/builtin.py's generation-mode
        # prompt: "call get_form_state and get_active_models before answering —
        # don't wait to be asked"), so iteration 1 is almost never the model's
        # real answer — it's a tool call. Resetting the image to None right after
        # the first call silently blinds the model on the answer it actually
        # gives the user. A tool-returned image (e.g. a
        # render preview) takes precedence over the user's image for exactly the
        # next call, then reverts to the user's image afterward — a one-shot
        # "look at this" signal, not something worth resending on every
        # remaining iteration of a loop capped at max_iterations.
        user_image_data = image_data
        tool_image_data: Optional[str] = None

        if forced_tool_call:
            if on_tool_event:
                on_tool_event("tool_start", {
                    "tool_name": forced_tool_call["name"],
                    "arguments": forced_tool_call.get("arguments", {}),
                })
            forced_execution = await self._execute_forced_tool(
                forced_tool_call, tool_context, working_messages, tool_executions, allowed_tools
            )
            if on_tool_event:
                on_tool_event("tool_end", self._tool_end_event_data(forced_execution))
            if forced_execution.result.image_data:
                tool_image_data = forced_execution.result.image_data

        # See `iteration_nudge`'s docstring note above: True once this turn has
        # completed at least one tool round (a forced call counts as one).
        any_tool_round_completed = bool(forced_tool_call)

        for iteration in range(max_iterations):
            logger.debug(f"[ToolExecutor] Iteration {iteration + 1}/{max_iterations}")

            effective_image_data = tool_image_data if tool_image_data is not None else user_image_data
            call_messages = working_messages
            if iteration_nudge and any_tool_round_completed:
                call_messages = working_messages + [{"role": "system", "content": iteration_nudge}]

            # Call LLM with tools
            response = await self.llm_service.generate_with_tools(
                messages=call_messages,
                llm_id=llm_id,
                tools=tool_schemas,
                image_data=effective_image_data,
                custom_system_message=system_message,
                mode=mode,
                options_override=llm_options,
            )
            tool_image_data = None  # one-shot consumed; a tool below may set it again

            # Check if LLM wants to call tools (structured or XML fallback)
            tool_calls = self._resolve_tool_calls(response)
            if not tool_calls:
                # An opened `<tool_call>` that never closed is a truncated
                # generation, not a wrong-format near-miss — nothing to repair,
                # only the same bounded retry, so it's checked first.
                truncated = tool_call_rescue.find_truncated_tool_call(response.content or "")
                if truncated:
                    cleaned = tool_call_rescue.strip_spans(response.content or "", [truncated.span])
                    if rescue_retries < self._MAX_RESCUE_RETRIES:
                        rescue_retries += 1
                        working_messages.append({"role": "assistant", "content": cleaned})
                        working_messages.append({
                            "role": "system",
                            "content": tool_call_rescue.truncated_retry_nudge(truncated.tool_name),
                        })
                        continue
                    response.content = tool_call_rescue.truncated_fallback_message(truncated.tool_name)
                    response.rescues = rescue_records or None
                    response.tool_failures = dict(guard.tool_failures) if guard.tool_failures else None
                    return response, tool_executions

                # No parsed call — the content may still be a near-miss invocation
                # (wrong tag / fence / bare JSON) that must not reach the user raw.
                repaired, ambiguous, problems, records, cleaned = self._rescue_final_content(
                    response.content or "", allowed_tools
                )
                if repaired:
                    rescue_records.extend(records)
                    response.content = cleaned
                    tool_calls = repaired
                elif ambiguous and rescue_retries < self._MAX_RESCUE_RETRIES:
                    rescue_retries += 1
                    working_messages.append({"role": "assistant", "content": cleaned})
                    working_messages.append(
                        {"role": "system", "content": tool_call_rescue.retry_nudge(ambiguous, problems)}
                    )
                    continue
                elif ambiguous:
                    response.content = tool_call_rescue.fallback_message(ambiguous)
                    response.rescues = rescue_records or None
                    response.tool_failures = dict(guard.tool_failures) if guard.tool_failures else None
                    return response, tool_executions
                else:
                    # Final response — strip any leftover XML
                    response.content = strip_tool_call_xml(response.content or "")
                    response.rescues = rescue_records or None
                    response.tool_failures = dict(guard.tool_failures) if guard.tool_failures else None
                    logger.debug(f"[ToolExecutor] LLM returned final response after {iteration + 1} iteration(s)")
                    return response, tool_executions

            # Process tool calls
            # Append assistant message with tool_calls to working messages
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": strip_tool_call_xml(response.content or ""),
                "tool_calls": tool_calls,
            }
            working_messages.append(assistant_msg)

            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "")
                tool_call_id = tool_call.get("id", "")
                raw_args = tool_call.get("function", {}).get("arguments", "{}")

                # Parse arguments
                try:
                    if isinstance(raw_args, str):
                        arguments = json.loads(raw_args)
                    else:
                        arguments = raw_args
                except json.JSONDecodeError:
                    arguments = {}
                    logger.warning(f"[ToolExecutor] Failed to parse arguments for {tool_name}: {raw_args}")

                # Notify caller that tool is starting
                if on_tool_event:
                    on_tool_event("tool_start", {"tool_name": tool_name, "arguments": arguments})

                # Execute tool
                start_time = time.monotonic()
                result, is_pending = await self._execute_tool_guarded(
                    tool_name, tool_context, arguments, allowed_tools, guard
                )
                duration_ms = int((time.monotonic() - start_time) * 1000)

                # Record execution
                execution = ToolExecution(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                    duration_ms=duration_ms,
                    pending_approval=is_pending,
                )
                tool_executions.append(execution)

                # Notify caller that tool finished
                if on_tool_event:
                    event_data: Dict[str, Any] = {
                        "tool_name": tool_name,
                        "success": result.success,
                        "duration_ms": duration_ms,
                        "pending_approval": is_pending,
                    }
                    if is_pending:
                        event_data["arguments"] = arguments
                        preview = serialize_approval_preview(result.preview)
                        if preview:
                            event_data["preview"] = preview
                    if result.sources:
                        event_data["sources"] = [
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
                    on_tool_event("tool_end", event_data)

                if is_pending:
                    # Tool requires approval — stop the loop immediately.
                    # Do NOT feed the result back to the LLM yet.
                    logger.info(
                        f"[ToolExecutor] Tool '{tool_name}' requires approval — pausing loop"
                    )
                    pending_approval = True
                    break

                # Append tool result message for non-pending tools
                tool_result_msg: Dict[str, Any] = {
                    "role": "tool",
                    "content": _bound_tool_result_content(result.data if result.success else f"Error: {result.error}"),
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                }
                working_messages.append(tool_result_msg)

                # If the tool returned an image, it takes precedence over the
                # user's image for exactly the next iteration (see the
                # precedence note above this loop).
                if result.image_data:
                    tool_image_data = result.image_data

            if pending_approval:
                break

            any_tool_round_completed = True

        if pending_approval:
            # Return a synthetic response indicating the loop is paused
            response.content = ""
            response.rescues = rescue_records or None
            response.tool_failures = dict(guard.tool_failures) if guard.tool_failures else None
            return response, tool_executions

        # Max iterations reached - do one final call without tools
        logger.warning(f"[ToolExecutor] Max iterations ({max_iterations}) reached, making final call without tools")
        response = await self.llm_service.generate_with_tools(
            messages=working_messages,
            llm_id=llm_id,
            tools=[],  # No tools - force text response
            custom_system_message=system_message,
            mode=mode,
            options_override=llm_options,
        )
        response.content = strip_tool_call_xml(response.content or "")
        response.rescues = rescue_records or None
        response.tool_failures = dict(guard.tool_failures) if guard.tool_failures else None
        return response, tool_executions

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
        - {"type": "done", "data": {"tool_executions": [...], "full_content": ..., "pending_tool_approval": ...}}

        When a tool with requires_approval=True is executed, the generator emits a
        "done" event with pending_tool_approval=True and then returns immediately
        without feeding the result to the LLM.

        force_prompt_tools configurations (see `_force_prompt_tools_for`) are
        delegated to `_execute_with_tools_stream_legacy`, which buffers each
        iteration's full response before deciding whether it was a tool call —
        the same behavior this method used before it streamed natively.

        Every other config still streams tokens live, but a `<tool_call>` a
        client embeds in content (see `_parse_xml_tool_calls`'s docstring) is
        suppressed at the source rather than forwarded and cleaned up after
        the fact: `_StreamToolCallFilter` withholds everything from the open
        tag through the close tag, and the moment a block closes it is parsed
        and dispatched immediately — through the SAME `_run_tool_calls_stream`
        event surface a structured `tool_calls` response uses — without
        waiting for the rest of that iteration's generation to finish. A
        block that never closes falls through to the truncation/near-miss
        handling below exactly as before, since the accumulated
        `full_iter_content` still contains everything regardless of what was
        or wasn't forwarded live.
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

        tool_schemas = self.tool_registry.get_schemas(allowed_tools)
        tool_executions: List[ToolExecution] = []
        working_messages = list(messages)
        rescue_records: List[Dict[str, Any]] = []
        guard = _ToolCallGuard()
        # See `execute_with_tools`'s `iteration_nudge` docstring: True once
        # this turn has completed at least one tool round.
        any_tool_round_completed = False
        # Now that a <tool_call> span is suppressed at the source (see
        # _StreamToolCallFilter) instead of already having reached the user
        # live, a truncated or malformed one is exactly as safe to retry here
        # as it is on the buffered paths — same counter, same bound.
        rescue_retries = 0
        # See the precedence note in execute_with_tools: the user's image
        # persists across the whole turn; a tool-returned image is a one-shot
        # override for exactly the next call.
        user_image_data = image_data
        tool_image_data: Optional[str] = None

        if forced_tool_call:
            yield {"type": "tool_start", "data": {
                "tool_name": forced_tool_call["name"],
                "arguments": forced_tool_call.get("arguments", {}),
            }}
            forced_execution = await self._execute_forced_tool(
                forced_tool_call, tool_context, working_messages, tool_executions, allowed_tools
            )
            yield {"type": "tool_end", "data": self._tool_end_event_data(forced_execution)}
            if forced_execution.result.image_data:
                tool_image_data = forced_execution.result.image_data
            any_tool_round_completed = True

        for iteration in range(max_iterations):
            logger.debug(f"[ToolExecutor] Native stream iteration {iteration + 1}/{max_iterations}")

            effective_image_data = tool_image_data if tool_image_data is not None else user_image_data
            tool_image_data = None  # one-shot consumed now, before an inline dispatch below may set it again
            call_messages = working_messages
            if iteration_nudge and any_tool_round_completed:
                call_messages = working_messages + [{"role": "system", "content": iteration_nudge}]

            content_parts: List[str] = []
            tool_calls: Optional[List[Dict[str, Any]]] = None
            usage: Dict[str, Any] = {}
            # Set the moment a <tool_call> block closes mid-stream and is
            # dispatched inline (see _StreamToolCallFilter) — a real,
            # in-process tool execution, so once any attempt sets this the
            # empty-response retry below must never fire again for this
            # iteration even if the surrounding text happens to be empty.
            assistant_msg: Optional[Dict[str, Any]] = None
            control: Dict[str, Any] = {"pending": False, "tool_image_data": None}
            draining = False  # a pending-approval fired inline; keep consuming the generator, forward nothing more

            for attempt in range(self._EMPTY_RESPONSE_MAX_RETRIES):
                content_parts = []
                tool_calls = None
                usage = {}
                assistant_msg = None
                control = {"pending": False, "tool_image_data": None}
                draining = False
                stream_filter = _StreamToolCallFilter()

                async for event in self.llm_service.stream_with_tools(
                    messages=call_messages,
                    llm_id=llm_id,
                    tools=tool_schemas,
                    image_data=effective_image_data,
                    custom_system_message=system_message,
                    mode=mode,
                    options_override=llm_options,
                ):
                    event_type = event.get("type")
                    if event_type == "token":
                        content_parts.append(event["content"])
                        if draining:
                            continue
                        for kind, value in stream_filter.feed(event["content"]):
                            if draining:
                                # A call earlier in this SAME feed() result
                                # already turned out to need approval —
                                # nothing after it, text or another call, is
                                # forwarded or dispatched.
                                break
                            if kind == "text":
                                if value:
                                    yield {"type": "token", "data": {"content": value}}
                                continue
                            for call in _parse_xml_tool_calls(value):
                                if assistant_msg is None:
                                    assistant_msg = {"role": "assistant", "content": "", "tool_calls": []}
                                    working_messages.append(assistant_msg)
                                assistant_msg["tool_calls"].append(call)
                                async for tev in self._run_tool_calls_stream(
                                    [call], tool_context, working_messages, tool_executions, allowed_tools, guard
                                ):
                                    if tev["type"] == "_control":
                                        control = tev
                                        continue
                                    yield tev
                                if control["tool_image_data"]:
                                    tool_image_data = control["tool_image_data"]
                                if control["pending"]:
                                    draining = True
                                    break
                    elif event_type == "tool_calls":
                        tool_calls = event["tool_calls"]
                    elif event_type == "usage":
                        usage = event

                if not draining:
                    trailing = stream_filter.flush()
                    if trailing:
                        yield {"type": "token", "data": {"content": trailing}}

                if content_parts or tool_calls or assistant_msg is not None:
                    break
                if attempt < self._EMPTY_RESPONSE_MAX_RETRIES - 1:
                    logger.warning(
                        f"[ToolExecutor] Empty streamed response on iteration {iteration + 1} "
                        f"(attempt {attempt + 1}/{self._EMPTY_RESPONSE_MAX_RETRIES}), retrying"
                    )

            full_iter_content = "".join(content_parts)

            if draining:
                # A tool dispatched inline (above) needs approval — the loop
                # stops immediately, same as the structured-tool_calls path
                # below, without feeding a result back to the LLM yet.
                yield {"type": "done", "data": {
                    "tool_executions": tool_executions,
                    "full_content": "",
                    "pending_tool_approval": True,
                    "rescues": rescue_records or None,
                    "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
                }}
                return

            if assistant_msg is not None:
                # One or more <tool_call> blocks already closed and dispatched
                # inline as the stream produced them (see the token branch
                # above) — nothing pending, so patch in the surrounding text
                # and move straight to the next iteration; detection and
                # dispatch already happened, live.
                assistant_msg["content"] = strip_tool_call_xml(full_iter_content)
                any_tool_round_completed = True
                continue

            if not tool_calls and full_iter_content:
                # An opened `<tool_call>` cut off mid-payload never closed, so
                # the filter withheld it from the live stream entirely (see
                # _StreamToolCallFilter.flush) — it was never shown, so a
                # corrective retry is exactly as safe here as on the buffered
                # paths, bounded by the same counter.
                truncated = tool_call_rescue.find_truncated_tool_call(full_iter_content)
                if truncated:
                    cleaned = tool_call_rescue.strip_spans(full_iter_content, [truncated.span])
                    if rescue_retries < self._MAX_RESCUE_RETRIES:
                        rescue_retries += 1
                        working_messages.append({"role": "assistant", "content": cleaned})
                        working_messages.append({
                            "role": "system",
                            "content": tool_call_rescue.truncated_retry_nudge(truncated.tool_name),
                        })
                        continue
                    full_content = tool_call_rescue.truncated_fallback_message(truncated.tool_name)
                    yield {"type": "done", "data": {
                        "tool_executions": tool_executions,
                        "full_content": full_content,
                        "pending_tool_approval": False,
                        "rescues": rescue_records or None,
                        "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
                    }}
                    return

            if not tool_calls and full_iter_content:
                # Fallback: a structured tool_calls event never arrived and
                # nothing closed inline either — try the accumulated text as a
                # whole (covers a <tool_call> the filter somehow never saw
                # closed live, e.g. the entire response arriving as one token).
                tool_calls = _parse_xml_tool_calls(full_iter_content) or None

            if not tool_calls and full_iter_content:
                # A near-miss format (<tool_action>, a fence, bare JSON) or a
                # closed-but-malformed <tool_call> the inline dispatch above
                # found nothing to run for. The <tool_call> case was never
                # shown live (suppressed at the source), so — unlike the other
                # near-miss formats, which a model may still see mid-stream —
                # it is always safe to retry rather than just strip and answer.
                repaired, ambiguous, problems, records, cleaned = self._rescue_final_content(
                    full_iter_content, allowed_tools
                )
                if repaired:
                    rescue_records.extend(records)
                    full_iter_content = cleaned
                    tool_calls = repaired
                elif ambiguous and rescue_retries < self._MAX_RESCUE_RETRIES:
                    rescue_retries += 1
                    working_messages.append({"role": "assistant", "content": cleaned})
                    working_messages.append(
                        {"role": "system", "content": tool_call_rescue.retry_nudge(ambiguous, problems)}
                    )
                    continue
                elif ambiguous:
                    yield {"type": "done", "data": {
                        "tool_executions": tool_executions,
                        "full_content": tool_call_rescue.fallback_message(ambiguous),
                        "pending_tool_approval": False,
                        "rescues": rescue_records or None,
                        "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
                    }}
                    return

            if not tool_calls:
                full_content = strip_tool_call_xml(full_iter_content)
                logger.debug(f"[ToolExecutor] LLM returned final streamed response after {iteration + 1} iteration(s)")
                yield {"type": "done", "data": {
                    "tool_executions": tool_executions,
                    "full_content": full_content,
                    "pending_tool_approval": False,
                    "rescues": rescue_records or None,
                    "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
                    "tokens_used": usage.get("tokens_used"),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                }}
                return

            # A genuinely structured tool_calls event (or the whole-text
            # fallback above) — dispatch the traditional way, all at once.
            assistant_msg = {
                "role": "assistant",
                "content": strip_tool_call_xml(full_iter_content),
                "tool_calls": tool_calls,
            }
            working_messages.append(assistant_msg)

            control = {"pending": False, "tool_image_data": None}
            async for event in self._run_tool_calls_stream(
                tool_calls, tool_context, working_messages, tool_executions, allowed_tools, guard
            ):
                if event["type"] == "_control":
                    control = event
                    continue
                yield event

            if control["tool_image_data"]:
                tool_image_data = control["tool_image_data"]

            if control["pending"]:
                yield {"type": "done", "data": {
                    "tool_executions": tool_executions,
                    "full_content": "",
                    "pending_tool_approval": True,
                    "rescues": rescue_records or None,
                    "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
                }}
                return

            any_tool_round_completed = True

        # Max iterations reached — one final call without tools, streamed. A
        # visible signal accompanies the log line (never a silent forced
        # finish) and the final call carries an explicit wrap-up instruction
        # so the model doesn't just retry the tool it was cut off from.
        logger.warning(f"[ToolExecutor] Max iterations ({max_iterations}) reached, making final streamed call without tools")
        yield {"type": "status", "data": {
            "step": "tool_budget_exhausted", "state": "completed", "detail": {"max_iterations": max_iterations},
        }}
        final_content_parts: List[str] = []
        final_usage: Dict[str, Any] = {}
        async for event in self.llm_service.stream_with_tools(
            messages=working_messages + [{"role": "system", "content": self._TOOL_BUDGET_EXHAUSTED_MESSAGE}],
            llm_id=llm_id,
            tools=None,
            image_data=None,
            custom_system_message=system_message,
            mode=mode,
            options_override=llm_options,
        ):
            event_type = event.get("type")
            if event_type == "token":
                final_content_parts.append(event["content"])
                yield {"type": "token", "data": {"content": event["content"]}}
            elif event_type == "usage":
                final_usage = event

        final_content = strip_tool_call_xml("".join(final_content_parts))
        yield {"type": "done", "data": {
            "tool_executions": tool_executions,
            "full_content": final_content,
            "pending_tool_approval": False,
            "rescues": rescue_records or None,
            "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
            "tokens_used": final_usage.get("tokens_used"),
            "prompt_tokens": final_usage.get("prompt_tokens"),
            "completion_tokens": final_usage.get("completion_tokens"),
        }}

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
        embedded in ordinary content and can't be safely told apart from a
        real answer until the whole response is in. Kept byte-for-byte
        equivalent to what `execute_with_tools_stream` did before it grew a
        native-streaming path for everything else.

        Yields the same event shapes as `execute_with_tools_stream`.
        """
        tool_schemas = self.tool_registry.get_schemas(allowed_tools)
        tool_executions: List[ToolExecution] = []
        working_messages = list(messages)
        rescue_records: List[Dict[str, Any]] = []
        rescue_retries = 0
        guard = _ToolCallGuard()
        # See the precedence note in execute_with_tools: the user's image
        # persists across the whole turn; a tool-returned image is a one-shot
        # override for exactly the next call.
        user_image_data = image_data
        tool_image_data: Optional[str] = None
        # See `execute_with_tools`'s `iteration_nudge` docstring: True once
        # this turn has completed at least one tool round.
        any_tool_round_completed = False

        if forced_tool_call:
            yield {"type": "tool_start", "data": {
                "tool_name": forced_tool_call["name"],
                "arguments": forced_tool_call.get("arguments", {}),
            }}
            forced_execution = await self._execute_forced_tool(
                forced_tool_call, tool_context, working_messages, tool_executions, allowed_tools
            )
            yield {"type": "tool_end", "data": self._tool_end_event_data(forced_execution)}
            if forced_execution.result.image_data:
                tool_image_data = forced_execution.result.image_data
            any_tool_round_completed = True

        for iteration in range(max_iterations):
            logger.debug(f"[ToolExecutor] Stream iteration {iteration + 1}/{max_iterations}")

            effective_image_data = tool_image_data if tool_image_data is not None else user_image_data
            call_messages = working_messages
            if iteration_nudge and any_tool_round_completed:
                call_messages = working_messages + [{"role": "system", "content": iteration_nudge}]

            # Non-streaming call with tools (need full response for tool_call detection)
            response = await self.llm_service.generate_with_tools(
                messages=call_messages,
                llm_id=llm_id,
                tools=tool_schemas,
                image_data=effective_image_data,
                custom_system_message=system_message,
                mode=mode,
                options_override=llm_options,
            )
            tool_image_data = None  # one-shot consumed; a tool below may set it again

            # Check for tool calls (structured or XML fallback)
            tool_calls = self._resolve_tool_calls(response)

            if not tool_calls:
                # This buffered path holds the whole response before emitting a
                # token, so a truncated or near-miss invocation is caught here —
                # no raw markup is ever streamed to the user. A truncated
                # `<tool_call>` is checked first: it's a cut-off generation, not
                # a wrong-format near-miss, so it's steered with the same
                # bounded retry rather than passed to the near-miss repair.
                truncated = tool_call_rescue.find_truncated_tool_call(response.content or "")
                if truncated:
                    cleaned = tool_call_rescue.strip_spans(response.content or "", [truncated.span])
                    if rescue_retries < self._MAX_RESCUE_RETRIES:
                        rescue_retries += 1
                        working_messages.append({"role": "assistant", "content": cleaned})
                        working_messages.append({
                            "role": "system",
                            "content": tool_call_rescue.truncated_retry_nudge(truncated.tool_name),
                        })
                        continue
                    full_content = tool_call_rescue.truncated_fallback_message(truncated.tool_name)
                    if full_content:
                        yield {"type": "token", "data": {"content": full_content}}
                    yield {"type": "done", "data": {
                        "tool_executions": tool_executions,
                        "full_content": full_content,
                        "pending_tool_approval": False,
                        "rescues": rescue_records or None,
                        "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
                        "tokens_used": response.tokens_used,
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                    }}
                    return

                repaired, ambiguous, problems, records, cleaned = self._rescue_final_content(
                    response.content or "", allowed_tools
                )
                if repaired:
                    rescue_records.extend(records)
                    response.content = cleaned
                    tool_calls = repaired
                elif ambiguous and rescue_retries < self._MAX_RESCUE_RETRIES:
                    rescue_retries += 1
                    working_messages.append({"role": "assistant", "content": cleaned})
                    working_messages.append(
                        {"role": "system", "content": tool_call_rescue.retry_nudge(ambiguous, problems)}
                    )
                    continue
                else:
                    # No tool calls — use the response we already have instead of
                    # making a redundant second LLM call. Emit the content as a
                    # single token event so the frontend receives it immediately.
                    full_content = (
                        tool_call_rescue.fallback_message(ambiguous)
                        if ambiguous else strip_tool_call_xml(response.content or "")
                    )
                    if full_content:
                        yield {"type": "token", "data": {"content": full_content}}
                    yield {"type": "done", "data": {
                        "tool_executions": tool_executions,
                        "full_content": full_content,
                        "pending_tool_approval": False,
                        "rescues": rescue_records or None,
                        "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
                        "tokens_used": response.tokens_used,
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                    }}
                    return

            # Process tool calls
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": strip_tool_call_xml(response.content or ""),
                "tool_calls": tool_calls,
            }
            working_messages.append(assistant_msg)

            pending_this_iteration = False

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

                # Yield tool_start event immediately
                yield {"type": "tool_start", "data": {"tool_name": tool_name, "arguments": arguments}}

                # Execute tool
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

                # Build tool_end event data
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
                    # Pause the tool loop — emit done with pending flag and return.
                    logger.info(
                        f"[ToolExecutor] Tool '{tool_name}' requires approval — pausing stream loop"
                    )
                    pending_this_iteration = True
                    yield {"type": "done", "data": {
                        "tool_executions": tool_executions,
                        "full_content": "",
                        "pending_tool_approval": True,
                        "rescues": rescue_records or None,
                        "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
                    }}
                    return

                # Only append tool result to working messages for non-pending tools
                tool_result_msg: Dict[str, Any] = {
                    "role": "tool",
                    "content": _bound_tool_result_content(result.data if result.success else f"Error: {result.error}"),
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                }
                working_messages.append(tool_result_msg)

                if result.image_data:
                    tool_image_data = result.image_data

            if pending_this_iteration:
                return

            any_tool_round_completed = True

        # Max iterations reached — one final call without tools, strip XML,
        # emit at once. A visible signal accompanies the log line (never a
        # silent forced finish) and the final call carries an explicit
        # wrap-up instruction.
        logger.warning(f"[ToolExecutor] Max iterations ({max_iterations}) reached, making final call without tools")
        yield {"type": "status", "data": {
            "step": "tool_budget_exhausted", "state": "completed", "detail": {"max_iterations": max_iterations},
        }}
        response = await self.llm_service.generate_with_tools(
            messages=working_messages + [{"role": "system", "content": self._TOOL_BUDGET_EXHAUSTED_MESSAGE}],
            llm_id=llm_id,
            tools=[],
            custom_system_message=system_message,
            mode=mode,
            options_override=llm_options,
        )
        full_content = strip_tool_call_xml(response.content or "")
        if full_content:
            yield {"type": "token", "data": {"content": full_content}}
        yield {"type": "done", "data": {
            "tool_executions": tool_executions,
            "full_content": full_content,
            "pending_tool_approval": False,
            "rescues": rescue_records or None,
            "tool_failures": dict(guard.tool_failures) if guard.tool_failures else None,
            "tokens_used": response.tokens_used,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }}

    # Appended to `system_message` on retries after an empty presentation
    # completion, to steer a model that emitted a bare/empty <tool_call> block
    # away from repeating it now that no tools are offered.
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
