import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from src.features.llm import trace_collector
from src.features.llm.clients.base import LLMResponse
from src.features.llm.clients.tool_call_shape import arguments_to_object, normalize_tool_calls
from src.features.llm.repository import LLMConfig
from src.features.llm.ttl_cache import TTLCache

# force_prompt_tools mode re-renders the same 3-8k tokens of tool-text on
# every single LLM call (once per tool-loop iteration); the rendering is a
# pure function of the tool schema set, so cache it briefly instead. TTL
# (not "forever") because the registry can change under a plugin toggle.
_PROMPT_TOOLS_TEXT_CACHE_TTL_SECONDS = 60.0
_prompt_tools_text_cache: TTLCache[tuple, str] = TTLCache(_PROMPT_TOOLS_TEXT_CACHE_TTL_SECONDS)

# Sampling knobs Ollama accepts inside the request "options" object. Values are
# copied straight from a config's provider_options when present.
OLLAMA_OPTION_KEYS = [
    "num_ctx",      # Context window size
    "num_gpu",      # Number of GPU layers
    "num_thread",   # Number of CPU threads
    "num_batch",    # Batch size for prompt processing
    "num_keep",     # Number of tokens to keep from initial prompt
    "seed",         # Random seed for reproducibility
    "top_k",        # Top-k sampling
    "top_p",        # Nucleus sampling
    "min_p",        # Min-p sampling
    "tfs_z",        # Tail-free sampling
    "typical_p",    # Typical p sampling
    "repeat_penalty",    # Repetition penalty
    "repeat_last_n",     # Tokens to look back for repeat penalty
    "presence_penalty",  # Presence penalty
    "frequency_penalty", # Frequency penalty
    "mirostat",     # Mirostat sampling mode
    "mirostat_tau", # Mirostat target entropy
    "mirostat_eta", # Mirostat learning rate
    "stop",         # Stop sequences
]


class OllamaClient:
    """Talks to an Ollama server over its /api/generate and /api/chat endpoints.

    Ollama has no native tool-calling schema for every model, so tools are also
    supported by rendering them into the system prompt as ``<tool_call>`` XML
    instructions (``force_prompt_tools``); the executor parses that dialect.
    """

    @staticmethod
    def _build_prompt_tools_text(tools: List[Dict]) -> str:
        """Build a text description of tools for injection into the system prompt.

        Used when native tool calling doesn't work (e.g. Gemma 4 abliterated models).
        The model is instructed to emit <tool_call> XML blocks which the executor
        already knows how to parse.

        Cached (see ``_prompt_tools_text_cache``) since the same tool set is
        re-rendered on every tool-loop iteration in force_prompt_tools mode.
        """
        cache_key = tuple(
            (t.get("function", {}).get("name", ""), t.get("function", {}).get("description", ""))
            for t in tools
        )
        cached = _prompt_tools_text_cache.get(cache_key)
        if cached is not None:
            return cached
        text = OllamaClient._render_prompt_tools_text(tools)
        _prompt_tools_text_cache.set(cache_key, text)
        return text

    @staticmethod
    def _render_prompt_tools_text(tools: List[Dict]) -> str:
        """Actually render the tool-text block (the uncached half of ``_build_prompt_tools_text``)."""
        lines = [
            "\n\n## Available Tools\n",
            "You have the following tools available. To call a tool, output a "
            "`<tool_call>` XML block with a JSON body containing `name` and `arguments`.\n",
            "Example:",
            '<tool_call>{"name": "tool_name", "arguments": {"arg1": "value1"}}</tool_call>\n',
            "You may call multiple tools in a single response. "
            "After each tool call you will receive the result in the next message. "
            "When you have enough information, respond normally without any tool_call blocks.\n",
            "### Tools\n",
        ]
        for tool_def in tools:
            func = tool_def.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            params = func.get("parameters", {})
            props = params.get("properties", {})
            required = params.get("required", [])

            lines.append(f"**{name}**: {desc}")
            if props:
                param_parts = []
                for pname, pschema in props.items():
                    req_mark = " (required)" if pname in required else ""
                    ptype = pschema.get("type", "any")
                    pdesc = pschema.get("description", "")
                    param_parts.append(f"  - `{pname}` ({ptype}{req_mark}): {pdesc}")
                lines.append("  Parameters:")
                lines.extend(param_parts)
            else:
                lines.append("  Parameters: none")
            lines.append("")

        return "\n".join(lines)

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
            # Get provider-specific options with defaults
            provider_opts = config.provider_options or {}

            # Build options dict with defaults, allowing provider_options to override
            options = {
                "temperature": config.temperature,
                "num_predict": config.max_tokens
            }

            # Root-level Ollama parameters (not in options)
            # Note: explicitly check for None since user might have null in config
            think_enabled = provider_opts.get("think")
            if think_enabled is None:
                think_enabled = True  # Default to enabled if not set or null

            for key in OLLAMA_OPTION_KEYS:
                if key in provider_opts:
                    options[key] = provider_opts[key]

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                keep_alive = provider_opts.get("keep_alive", 0)

                # Use /api/chat for vision models (with images), /api/generate for text-only
                if image_data:
                    # Vision model - use chat endpoint with messages format
                    # Note: Use 'system' field instead of system message in messages array
                    # for better compatibility with vision models (matches OpenWebUI behavior)
                    payload = {
                        "model": config.model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt,
                                "images": [image_data]
                            }
                        ],
                        "system": system_message,
                        "stream": False,
                        "keep_alive": keep_alive,
                        "think": think_enabled,
                        "options": options
                    }
                    endpoint = "/api/chat"
                    logging.info(f"Ollama chat payload (vision mode, image_length: {len(image_data)}, keep_alive: {keep_alive}, think: {think_enabled})")
                else:
                    # Text-only - use generate endpoint
                    payload = {
                        "model": config.model,
                        "system": system_message,
                        "prompt": prompt,
                        "stream": False,
                        "keep_alive": keep_alive,
                        "think": think_enabled,
                        "options": options
                    }
                    endpoint = "/api/generate"
                    logging.info(f"Ollama generate payload (text-only, keep_alive: {keep_alive}, think: {think_enabled})")

                response = await client.post(
                    f"{config.base_url}{endpoint}",
                    json=payload
                )

                if response.status_code != 200:
                    error_text = response.text
                    logging.error(f"Ollama error response: {error_text}")
                    raise ValueError(f"Ollama returned status {response.status_code}: {error_text}")

                response.raise_for_status()

                data = response.json()

                # Extract content based on endpoint used
                if image_data:
                    # Chat endpoint returns message.content
                    message = data.get("message", {})
                    content = message.get("content", "")

                    # Fallback: if content is empty but thinking exists, use thinking content
                    if not content and message.get("thinking"):
                        logging.warning(f"Model returned empty content but has thinking field. "
                                       f"Using thinking content as fallback. Consider setting 'think: false' in provider_options.")
                        content = message.get("thinking", "")
                else:
                    # Generate endpoint returns response
                    content = data.get("response", "")

                    # Fallback for generate endpoint too
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
            provider_opts = config.provider_options or {}
            options_override = options_override or {}

            options = {
                "temperature": config.temperature,
                "num_predict": config.max_tokens
            }

            think_enabled = provider_opts.get("think")
            if think_enabled is None:
                think_enabled = True

            for key in OLLAMA_OPTION_KEYS:
                if key in provider_opts:
                    options[key] = provider_opts[key]

            if "temperature" in options_override:
                options["temperature"] = options_override["temperature"]
            if "top_p" in options_override:
                options["top_p"] = options_override["top_p"]
            if "top_k" in options_override:
                options["top_k"] = options_override["top_k"]
            if "max_tokens" in options_override:
                options["num_predict"] = options_override["max_tokens"]
            if "think" in options_override:
                think_enabled = options_override["think"]

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                keep_alive = provider_opts.get("keep_alive", 0)

                # Convert messages to Ollama format, with system message as first message
                ollama_messages = []

                # Add system message as first message in the array for better model compatibility
                if system_message:
                    ollama_messages.append({
                        "role": "system",
                        "content": system_message
                    })

                for msg in messages:
                    ollama_msg = {
                        "role": msg["role"],
                        "content": msg["content"]
                    }
                    ollama_messages.append(ollama_msg)

                # Add image to the last user message if provided
                if image_data and ollama_messages:
                    for i in range(len(ollama_messages) - 1, -1, -1):
                        if ollama_messages[i]["role"] == "user":
                            ollama_messages[i]["images"] = [image_data]
                            break

                payload = {
                    "model": config.model,
                    "messages": ollama_messages,
                    "stream": False,
                    "keep_alive": keep_alive,
                    "think": think_enabled,
                    "options": options
                }

                logging.debug(f"[Ollama Chat] Sending request to {config.base_url}/api/chat")
                logging.debug(f"[Ollama Chat] Model: {config.model}")
                logging.debug(f"[Ollama Chat] System message ({len(system_message)} chars): {system_message[:200]}{'...' if len(system_message) > 200 else ''}")
                logging.debug(f"[Ollama Chat] Messages count: {len(ollama_messages)}")
                for i, msg in enumerate(ollama_messages):
                    content_preview = msg['content'][:100] + '...' if len(msg['content']) > 100 else msg['content']
                    logging.debug(f"[Ollama Chat] Message {i}: role={msg['role']}, content={content_preview}")
                logging.debug(f"[Ollama Chat] Options: think={think_enabled}, keep_alive={keep_alive}")

                _trace_start = time.monotonic()
                response = await client.post(
                    f"{config.base_url}/api/chat",
                    json=payload
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
                    request_messages=ollama_messages,
                    request_params=options,
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
        provider_opts = config.provider_options or {}
        options_override = options_override or {}

        options = {
            "temperature": config.temperature,
            "num_predict": config.max_tokens
        }

        think_enabled = provider_opts.get("think")
        if think_enabled is None:
            think_enabled = True

        for key in OLLAMA_OPTION_KEYS:
            if key in provider_opts:
                options[key] = provider_opts[key]

        if "temperature" in options_override:
            options["temperature"] = options_override["temperature"]
        if "top_p" in options_override:
            options["top_p"] = options_override["top_p"]
        if "top_k" in options_override:
            options["top_k"] = options_override["top_k"]
        if "max_tokens" in options_override:
            options["num_predict"] = options_override["max_tokens"]
        if "think" in options_override:
            think_enabled = options_override["think"]

        keep_alive = provider_opts.get("keep_alive", 0)

        ollama_messages = []
        if system_message:
            ollama_messages.append({"role": "system", "content": system_message})

        for msg in messages:
            ollama_msg = {"role": msg["role"], "content": msg["content"]}
            ollama_messages.append(ollama_msg)

        if image_data and ollama_messages:
            for i in range(len(ollama_messages) - 1, -1, -1):
                if ollama_messages[i]["role"] == "user":
                    ollama_messages[i]["images"] = [image_data]
                    break

        payload = {
            "model": config.model,
            "messages": ollama_messages,
            "stream": True,
            "keep_alive": keep_alive,
            "think": think_enabled,
            "options": options
        }

        logging.info(f"[Ollama Stream] Sending streaming request to {config.base_url}/api/chat")

        full_content_parts: List[str] = []
        eval_count = None
        prompt_eval_count = None
        _trace_start = time.monotonic()
        timeout = httpx.Timeout(connect=config.timeout, read=None, write=config.timeout, pool=config.timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{config.base_url}/api/chat", json=payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise ValueError(f"Ollama returned status {response.status_code}: {error_text.decode()}")

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        message = data.get("message", {})
                        content = message.get("content", "")
                        if content:
                            full_content_parts.append(content)
                            yield {"type": "token", "content": content}
                        if data.get("done", False):
                            # Ollama includes token counts in the final chunk
                            eval_count = data.get("eval_count")
                            prompt_eval_count = data.get("prompt_eval_count")
                            tokens_used = None
                            if eval_count is not None or prompt_eval_count is not None:
                                tokens_used = (eval_count or 0) + (prompt_eval_count or 0)
                            yield {
                                "type": "usage",
                                "tokens_used": tokens_used,
                                "prompt_tokens": prompt_eval_count,
                                "completion_tokens": eval_count,
                            }
                            break
                    except json.JSONDecodeError:
                        logging.warning(f"[Ollama Stream] Failed to parse line: {line}")
                        continue

        trace_collector.record(
            provider="ollama",
            model=config.model,
            request_system=system_message,
            request_messages=ollama_messages,
            request_params=options,
            response_text="".join(full_content_parts),
            prompt_tokens=prompt_eval_count,
            completion_tokens=eval_count,
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
            provider_opts = config.provider_options or {}
            options_override = options_override or {}
            force_prompt_tools = bool(provider_opts.get("force_prompt_tools", False))

            options = {
                "temperature": config.temperature,
                "num_predict": config.max_tokens
            }

            think_enabled = provider_opts.get("think")
            if think_enabled is None:
                think_enabled = True

            for key in OLLAMA_OPTION_KEYS:
                if key in provider_opts:
                    options[key] = provider_opts[key]

            if "temperature" in options_override:
                options["temperature"] = options_override["temperature"]
            if "top_p" in options_override:
                options["top_p"] = options_override["top_p"]
            if "top_k" in options_override:
                options["top_k"] = options_override["top_k"]
            if "max_tokens" in options_override:
                options["num_predict"] = options_override["max_tokens"]
            if "think" in options_override:
                think_enabled = options_override["think"]

            # When force_prompt_tools is enabled, inject tool definitions into the
            # system prompt as text and skip native tool calling.  The model will
            # emit <tool_call> XML blocks that executor.py already parses.
            use_native_tools = tools and not force_prompt_tools
            if force_prompt_tools and tools:
                prompt_tools_text = self._build_prompt_tools_text(tools)
                system_message = (system_message or "") + prompt_tools_text
                logging.info(f"[Ollama Tools] force_prompt_tools enabled — injected {len(tools)} tool(s) into system prompt")

            async with httpx.AsyncClient(timeout=config.timeout) as client:
                keep_alive = provider_opts.get("keep_alive", 0)

                # Convert messages to Ollama format, with system message as first message
                ollama_messages = []

                if system_message:
                    ollama_messages.append({
                        "role": "system",
                        "content": system_message
                    })

                for msg in messages:
                    if msg["role"] == "tool":
                        if force_prompt_tools:
                            # In prompt-tools mode Ollama doesn't know about tool
                            # roles, so convert to a user message with clear label.
                            tool_name = msg.get("name", "tool")
                            ollama_messages.append({
                                "role": "user",
                                "content": f"[Tool Result: {tool_name}]\n{msg['content']}"
                            })
                        else:
                            # Tool result message - preserve tool_call_id
                            ollama_msg: Dict[str, Any] = {
                                "role": "tool",
                                "content": msg["content"]
                            }
                            if "tool_call_id" in msg:
                                ollama_msg["tool_call_id"] = msg["tool_call_id"]
                            ollama_messages.append(ollama_msg)
                    elif msg["role"] == "assistant" and msg.get("tool_calls"):
                        if force_prompt_tools:
                            # Reconstruct the assistant's tool calls as XML text
                            # so the conversation history stays coherent.
                            content_parts = []
                            if msg.get("content"):
                                content_parts.append(msg["content"])
                            for tc in msg["tool_calls"]:
                                fn = tc.get("function", {})
                                call_json = json.dumps({
                                    "name": fn.get("name", ""),
                                    "arguments": arguments_to_object(fn.get("arguments")),
                                })
                                content_parts.append(f"<tool_call>{call_json}</tool_call>")
                            ollama_messages.append({
                                "role": "assistant",
                                "content": "\n".join(content_parts)
                            })
                        else:
                            # Assistant message with tool calls — Ollama's native
                            # API needs object arguments (a string 400s).
                            ollama_msg = {
                                "role": "assistant",
                                "content": msg.get("content", ""),
                                "tool_calls": normalize_tool_calls(msg["tool_calls"], as_object=True)
                            }
                            ollama_messages.append(ollama_msg)
                    else:
                        ollama_msg = {
                            "role": msg["role"],
                            "content": msg.get("content", "")
                        }
                        ollama_messages.append(ollama_msg)

                # Add image to the last user message if provided
                if image_data and ollama_messages:
                    for i in range(len(ollama_messages) - 1, -1, -1):
                        if ollama_messages[i]["role"] == "user":
                            ollama_messages[i]["images"] = [image_data]
                            break

                # Disable thinking when tools are present — thinking mode causes
                # models to reason about tools in the think phase and then output
                # a text description ("Let me check...") instead of emitting a
                # structured tool_call.
                use_think = think_enabled if not use_native_tools else False

                payload: Dict[str, Any] = {
                    "model": config.model,
                    "messages": ollama_messages,
                    "stream": False,
                    "keep_alive": keep_alive,
                    "think": use_think,
                    "options": options
                }

                if use_native_tools:
                    payload["tools"] = tools

                tool_mode = "prompt-injected" if force_prompt_tools else "native"
                logging.info(f"[Ollama Tools] Sending request to {config.base_url}/api/chat")
                logging.info(f"[Ollama Tools] Model: {config.model}, tools: {len(tools) if tools else 0} ({tool_mode}), think: {use_think}")

                # Retry on empty response — some models non-deterministically
                # return empty content with high temperature.
                max_retries = 3
                data = None
                _trace_start = time.monotonic()
                for attempt in range(max_retries):
                    response = await client.post(
                        f"{config.base_url}/api/chat",
                        json=payload
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
                    request_system=system_message,
                    request_messages=ollama_messages,
                    request_params=options,
                    request_tools=[t.get("function", {}).get("name") for t in (tools or [])] if use_native_tools else None,
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
        config; see ToolExecutor). Unlike OpenAI, Ollama does not fragment
        tool_calls across chunks: a chunk's `message.tool_calls`, when
        present, is already the complete list, so it's forwarded as-is rather
        than assembled incrementally.
        """
        provider_opts = config.provider_options or {}
        options_override = options_override or {}

        options = {
            "temperature": config.temperature,
            "num_predict": config.max_tokens
        }

        think_enabled = provider_opts.get("think")
        if think_enabled is None:
            think_enabled = True

        for key in OLLAMA_OPTION_KEYS:
            if key in provider_opts:
                options[key] = provider_opts[key]

        if "temperature" in options_override:
            options["temperature"] = options_override["temperature"]
        if "top_p" in options_override:
            options["top_p"] = options_override["top_p"]
        if "top_k" in options_override:
            options["top_k"] = options_override["top_k"]
        if "max_tokens" in options_override:
            options["num_predict"] = options_override["max_tokens"]
        if "think" in options_override:
            think_enabled = options_override["think"]

        keep_alive = provider_opts.get("keep_alive", 0)

        ollama_messages = []
        if system_message:
            ollama_messages.append({"role": "system", "content": system_message})

        for msg in messages:
            if msg["role"] == "tool":
                ollama_msg: Dict[str, Any] = {
                    "role": "tool",
                    "content": msg["content"]
                }
                if "tool_call_id" in msg:
                    ollama_msg["tool_call_id"] = msg["tool_call_id"]
                ollama_messages.append(ollama_msg)
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                ollama_messages.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": normalize_tool_calls(msg["tool_calls"], as_object=True)
                })
            else:
                ollama_messages.append({
                    "role": msg["role"],
                    "content": msg.get("content", "")
                })

        if image_data and ollama_messages:
            for i in range(len(ollama_messages) - 1, -1, -1):
                if ollama_messages[i]["role"] == "user":
                    ollama_messages[i]["images"] = [image_data]
                    break

        # Disable thinking when tools are present — see the matching comment
        # in generate_with_tools: thinking mode causes models to reason about
        # tools in <think> and then describe the call in prose instead of
        # emitting a structured tool_call.
        use_think = think_enabled if not tools else False

        payload: Dict[str, Any] = {
            "model": config.model,
            "messages": ollama_messages,
            "stream": True,
            "keep_alive": keep_alive,
            "think": use_think,
            "options": options
        }
        if tools:
            payload["tools"] = tools

        logging.info(
            f"[Ollama Tools Stream] Sending streaming request to {config.base_url}/api/chat, "
            f"tools: {len(tools) if tools else 0}"
        )

        full_content_parts: List[str] = []
        tool_calls: Optional[List[Dict[str, Any]]] = None
        eval_count = None
        prompt_eval_count = None
        _trace_start = time.monotonic()
        timeout = httpx.Timeout(connect=config.timeout, read=None, write=config.timeout, pool=config.timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{config.base_url}/api/chat", json=payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    raise ValueError(f"Ollama returned status {response.status_code}: {error_text.decode()}")

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        message = data.get("message", {})
                        content = message.get("content", "")
                        if content:
                            full_content_parts.append(content)
                            yield {"type": "token", "content": content}
                        if message.get("tool_calls"):
                            tool_calls = message["tool_calls"]
                        if data.get("done", False):
                            eval_count = data.get("eval_count")
                            prompt_eval_count = data.get("prompt_eval_count")
                            tokens_used = None
                            if eval_count is not None or prompt_eval_count is not None:
                                tokens_used = (eval_count or 0) + (prompt_eval_count or 0)
                            if tool_calls:
                                yield {"type": "tool_calls", "tool_calls": tool_calls}
                            yield {
                                "type": "usage",
                                "tokens_used": tokens_used,
                                "prompt_tokens": prompt_eval_count,
                                "completion_tokens": eval_count,
                            }
                            break
                    except json.JSONDecodeError:
                        logging.warning(f"[Ollama Tools Stream] Failed to parse line: {line}")
                        continue

        trace_collector.record(
            provider="ollama",
            model=config.model,
            request_system=system_message,
            request_messages=ollama_messages,
            request_params=options,
            request_tools=[t.get("function", {}).get("name") for t in (tools or [])] if tools else None,
            response_text="".join(full_content_parts),
            response_tool_calls=tool_calls,
            prompt_tokens=prompt_eval_count,
            completion_tokens=eval_count,
            duration_ms=int((time.monotonic() - _trace_start) * 1000),
        )
