"""Normalizes each provider's raw stop-reason string into one shared shape.

Every client tells a stream or a buffered response apart from an aborted one
in its own vocabulary — OpenAI-compatible servers put a `finish_reason` on the
last choice, Ollama puts `done_reason` on the terminal NDJSON line, the native
engine has neither and must infer the boundary from the generation config.
None of that is allowed to leak into a shape callers must special-case per
provider, and none of it is allowed to be *invented*: a provider that reports
nothing stays "unknown" rather than being defaulted to "stop", and the raw
string is always kept alongside the normalized bucket so an operator can still
see exactly what the provider said.

``{"reason": "stop"|"length"|"tool_calls"|"unknown", "raw": <provider string
or None>}`` is the whole contract. It rides as ``LLMResponse.completion`` on
every buffered path and as an extra ``completion`` key on the streaming
"usage" event dict; from there it is persisted verbatim (see
``ConversationRunner._build_behavior_trace``) and forwarded on the SSE `done`
event's `assistant_message.metadata.behavior_trace.completion`.
"""

from typing import Any, Dict, Optional

REASON_STOP = "stop"
REASON_LENGTH = "length"
REASON_TOOL_CALLS = "tool_calls"
REASON_UNKNOWN = "unknown"


def _outcome(reason: str, raw: Optional[str]) -> Dict[str, Any]:
    return {"reason": reason, "raw": raw}


def stop(raw: Optional[str] = None) -> Dict[str, Any]:
    return _outcome(REASON_STOP, raw)


def length(raw: Optional[str] = None) -> Dict[str, Any]:
    return _outcome(REASON_LENGTH, raw)


def tool_calls(raw: Optional[str] = None) -> Dict[str, Any]:
    return _outcome(REASON_TOOL_CALLS, raw)


def unknown(raw: Optional[str] = None) -> Dict[str, Any]:
    return _outcome(REASON_UNKNOWN, raw)


# OpenAI-compatible /chat/completions `choices[0].finish_reason`. `length` and
# `content_filter` are both real terminal conditions, but only `length` is one
# of the buckets a caller acts on — `content_filter` keeps its raw string and
# reports `unknown` so it is never mistaken for a token-limit truncation.
_OPENAI_COMPAT_MAP = {
    "stop": REASON_STOP,
    "length": REASON_LENGTH,
    "tool_calls": REASON_TOOL_CALLS,
    "content_filter": REASON_UNKNOWN,
}

# Ollama's documented `done_reason` values (docs.ollama.com/api/chat) are
# `stop` and `length`; anything else (including values not documented there,
# such as `load`/`unload`) is reported unknown-with-raw rather than guessed at.
_OLLAMA_MAP = {
    "stop": REASON_STOP,
    "length": REASON_LENGTH,
}


def from_openai_compat(raw: Optional[str]) -> Dict[str, Any]:
    """Normalize an OpenAI-compatible `finish_reason` string. `None` (absent
    on the wire, or `[DONE]` reached with no prior choice carrying one) stays
    unknown — never inferred as `stop`."""
    if raw is None:
        return unknown(None)
    return _outcome(_OPENAI_COMPAT_MAP.get(raw, REASON_UNKNOWN), raw)


def from_ollama(raw: Optional[str]) -> Dict[str, Any]:
    """Normalize an Ollama `done_reason` string. `None` (absent, or `done:
    true` reached with no `done_reason` field) stays unknown."""
    if raw is None:
        return unknown(None)
    return _outcome(_OLLAMA_MAP.get(raw, REASON_UNKNOWN), raw)
