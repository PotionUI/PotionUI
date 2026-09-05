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
from src.features.llm.clients.wire_events import Done, TextDelta, ToolCallDelta, Usage
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
    """Turns SSE ``data:`` lines into protocol-neutral events.

    ``label`` reaches only the warning logged for an undecodable frame — the two
    stream methods have always logged under their own tag.
    """

    def __init__(self, label: str):
        self._label = label

    def feed(self, line: str) -> Iterator[Any]:
        if not line or not line.startswith("data: "):
            return
        data_str = line[len("data: "):]
        if data_str.strip() == "[DONE]":
            yield Done()
            return
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            logging.warning(f"[{self._label}] Failed to parse SSE data: {data_str}")
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
