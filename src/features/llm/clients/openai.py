import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from src.features.llm import trace_collector
from src.features.llm.clients import completion as completion_outcome
from src.features.llm.clients.base import LLMResponse
from src.features.llm.clients.openai_wire import (
    OpenAICompatSSEDecoder,
    ToolCallAssembler,
    build_openai_request,
    prompt_from,
)
from src.features.llm.clients.wire_events import Done, TextDelta, ToolCallDelta, Usage
from src.features.llm.repository import LLMConfig

NO_USAGE = {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None}


class OpenAIClient:
    """Talks to any OpenAI-compatible /chat/completions endpoint.

    Covers hosted OpenAI as well as local servers that speak the same schema
    (vLLM, LM Studio, llama.cpp, ...). Bearer auth is optional so keyless local
    endpoints work unchanged. Payload building and chunk parsing live in
    ``openai_wire``; what stays here is the HTTP call and the response shaping.
    """

    @staticmethod
    def _stream_timeout(config: LLMConfig) -> httpx.Timeout:
        return httpx.Timeout(
            connect=config.timeout, read=None, write=config.timeout, pool=config.timeout
        )

    async def generate(
        self,
        prompt: str,
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
    ) -> LLMResponse:
        """Generate a single response.

        Args:
            prompt: The text prompt
            config: LLM configuration
            system_message: System message/instructions
            image_data: Optional base64 encoded image for vision models (GPT-4 Vision, etc.)
        """
        try:
            request = build_openai_request(
                config, prompt_from(prompt), system_message, image_data
            )

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                logging.info(f"[OpenAI] Sending to {config.base_url}/chat/completions, model: {config.model}, image: {bool(image_data)}")

                response = await client.post(
                    f"{config.base_url}/chat/completions",
                    json=request.payload,
                    headers=request.headers
                )
                if response.status_code != 200:
                    logging.error(response.text)

                response.raise_for_status()

                data = response.json()
                usage = data.get("usage", {})

                return LLMResponse(
                    content=data["choices"][0]["message"]["content"],
                    model=config.model,
                    provider_id=config.id,
                    tokens_used=usage.get("total_tokens"),
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens")
                )
        except Exception as e:
            raise ValueError(f"Error generating OpenAI response: {str(e)}")

    async def generate_with_history(
        self,
        messages: list[Dict[str, str]],
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Generate a response over a full conversation history."""
        try:
            request = build_openai_request(
                config, messages, system_message, image_data, options_override
            )

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                logging.info(f"[OpenAI Chat] Sending to {config.base_url}/chat/completions, model: {config.model}, messages: {len(request.messages)}, image: {bool(image_data)}")

                _trace_start = time.monotonic()
                response = await client.post(
                    f"{config.base_url}/chat/completions",
                    json=request.payload,
                    headers=request.headers
                )

                if response.status_code != 200:
                    logging.error(response.text)

                response.raise_for_status()
                data = response.json()
                usage = data.get("usage", {})
                choice = data["choices"][0]
                content = choice["message"]["content"]
                finish_reason = choice.get("finish_reason")

                trace_collector.record(
                    provider="openai",
                    model=config.model,
                    request_system=system_message,
                    request_messages=request.messages,
                    request_params=request.sampling_params,
                    response_text=content,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    duration_ms=int((time.monotonic() - _trace_start) * 1000),
                )

                return LLMResponse(
                    content=content,
                    model=config.model,
                    provider_id=config.id,
                    tokens_used=usage.get("total_tokens"),
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    finish_reason=finish_reason,
                    completion=completion_outcome.from_openai_compat(finish_reason),
                )
        except Exception as e:
            raise ValueError(f"Error generating OpenAI response: {str(e)}")

    async def stream_with_history(
        self,
        messages: list[Dict[str, str]],
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[dict, None]:
        """Stream a response over a full conversation history."""
        request = build_openai_request(
            config, messages, system_message, image_data, options_override, stream=True
        )

        logging.info(f"[OpenAI Stream] Sending streaming request to {config.base_url}/chat/completions")

        decoder = OpenAICompatSSEDecoder("OpenAI Stream")
        usage_data = None
        finish_reason_raw = None
        full_content_parts: List[str] = []
        _trace_start = time.monotonic()
        async with httpx.AsyncClient(timeout=self._stream_timeout(config)) as client:
            async with client.stream("POST", f"{config.base_url}/chat/completions", json=request.payload, headers=request.headers) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise ValueError(f"OpenAI returned status {response.status_code}: {error_text.decode()}")

                async for line in response.aiter_lines():
                    stop = False
                    for event in decoder.feed(line):
                        if isinstance(event, Done):
                            finish_reason_raw = event.finish_reason
                            stop = True
                            break
                        if isinstance(event, Usage):
                            usage_data = {
                                "type": "usage",
                                "tokens_used": event.tokens_used,
                                "prompt_tokens": event.prompt_tokens,
                                "completion_tokens": event.completion_tokens,
                            }
                        elif isinstance(event, TextDelta):
                            full_content_parts.append(event.text)
                            yield {"type": "token", "content": event.text}
                    if stop:
                        break

        trace_collector.record(
            provider="openai",
            model=config.model,
            request_system=system_message,
            request_messages=request.messages,
            request_params=request.sampling_params,
            response_text="".join(full_content_parts),
            prompt_tokens=(usage_data or {}).get("prompt_tokens"),
            completion_tokens=(usage_data or {}).get("completion_tokens"),
            duration_ms=int((time.monotonic() - _trace_start) * 1000),
        )

        # Yield usage data at the end, always carrying the normalized completion outcome.
        result = usage_data or dict(NO_USAGE)
        result["completion"] = completion_outcome.from_openai_compat(finish_reason_raw)
        yield result

    async def generate_with_tools(
        self,
        messages: list[Dict[str, Any]],
        config: LLMConfig,
        system_message: str,
        tools: List[Dict] = None,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Generate a response with native tool calling."""
        try:
            request = build_openai_request(
                config, messages, system_message, image_data, options_override, tools,
                tool_aware=True,
            )

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                logging.info(f"[OpenAI Tools] Sending to {config.base_url}/chat/completions, model: {config.model}, messages: {len(request.messages)}, tools: {len(tools) if tools else 0}")

                _trace_start = time.monotonic()
                response = await client.post(
                    f"{config.base_url}/chat/completions",
                    json=request.payload,
                    headers=request.headers
                )

                if response.status_code != 200:
                    logging.error(response.text)

                response.raise_for_status()
                data = response.json()
                usage = data.get("usage", {})

                choice = data["choices"][0]
                message = choice.get("message", {})
                content = message.get("content") or ""
                tool_calls = message.get("tool_calls") or None
                finish_reason = choice.get("finish_reason")

                trace_collector.record(
                    provider="openai",
                    model=config.model,
                    request_system=system_message,
                    request_messages=request.messages,
                    request_params=request.sampling_params,
                    request_tools=[t.get("function", {}).get("name") for t in (tools or [])],
                    response_text=content,
                    response_tool_calls=tool_calls,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    duration_ms=int((time.monotonic() - _trace_start) * 1000),
                )

                return LLMResponse(
                    content=content,
                    model=config.model,
                    provider_id=config.id,
                    tokens_used=usage.get("total_tokens"),
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                    completion=completion_outcome.from_openai_compat(finish_reason),
                )
        except Exception as e:
            raise ValueError(f"Error generating OpenAI response: {str(e)}")

    async def stream_with_tools(
        self,
        messages: list[Dict[str, Any]],
        config: LLMConfig,
        system_message: str,
        tools: Optional[List[Dict]] = None,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[dict, None]:
        """Stream a response over a history that may carry tool messages.

        When `tools` is given, this is a native tool-calling turn: the model
        may emit `delta.tool_calls` fragments (assembled by ``ToolCallAssembler``)
        instead of, or possibly alongside, `delta.content` tokens. A "token"
        event is yielded for every content delta as it arrives — safe because
        OpenAI-compatible function-calling models don't mix a real content
        answer with tool_calls in the same turn — and a single "tool_calls"
        event is yielded at the end iff any were assembled, so the caller can
        tell a tool-invoking turn apart from a plain-text one without waiting
        for the whole thing to buffer.
        """
        request = build_openai_request(
            config, messages, system_message, image_data, options_override, tools,
            tool_aware=True, stream=True,
        )

        logging.info(
            f"[OpenAI Tools Stream] Sending streaming request to {config.base_url}/chat/completions, "
            f"tools: {len(tools) if tools else 0}"
        )

        decoder = OpenAICompatSSEDecoder("OpenAI Tools Stream")
        assembler = ToolCallAssembler()
        usage_data = None
        finish_reason_raw = None
        full_content_parts: List[str] = []
        _trace_start = time.monotonic()
        async with httpx.AsyncClient(timeout=self._stream_timeout(config)) as client:
            async with client.stream("POST", f"{config.base_url}/chat/completions", json=request.payload, headers=request.headers) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise ValueError(f"OpenAI returned status {response.status_code}: {error_text.decode()}")

                async for line in response.aiter_lines():
                    stop = False
                    for event in decoder.feed(line):
                        if isinstance(event, Done):
                            finish_reason_raw = event.finish_reason
                            stop = True
                            break
                        if isinstance(event, Usage):
                            usage_data = {
                                "type": "usage",
                                "tokens_used": event.tokens_used,
                                "prompt_tokens": event.prompt_tokens,
                                "completion_tokens": event.completion_tokens,
                            }
                        elif isinstance(event, TextDelta):
                            full_content_parts.append(event.text)
                            yield {"type": "token", "content": event.text}
                        elif isinstance(event, ToolCallDelta):
                            assembler.add(event)
                    if stop:
                        break

        assembled_tool_calls = assembler.assembled()

        trace_collector.record(
            provider="openai",
            model=config.model,
            request_system=system_message,
            request_messages=request.messages,
            request_params=request.sampling_params,
            request_tools=[t.get("function", {}).get("name") for t in (tools or [])] if tools else None,
            response_text="".join(full_content_parts),
            response_tool_calls=assembled_tool_calls,
            prompt_tokens=(usage_data or {}).get("prompt_tokens"),
            completion_tokens=(usage_data or {}).get("completion_tokens"),
            duration_ms=int((time.monotonic() - _trace_start) * 1000),
        )

        if assembled_tool_calls:
            yield {"type": "tool_calls", "tool_calls": assembled_tool_calls}

        result = usage_data or dict(NO_USAGE)
        result["completion"] = completion_outcome.from_openai_compat(finish_reason_raw)
        yield result
