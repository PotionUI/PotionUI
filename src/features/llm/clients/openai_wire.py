"""Request building and SSE decoding for OpenAI-compatible /chat/completions.

The buffered and the streaming client methods share one builder, so a payload
cannot drift between them, and one decoder, so a chunk parses the same way in
both. Everything provider-specific about the wire lives here; the client methods
keep only what they do with the result.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

from src.features.llm.clients.tool_call_shape import normalize_tool_calls
from src.features.llm.clients.wire_events import Done, RecordTooLarge, TextDelta, ToolCallDelta, Usage
from src.features.llm.repository import LLMConfig


@dataclass
class OpenAIRequest:
    """A ready-to-send call, plus the two parts the trace record wants back."""

    payload: Dict[str, Any]
    messages: List[Dict[str, Any]]
    sampling_params: Dict[str, Any]
    headers: Dict[str, str]


def auth_headers(config: LLMConfig) -> Dict[str, str]:
    """Base JSON headers plus a Bearer token when the config carries an api_key."""
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def sampling_params(
    config: LLMConfig, options_override: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Build the sampling portion of the request payload.

    Includes temperature/max_tokens from config, top_p/presence_penalty/frequency_penalty
    from config.provider_options when present, with options_override merged last (only
    OpenAI-valid keys are honored; top_k is not an OpenAI param and is ignored).
    """
    provider_opts = config.provider_options or {}
    options_override = options_override or {}

    params: Dict[str, Any] = {
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }

    for key in ("top_p", "presence_penalty", "frequency_penalty"):
        if key in provider_opts:
            params[key] = provider_opts[key]

    for key in ("temperature", "max_tokens", "top_p"):
        if key in options_override:
            params[key] = options_override[key]

    return params


def _attach_image(api_messages: List[Dict[str, Any]], image_data: str) -> None:
    """Rewrite the last user turn as an image part followed by its text.

    Image before text per multimodal best practice (Gemma modality order).
    """
    for i in range(len(api_messages) - 1, -1, -1):
        if api_messages[i]["role"] == "user":
            api_messages[i]["content"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                },
                {"type": "text", "text": api_messages[i]["content"]},
            ]
            break


def build_openai_messages(
    messages: List[Dict[str, Any]],
    system_message: str,
    image_data: Optional[str],
    *,
    tool_aware: bool,
) -> List[Dict[str, Any]]:
    """Prepend the system turn, convert the history, attach an image if given.

    ``tool_aware`` is picked by the calling method, not by whether tools were
    passed: the tool-calling methods must round-trip `tool` results and
    assistant tool_calls even on a turn that offers no tools, while the plain
    history methods never see those roles.
    """
    api_messages: List[Dict[str, Any]] = [{"role": "system", "content": system_message}]

    for msg in messages:
        if not tool_aware:
            api_messages.append({"role": msg["role"], "content": msg["content"]})
        elif msg["role"] == "tool":
            # Tool result message - preserve tool_call_id and name
            tool_msg: Dict[str, Any] = {
                "role": "tool",
                "content": msg["content"],
                "tool_call_id": msg["tool_call_id"],
            }
            if "name" in msg:
                tool_msg["name"] = msg["name"]
            api_messages.append(tool_msg)
        elif msg["role"] == "assistant" and msg.get("tool_calls"):
            # Assistant message with tool calls — the OpenAI wire wants
            # string arguments (canonical in-process shape is an object).
            api_messages.append({
                "role": "assistant",
                "content": msg.get("content", ""),
                "tool_calls": normalize_tool_calls(msg["tool_calls"], as_object=False),
            })
        else:
            api_messages.append({"role": msg["role"], "content": msg.get("content", "")})

    if image_data:
        _attach_image(api_messages, image_data)

    return api_messages


def build_openai_request(
    config: LLMConfig,
    messages: List[Dict[str, Any]],
    system_message: str,
    image_data: Optional[str] = None,
    options_override: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict]] = None,
    *,
    tool_aware: bool = False,
    stream: bool = False,
) -> OpenAIRequest:
    """Assemble the whole call — headers, messages, sampling knobs, tools."""
    api_messages = build_openai_messages(
        messages, system_message, image_data, tool_aware=tool_aware
    )
    params = sampling_params(config, options_override)

    payload: Dict[str, Any] = {
        "model": config.model,
        "messages": api_messages,
        **params,
    }
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    if tools:
        payload["tools"] = tools

    return OpenAIRequest(
        payload=payload,
        messages=api_messages,
        sampling_params=params,
        headers=auth_headers(config),
    )


def prompt_from(prompt: str) -> List[Dict[str, Any]]:
    """The single-turn history a bare prompt stands for."""
    return [{"role": "user", "content": prompt}]


class OpenAICompatSSEDecoder:
    """Turns SSE lines into protocol-neutral events.

    Implements the subset of the WHATWG "process a field"/event-stream
    algorithm this wire actually needs: a logical record is one or more
    ``data:`` field lines, joined with ``"\\n"`` and dispatched together on
    the terminating blank line — NOT one record per line, which silently
    drops any record a provider legally splits across several ``data:``
    lines. A line starting with ``:`` is a comment (ignored); any other
    field name (``event``, ``id``, ``retry``, ...) is accepted but ignored,
    since this protocol never uses them; a line with no ``:`` at all is the
    field name with an empty value, per spec. Exactly one leading space is
    stripped from a field's value when present (``"data: x"`` and
    ``"data:x"`` both carry ``"x"``).

    This is record-boundary reassembly, not chunk/line reassembly —
    ``response.aiter_lines()`` already yields complete lines with their
    ``\\r\\n``/``\\n``/``\\r`` terminator stripped regardless of how the
    transport chunked the bytes, so ``feed()`` is never handed a partial
    line.

    Supported SSE subset (this repo has no dedicated LLM-provider docs page
    — see docs/chat-evaluation.md's own precedent for a field with nowhere
    else to live — so this docstring is the reference):

    - Multiple ``data:`` lines in one record join with ``"\\n"`` before
      being interpreted, per spec — never parsed as separate records.
    - A ``:``-prefixed line is a comment; ``event``/``id``/``retry`` (and
      any other field name) are accepted but ignored — this protocol never
      sends them.
    - A single leading UTF-8 BOM on the very first line of the stream is
      stripped once; a BOM anywhere else is ordinary data.
    - A record with no terminating blank line when the body ends (a
      truncated response, a dropped connection) is discarded, never
      dispatched — see ``finish()``.
    - A record whose accumulated ``data:`` payload exceeds
      ``MAX_RECORD_BYTES`` (1 MiB) is dropped in full and logged; decoding
      resumes cleanly on the next record — see ``RecordTooLarge``.

    ``label`` reaches only the warning logged for an undecodable, dropped or
    oversized record — the two stream methods have always logged under
    their own tag.
    """

    # A connection that never sends a terminating blank line for a record —
    # or sends one absurdly large record — must not grow this client's
    # memory without bound.
    MAX_RECORD_BYTES = 1 * 1024 * 1024

    def __init__(self, label: str):
        self._label = label
        self._data: List[str] = []
        self._data_bytes = 0
        self._seen_first_line = False
        # The choice that carries `finish_reason` arrives on a delta chunk
        # BEFORE the separate choice-less usage chunk and the terminal
        # `[DONE]` frame, so it has to be remembered across records rather
        # than read off the record that triggers Done().
        self._finish_reason: Optional[str] = None

    def _reset_record(self) -> None:
        self._data = []
        self._data_bytes = 0

    def feed(self, line: str) -> Iterator[Any]:
        if not self._seen_first_line:
            self._seen_first_line = True
            if line.startswith("﻿"):  # a leading UTF-8 BOM, stripped once
                line = line[1:]

        if line == "":
            yield from self._dispatch()
            return
        if line.startswith(":"):
            return  # comment — never part of a record's data

        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field != "data":
            return  # event/id/retry/unknown — ignored; this protocol never sends them

        value_bytes = len(value.encode("utf-8"))
        if self._data_bytes + value_bytes > self.MAX_RECORD_BYTES:
            size = self._data_bytes + value_bytes
            logging.warning(f"[{self._label}] SSE record exceeded {self.MAX_RECORD_BYTES} bytes; dropping it")
            self._reset_record()
            yield RecordTooLarge(size)
            return
        self._data.append(value)
        self._data_bytes += value_bytes

    def finish(self) -> None:
        """Call once after the response body's line loop ends normally. A
        record with no terminating blank line — the connection ended
        mid-record — is discarded, never dispatched: an incomplete
        ``[DON`` is not a completion terminator, and incomplete JSON is not
        a valid one either."""
        if self._data:
            logging.warning(
                f"[{self._label}] stream ended mid-record; discarding {len(self._data)} pending data line(s)"
            )
        self._reset_record()

    def _dispatch(self) -> Iterator[Any]:
        if not self._data:
            return  # a blank line with no preceding data field never dispatches
        payload = "\n".join(self._data)
        self._reset_record()

        if payload == "[DONE]":
            yield Done(finish_reason=self._finish_reason)
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logging.warning(f"[{self._label}] Failed to parse SSE data: {payload}")
            return

        # OpenAI puts usage on a final choice-less chunk when stream_options asks for it.
        if data.get("usage"):
            usage = data["usage"]
            yield Usage(
                tokens_used=usage.get("total_tokens"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )

        choices = data.get("choices", [])
        if not choices:
            return
        finish_reason = choices[0].get("finish_reason")
        if finish_reason:
            self._finish_reason = finish_reason
        delta = choices[0].get("delta", {})
        content = delta.get("content")
        if content:
            yield TextDelta(content)
        for fragment in delta.get("tool_calls") or []:
            function = fragment.get("function") or {}
            yield ToolCallDelta(
                index=fragment.get("index", 0),
                id=fragment.get("id"),
                type=fragment.get("type"),
                name=function.get("name"),
                arguments=function.get("arguments"),
            )


class ToolCallAssembler:
    """Rebuilds whole tool calls from OpenAI's indexed streaming fragments."""

    def __init__(self):
        self._by_index: Dict[int, Dict[str, Any]] = {}

    def add(self, fragment: ToolCallDelta) -> None:
        entry = self._by_index.setdefault(
            fragment.index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if fragment.id:
            entry["id"] = fragment.id
        if fragment.type:
            entry["type"] = fragment.type
        if fragment.name:
            entry["function"]["name"] += fragment.name
        if fragment.arguments:
            entry["function"]["arguments"] += fragment.arguments

    def assembled(self) -> Optional[List[Dict[str, Any]]]:
        if not self._by_index:
            return None
        return [self._by_index[index] for index in sorted(self._by_index)]
