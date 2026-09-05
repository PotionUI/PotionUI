import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from src.features.llm import trace_collector
from src.features.llm.clients.base import LLMResponse
from src.features.llm.clients.ollama_wire import (
    TOOLS_AUTO,
    TOOLS_NATIVE,
    OllamaNDJSONDecoder,
    build_ollama_chat_request,
    build_ollama_generate_request,
)
from src.features.llm.clients.wire_events import Done, TextDelta, ToolCalls, Usage
from src.features.llm.repository import LLMConfig


class OllamaClient:
    """Talks to an Ollama server over its /api/generate and /api/chat endpoints.

    Ollama has no native tool-calling schema for every model, so tools are also
    supported by rendering them into the system prompt as ``<tool_call>`` XML
    instructions (``force_prompt_tools``); the executor parses that dialect.
    Payload building and chunk parsing live in ``ollama_wire``; what stays here
    is the HTTP call and the response shaping.
    """

    @staticmethod
    def _stream_timeout(config: LLMConfig) -> httpx.Timeout:
        return httpx.Timeout(
            connect=config.timeout, read=None, write=config.timeout, pool=config.timeout
        )

    @staticmethod
    def _usage_event(event: Usage) -> Dict[str, Any]:
        return {
            "type": "usage",
            "tokens_used": event.tokens_used,
            "prompt_tokens": event.prompt_tokens,
            "completion_tokens": event.completion_tokens,
        }

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
            image_data: Optional base64 encoded image for vision models (LLaVA, etc.)
        """
        try:
            request = build_ollama_generate_request(config, prompt, system_message, image_data)

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                keep_alive = request.payload["keep_alive"]
                think_enabled = request.payload["think"]
                if image_data:
                    logging.info(f"Ollama chat payload (vision mode, image_length: {len(image_data)}, keep_alive: {keep_alive}, think: {think_enabled})")
                else:
                    logging.info(f"Ollama generate payload (text-only, keep_alive: {keep_alive}, think: {think_enabled})")

                response = await client.post(
                    f"{config.base_url}{request.endpoint}",
                    json=request.payload
                )

                if response.status_code != 200:
                    error_text = response.text
                    logging.error(f"Ollama error response: {error_text}")
                    raise ValueError(f"Ollama returned status {response.status_code}: {error_text}")

                response.raise_for_status()

                data = response.json()

                # /api/chat answers under message.content, /api/generate under response.
                # Either can come back empty with the reasoning in `thinking` instead.
                if image_data:
                    message = data.get("message", {})
                    content = message.get("content", "")
                    if not content and message.get("thinking"):
                        logging.warning(f"Model returned empty content but has thinking field. "
                                       f"Using thinking content as fallback. Consider setting 'think: false' in provider_options.")
                        content = message.get("thinking", "")
                else:
                    content = data.get("response", "")
                    if not content and data.get("thinking"):
                        logging.warning(f"Model returned empty response but has thinking field. Using thinking as fallback.")
                        content = data.get("thinking", "")

                return LLMResponse(
                    content=content,
                    model=config.model,
                    provider_id=config.id,
                    tokens_used=data.get("eval_count"),
                    prompt_tokens=data.get("prompt_eval_count"),
                    completion_tokens=data.get("eval_count")
                )
        except httpx.HTTPStatusError as e:
            logging.error(f"Ollama HTTP error: {e.response.text if e.response else str(e)}")
            raise ValueError(f"Error generating Ollama response: {e.response.text if e.response else str(e)}")
        except Exception as e:
            logging.error(f"Ollama exception: {str(e)}")
            raise ValueError(f"Error generating Ollama response: {str(e)}")

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
            request = build_ollama_chat_request(
                config, messages, system_message, image_data, options_override
            )

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                logging.debug(f"[Ollama Chat] Sending request to {config.base_url}/api/chat")
                logging.debug(f"[Ollama Chat] Model: {config.model}")
                logging.debug(f"[Ollama Chat] System message ({len(system_message)} chars): {system_message[:200]}{'...' if len(system_message) > 200 else ''}")
                logging.debug(f"[Ollama Chat] Messages count: {len(request.messages)}")
                for i, msg in enumerate(request.messages):
                    content_preview = msg['content'][:100] + '...' if len(msg['content']) > 100 else msg['content']
                    logging.debug(f"[Ollama Chat] Message {i}: role={msg['role']}, content={content_preview}")
                logging.debug(f"[Ollama Chat] Options: think={request.payload['think']}, keep_alive={request.payload['keep_alive']}")

                _trace_start = time.monotonic()
                response = await client.post(
                    f"{config.base_url}{request.endpoint}",
                    json=request.payload
                )

                if response.status_code != 200:
                    error_text = response.text
                    logging.error(f"Ollama error response: {error_text}")
                    raise ValueError(f"Ollama returned status {response.status_code}: {error_text}")

                response.raise_for_status()
                data = response.json()

                message = data.get("message", {})
                content = message.get("content", "")

                if not content and message.get("thinking"):
                    logging.warning("Model returned empty content but has thinking field.")
                    content = message.get("thinking", "")

                trace_collector.record(
                    provider="ollama",
                    model=config.model,
                    request_system=system_message,
                    request_messages=request.messages,
                    request_params=request.options,
                    response_text=content,
                    prompt_tokens=data.get("prompt_eval_count"),
                    completion_tokens=data.get("eval_count"),
                    duration_ms=int((time.monotonic() - _trace_start) * 1000),
                )

                return LLMResponse(
                    content=content,
                    model=config.model,
                    provider_id=config.id,
                    tokens_used=data.get("eval_count"),
                    prompt_tokens=data.get("prompt_eval_count"),
                    completion_tokens=data.get("eval_count")
                )
        except httpx.HTTPStatusError as e:
            logging.error(f"Ollama HTTP error: {e.response.text if e.response else str(e)}")
            raise ValueError(f"Error generating Ollama response: {e.response.text if e.response else str(e)}")
        except Exception as e:
            logging.error(f"Ollama exception: {str(e)}")
            raise ValueError(f"Error generating Ollama response: {str(e)}")

    async def stream_with_history(
        self,
        messages: list[Dict[str, str]],
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[dict, None]:
        """Stream a response over a full conversation history."""
        request = build_ollama_chat_request(
            config, messages, system_message, image_data, options_override, stream=True
        )

        logging.info(f"[Ollama Stream] Sending streaming request to {config.base_url}/api/chat")

        decoder = OllamaNDJSONDecoder("Ollama Stream")
        full_content_parts: List[str] = []
        usage_event: Optional[Dict[str, Any]] = None
        _trace_start = time.monotonic()
        async with httpx.AsyncClient(timeout=self._stream_timeout(config)) as client:
            async with client.stream("POST", f"{config.base_url}{request.endpoint}", json=request.payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise ValueError(f"Ollama returned status {response.status_code}: {error_text.decode()}")

                async for line in response.aiter_lines():
                    stop = False
                    for event in decoder.feed(line):
                        if isinstance(event, TextDelta):
                            full_content_parts.append(event.text)
                            yield {"type": "token", "content": event.text}
                        elif isinstance(event, Usage):
                            usage_event = self._usage_event(event)
                        elif isinstance(event, Done):
                            yield usage_event
                            stop = True
                            break
                    if stop:
                        break

        trace_collector.record(
            provider="ollama",
            model=config.model,
            request_system=system_message,
            request_messages=request.messages,
            request_params=request.options,
            response_text="".join(full_content_parts),
            prompt_tokens=(usage_event or {}).get("prompt_tokens"),
            completion_tokens=(usage_event or {}).get("completion_tokens"),
            duration_ms=int((time.monotonic() - _trace_start) * 1000),
        )

    async def generate_with_tools(
        self,
        messages: list[Dict[str, Any]],
        config: LLMConfig,
        system_message: str,
        tools: List[Dict] = None,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Generate a response with native (or prompt-injected) tool calling."""
        try:
            request = build_ollama_chat_request(
                config, messages, system_message, image_data, options_override, tools,
                tool_mode=TOOLS_AUTO,
            )

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                tool_mode = "native" if request.native_tools else "prompt-injected"
                logging.info(f"[Ollama Tools] Sending request to {config.base_url}/api/chat")
                logging.info(f"[Ollama Tools] Model: {config.model}, tools: {len(tools) if tools else 0} ({tool_mode}), think: {request.payload['think']}")

                # Retry on empty response — some models non-deterministically
                # return empty content with high temperature.
                max_retries = 3
                data = None
                _trace_start = time.monotonic()
                for attempt in range(max_retries):
                    response = await client.post(
                        f"{config.base_url}{request.endpoint}",
                        json=request.payload
                    )

                    if response.status_code != 200:
                        error_text = response.text
                        logging.error(f"Ollama error response: {error_text}")
                        raise ValueError(f"Ollama returned status {response.status_code}: {error_text}")

                    response.raise_for_status()
                    data = response.json()

                    message = data.get("message", {})
                    content = message.get("content", "")
                    tool_calls_check = message.get("tool_calls") or None

                    if content or tool_calls_check:
                        if attempt > 0:
                            logging.info(f"[Ollama Tools] Succeeded on retry {attempt + 1}/{max_retries}")
                        break

                    logging.warning(f"[Ollama Tools] Empty response (attempt {attempt + 1}/{max_retries}), done_reason: {data.get('done_reason')}")
                    if attempt < max_retries - 1:
                        logging.info(f"[Ollama Tools] Retrying...")

                message = data.get("message", {})
                content = message.get("content", "")
                tool_calls = message.get("tool_calls") or None

                logging.info(f"[Ollama Tools] Response: tool_calls={bool(tool_calls)}, content_len={len(content)}, has_thinking={bool(message.get('thinking'))}")
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        logging.info(f"[Ollama Tools] Tool call: {fn.get('name')}({fn.get('arguments', {})})")
                elif content:
                    logging.info(f"[Ollama Tools] Content preview: {content[:200]}")

                if not content and message.get("thinking"):
                    logging.warning("Model returned empty content but has thinking field.")
                    content = message.get("thinking", "")

                finish_reason = "tool_calls" if tool_calls else "stop"

                trace_collector.record(
                    provider="ollama",
                    model=config.model,
                    request_system=request.system_message,
                    request_messages=request.messages,
                    request_params=request.options,
                    request_tools=[t.get("function", {}).get("name") for t in (tools or [])] if request.native_tools else None,
                    response_text=content,
                    response_tool_calls=tool_calls,
                    prompt_tokens=data.get("prompt_eval_count"),
                    completion_tokens=data.get("eval_count"),
                    duration_ms=int((time.monotonic() - _trace_start) * 1000),
                )

                return LLMResponse(
                    content=content,
                    model=config.model,
                    provider_id=config.id,
                    tokens_used=data.get("eval_count"),
                    prompt_tokens=data.get("prompt_eval_count"),
                    completion_tokens=data.get("eval_count"),
                    tool_calls=tool_calls,
                    finish_reason=finish_reason
                )
        except httpx.HTTPStatusError as e:
            logging.error(f"Ollama HTTP error: {e.response.text if e.response else str(e)}")
            raise ValueError(f"Error generating Ollama response: {e.response.text if e.response else str(e)}")
        except Exception as e:
            logging.error(f"Ollama exception: {str(e)}")
            raise ValueError(f"Error generating Ollama response: {str(e)}")

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

        Native tool calling only (``force_prompt_tools`` is not supported
        here — the caller must not pass `tools` for a force_prompt_tools
        config; see ToolExecutor).
        """
        request = build_ollama_chat_request(
            config, messages, system_message, image_data, options_override, tools,
            tool_mode=TOOLS_NATIVE, stream=True,
        )

        logging.info(
            f"[Ollama Tools Stream] Sending streaming request to {config.base_url}/api/chat, "
            f"tools: {len(tools) if tools else 0}"
        )

        decoder = OllamaNDJSONDecoder("Ollama Tools Stream")
        full_content_parts: List[str] = []
        tool_calls: Optional[List[Dict[str, Any]]] = None
        usage_event: Optional[Dict[str, Any]] = None
        _trace_start = time.monotonic()
        async with httpx.AsyncClient(timeout=self._stream_timeout(config)) as client:
            async with client.stream("POST", f"{config.base_url}{request.endpoint}", json=request.payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise ValueError(f"Ollama returned status {response.status_code}: {error_text.decode()}")

                async for line in response.aiter_lines():
                    stop = False
                    for event in decoder.feed(line):
                        if isinstance(event, TextDelta):
                            full_content_parts.append(event.text)
                            yield {"type": "token", "content": event.text}
                        elif isinstance(event, ToolCalls):
                            tool_calls = event.tool_calls
                        elif isinstance(event, Usage):
                            usage_event = self._usage_event(event)
                        elif isinstance(event, Done):
                            if tool_calls:
                                yield {"type": "tool_calls", "tool_calls": tool_calls}
                            yield usage_event
                            stop = True
                            break
                    if stop:
                        break

        trace_collector.record(
            provider="ollama",
            model=config.model,
            request_system=system_message,
            request_messages=request.messages,
            request_params=request.options,
            request_tools=[t.get("function", {}).get("name") for t in (tools or [])] if tools else None,
            response_text="".join(full_content_parts),
            response_tool_calls=tool_calls,
            prompt_tokens=(usage_event or {}).get("prompt_tokens"),
            completion_tokens=(usage_event or {}).get("completion_tokens"),
            duration_ms=int((time.monotonic() - _trace_start) * 1000),
        )
