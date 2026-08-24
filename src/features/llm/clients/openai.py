import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from src.features.llm import trace_collector
from src.features.llm.clients.base import LLMResponse
from src.features.llm.clients.tool_call_shape import normalize_tool_calls
from src.features.llm.repository import LLMConfig


class OpenAIClient:
    """Talks to any OpenAI-compatible /chat/completions endpoint.

    Covers hosted OpenAI as well as local servers that speak the same schema
    (vLLM, LM Studio, llama.cpp, ...). Bearer auth is optional so keyless local
    endpoints work unchanged.
    """

    @staticmethod
    def _auth_headers(config: LLMConfig) -> Dict[str, str]:
        """Base JSON headers plus a Bearer token when the config carries an api_key."""
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        return headers

    def _sampling_params(
        self, config: LLMConfig, options_override: Optional[Dict[str, Any]] = None
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
            "max_tokens": config.max_tokens
        }

        for key in ("top_p", "presence_penalty", "frequency_penalty"):
            if key in provider_opts:
                params[key] = provider_opts[key]

        for key in ("temperature", "max_tokens", "top_p"):
            if key in options_override:
                params[key] = options_override[key]

        return params

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
            headers = self._auth_headers(config)

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                # Build user message with optional image
                if image_data:
                    # Vision model format with image. Image before text per
                    # multimodal best practice (e.g., Gemma modality order).
                    user_content = [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        },
                        {"type": "text", "text": prompt}
                    ]
                else:
                    # Regular text-only format
                    user_content = prompt

                payload = {
                    "model": config.model,
                    "messages": [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_content}
                    ],
                    **self._sampling_params(config)
                }

                logging.info(f"[OpenAI] Sending to {config.base_url}/chat/completions, model: {config.model}, image: {bool(image_data)}")

                response = await client.post(
                    f"{config.base_url}/chat/completions",
                    json=payload,
                    headers=headers
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
            headers = self._auth_headers(config)

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                # Build messages array with system message first
                api_messages = [{"role": "system", "content": system_message}]

                for msg in messages:
                    api_messages.append({
                        "role": msg["role"],
                        "content": msg["content"]
                    })

                # Add image to the last user message if provided.
                # Image part first per multimodal best practice (Gemma et al).
                if image_data:
                    for i in range(len(api_messages) - 1, -1, -1):
                        if api_messages[i]["role"] == "user":
                            api_messages[i]["content"] = [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_data}"
                                    }
                                },
                                {"type": "text", "text": api_messages[i]["content"]}
                            ]
                            break

                sampling_params = self._sampling_params(config, options_override)
                payload = {
                    "model": config.model,
                    "messages": api_messages,
                    **sampling_params
                }

                logging.info(f"[OpenAI Chat] Sending to {config.base_url}/chat/completions, model: {config.model}, messages: {len(api_messages)}, image: {bool(image_data)}")

                _trace_start = time.monotonic()
                response = await client.post(
                    f"{config.base_url}/chat/completions",
                    json=payload,
                    headers=headers
                )

                if response.status_code != 200:
                    logging.error(response.text)

                response.raise_for_status()
                data = response.json()
                usage = data.get("usage", {})
                content = data["choices"][0]["message"]["content"]

                trace_collector.record(
                    provider="openai",
                    model=config.model,
                    request_system=system_message,
                    request_messages=api_messages,
                    request_params=sampling_params,
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
                    completion_tokens=usage.get("completion_tokens")
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
        headers = self._auth_headers(config)

        api_messages: list[Dict[str, Any]] = [{"role": "system", "content": system_message}]

        for msg in messages:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        # Image before text per multimodal best practice (Gemma modality order).
        if image_data:
            for i in range(len(api_messages) - 1, -1, -1):
                if api_messages[i]["role"] == "user":
                    api_messages[i]["content"] = [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}
                        },
                        {"type": "text", "text": api_messages[i]["content"]}
                    ]
                    break

        sampling_params = self._sampling_params(config, options_override)
        payload = {
            "model": config.model,
            "messages": api_messages,
            **sampling_params,
            "stream": True,
            "stream_options": {"include_usage": True}
        }

        logging.info(f"[OpenAI Stream] Sending streaming request to {config.base_url}/chat/completions")

        usage_data = None
        full_content_parts: List[str] = []
        _trace_start = time.monotonic()
        timeout = httpx.Timeout(connect=config.timeout, read=None, write=config.timeout, pool=config.timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{config.base_url}/chat/completions", json=payload, headers=headers) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise ValueError(f"OpenAI returned status {response.status_code}: {error_text.decode()}")

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        # Capture usage from the final chunk (OpenAI includes it when stream_options is set)
                        if data.get("usage"):
                            usage = data["usage"]
                            usage_data = {
                                "type": "usage",
                                "tokens_used": usage.get("total_tokens"),
                                "prompt_tokens": usage.get("prompt_tokens"),
                                "completion_tokens": usage.get("completion_tokens"),
                            }
                        choices = data.get("choices", [])
                        if choices:
                            content = choices[0].get("delta", {}).get("content")
                            if content:
                                full_content_parts.append(content)
                                yield {"type": "token", "content": content}
                    except json.JSONDecodeError:
                        logging.warning(f"[OpenAI Stream] Failed to parse SSE data: {data_str}")
                        continue

        trace_collector.record(
            provider="openai",
            model=config.model,
            request_system=system_message,
            request_messages=api_messages,
            request_params=sampling_params,
            response_text="".join(full_content_parts),
            prompt_tokens=(usage_data or {}).get("prompt_tokens"),
            completion_tokens=(usage_data or {}).get("completion_tokens"),
            duration_ms=int((time.monotonic() - _trace_start) * 1000),
        )

        # Yield usage data at the end
        if usage_data:
            yield usage_data
        else:
            yield {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None}

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
            headers = self._auth_headers(config)

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                # Build messages array with system message first
                api_messages: list[Dict[str, Any]] = [{"role": "system", "content": system_message}]

                for msg in messages:
                    if msg["role"] == "tool":
                        # Tool result message - preserve tool_call_id and name
                        tool_msg: Dict[str, Any] = {
                            "role": "tool",
                            "content": msg["content"],
                            "tool_call_id": msg["tool_call_id"]
                        }
                        if "name" in msg:
                            tool_msg["name"] = msg["name"]
                        api_messages.append(tool_msg)
                    elif msg["role"] == "assistant" and msg.get("tool_calls"):
                        # Assistant message with tool calls — the OpenAI wire wants
                        # string arguments (canonical in-process shape is an object).
                        assistant_msg: Dict[str, Any] = {
                            "role": "assistant",
                            "content": msg.get("content", ""),
                            "tool_calls": normalize_tool_calls(msg["tool_calls"], as_object=False)
                        }
                        api_messages.append(assistant_msg)
                    else:
                        api_messages.append({
                            "role": msg["role"],
                            "content": msg.get("content", "")
                        })

                # Add image to the last user message if provided.
                # Image part first per multimodal best practice (Gemma et al).
                if image_data:
                    for i in range(len(api_messages) - 1, -1, -1):
                        if api_messages[i]["role"] == "user":
                            api_messages[i]["content"] = [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_data}"
                                    }
                                },
                                {"type": "text", "text": api_messages[i]["content"]}
                            ]
                            break

                sampling_params = self._sampling_params(config, options_override)
                payload: Dict[str, Any] = {
                    "model": config.model,
                    "messages": api_messages,
                    **sampling_params
                }

                if tools:
                    payload["tools"] = tools

                logging.info(f"[OpenAI Tools] Sending to {config.base_url}/chat/completions, model: {config.model}, messages: {len(api_messages)}, tools: {len(tools) if tools else 0}")

                _trace_start = time.monotonic()
                response = await client.post(
                    f"{config.base_url}/chat/completions",
                    json=payload,
                    headers=headers
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
                finish_reason = choice.get("finish_reason", "stop")

                trace_collector.record(
                    provider="openai",
                    model=config.model,
                    request_system=system_message,
                    request_messages=api_messages,
                    request_params=sampling_params,
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
                    finish_reason=finish_reason
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
        may emit `delta.tool_calls` fragments (assembled here by index, per the
        OpenAI streaming contract — the first fragment per index carries `id`/
        `type`/`function.name`, later fragments only append to
        `function.arguments`) instead of, or possibly alongside, `delta.content`
        tokens. A "token" event is yielded for every content delta as it
        arrives — safe because OpenAI-compatible function-calling models don't
        mix a real content answer with tool_calls in the same turn — and a
        single "tool_calls" event is yielded at the end iff any were
        assembled, so the caller can tell a tool-invoking turn apart from a
        plain-text one without waiting for the whole thing to buffer.
        """
        headers = self._auth_headers(config)

        api_messages: list[Dict[str, Any]] = [{"role": "system", "content": system_message}]

        for msg in messages:
            if msg["role"] == "tool":
                tool_msg: Dict[str, Any] = {
                    "role": "tool",
                    "content": msg["content"],
                    "tool_call_id": msg["tool_call_id"]
                }
                if "name" in msg:
                    tool_msg["name"] = msg["name"]
                api_messages.append(tool_msg)
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                api_messages.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": normalize_tool_calls(msg["tool_calls"], as_object=False)
                })
            else:
                api_messages.append({
                    "role": msg["role"],
                    "content": msg.get("content", "")
                })

        # Image before text per multimodal best practice (Gemma modality order).
        if image_data:
            for i in range(len(api_messages) - 1, -1, -1):
                if api_messages[i]["role"] == "user":
                    api_messages[i]["content"] = [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}
                        },
                        {"type": "text", "text": api_messages[i]["content"]}
                    ]
                    break

        sampling_params = self._sampling_params(config, options_override)
        payload: Dict[str, Any] = {
            "model": config.model,
            "messages": api_messages,
            **sampling_params,
            "stream": True,
            "stream_options": {"include_usage": True}
        }
        if tools:
            payload["tools"] = tools

        logging.info(
            f"[OpenAI Tools Stream] Sending streaming request to {config.base_url}/chat/completions, "
            f"tools: {len(tools) if tools else 0}"
        )

        usage_data = None
        full_content_parts: List[str] = []
        # Assembled by delta index — see the OpenAI streaming tool_calls contract
        # note on the docstring above.
        tool_call_acc: Dict[int, Dict[str, Any]] = {}
        _trace_start = time.monotonic()
        timeout = httpx.Timeout(connect=config.timeout, read=None, write=config.timeout, pool=config.timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{config.base_url}/chat/completions", json=payload, headers=headers) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise ValueError(f"OpenAI returned status {response.status_code}: {error_text.decode()}")

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        if data.get("usage"):
                            usage = data["usage"]
                            usage_data = {
                                "type": "usage",
                                "tokens_used": usage.get("total_tokens"),
                                "prompt_tokens": usage.get("prompt_tokens"),
                                "completion_tokens": usage.get("completion_tokens"),
                            }
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content")
                            if content:
                                full_content_parts.append(content)
                                yield {"type": "token", "content": content}
                            for tc_delta in delta.get("tool_calls") or []:
                                idx = tc_delta.get("index", 0)
                                entry = tool_call_acc.setdefault(
                                    idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                                )
                                if tc_delta.get("id"):
                                    entry["id"] = tc_delta["id"]
                                if tc_delta.get("type"):
                                    entry["type"] = tc_delta["type"]
                                fn_delta = tc_delta.get("function") or {}
                                if fn_delta.get("name"):
                                    entry["function"]["name"] += fn_delta["name"]
                                if fn_delta.get("arguments"):
                                    entry["function"]["arguments"] += fn_delta["arguments"]
                    except json.JSONDecodeError:
                        logging.warning(f"[OpenAI Tools Stream] Failed to parse SSE data: {data_str}")
                        continue

        assembled_tool_calls = [tool_call_acc[i] for i in sorted(tool_call_acc)] if tool_call_acc else None

        trace_collector.record(
            provider="openai",
            model=config.model,
            request_system=system_message,
            request_messages=api_messages,
            request_params=sampling_params,
            request_tools=[t.get("function", {}).get("name") for t in (tools or [])] if tools else None,
            response_text="".join(full_content_parts),
            response_tool_calls=assembled_tool_calls,
            prompt_tokens=(usage_data or {}).get("prompt_tokens"),
            completion_tokens=(usage_data or {}).get("completion_tokens"),
            duration_ms=int((time.monotonic() - _trace_start) * 1000),
        )

        if assembled_tool_calls:
            yield {"type": "tool_calls", "tool_calls": assembled_tool_calls}

        if usage_data:
            yield usage_data
        else:
            yield {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None}
