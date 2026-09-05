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
class Done:
    """The terminator: an SSE ``[DONE]`` frame, or an NDJSON ``done: true``."""
