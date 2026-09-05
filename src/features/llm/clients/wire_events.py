"""Protocol-neutral events a provider stream decoder emits.

The two wire protocols this codebase speaks — OpenAI-compatible SSE and Ollama's
NDJSON — disagree on framing, on the terminator, and on whether tool calls
arrive whole or in indexed fragments. They agree on what a turn is made of, and
that is what these carry, so a client's stream loop reads the same event kinds
whichever decoder produced them.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallDelta:
    """One fragment of a tool call, in the OpenAI indexed-streaming shape.

    The first fragment for an index carries id/type/name; later ones only append
    to ``arguments``. Assembling them is ``ToolCallAssembler``'s job.
    """

    index: int
    id: Optional[str]
    type: Optional[str]
    name: Optional[str]
    arguments: Optional[str]


@dataclass(frozen=True)
class ToolCalls:
    """A complete tool_call list, the way Ollama delivers it in one chunk."""

    tool_calls: List[Dict[str, Any]]


@dataclass(frozen=True)
class Usage:
    tokens_used: Optional[int]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]


@dataclass(frozen=True)
class RecordTooLarge:
    """An SSE record's accumulated ``data:`` payload exceeded
    ``OpenAICompatSSEDecoder.MAX_RECORD_BYTES`` before its terminating blank
    line ever arrived. The record is dropped in full — never dispatched,
    even once (or if) a later blank line would have completed it — so one
    connection can never grow this client's memory without bound. Decoding
    resumes cleanly on the next record; ``size`` is the byte count at the
    point of the drop, for the warning this rides alongside."""

    size: int


@dataclass(frozen=True)
class Done:
    """The terminator: an SSE ``[DONE]`` frame, or an NDJSON ``done: true``.

    ``finish_reason`` is the provider's own raw stop-reason string (OpenAI's
    ``choices[0].finish_reason``, Ollama's ``done_reason``) when the decoder
    saw one, else ``None`` — never inferred from reaching the terminator
    itself. See ``clients.completion`` for the normalized shape a client
    builds from it.
    """

    finish_reason: Optional[str] = None
