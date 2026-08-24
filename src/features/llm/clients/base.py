from typing import Any, AsyncGenerator, Dict, List, Optional, Protocol

from pydantic import BaseModel

from src.features.llm.repository import LLMConfig


class LLMResponse(BaseModel):
    content: str
    model: str
    provider_id: str
    tokens_used: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    tool_calls: Optional[list] = None
    finish_reason: Optional[str] = None
    # Records of malformed tool invocations the executor repaired or steered this
    # turn (see tool_call_rescue); surfaced into the behavior-trace manifest.
    rescues: Optional[list] = None
    # Per-tool failure counts for this turn's tool loop (see
    # ToolExecutor._ToolCallGuard); same dict[str, int] | None shape the
    # streaming `done` events carry under the same key.
    tool_failures: Optional[dict] = None


class LLMClient(Protocol):
    """A single-provider transport for one LLM configuration.

    Implementations translate a resolved config and an already-resolved system
    message into the provider's wire format, issue the HTTP call, and normalize
    the reply into an ``LLMResponse`` (or a stream of token/usage dicts). The
    system-message resolution and config selection happen upstream in the
    gateway; a client is handed exactly what to send.
    """

    async def generate(
        self,
        prompt: str,
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
    ) -> LLMResponse: ...

    async def generate_with_history(
        self,
        messages: list[Dict[str, str]],
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse: ...

    def stream_with_history(
        self,
        messages: list[Dict[str, str]],
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[dict, None]: ...

    async def generate_with_tools(
        self,
        messages: list[Dict[str, Any]],
        config: LLMConfig,
        system_message: str,
        tools: List[Dict] = None,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse: ...

    def stream_with_tools(
        self,
        messages: list[Dict[str, Any]],
        config: LLMConfig,
        system_message: str,
        tools: Optional[List[Dict]] = None,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[dict, None]: ...
