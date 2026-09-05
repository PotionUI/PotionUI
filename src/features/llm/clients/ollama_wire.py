"""Request building and NDJSON decoding for an Ollama server.

Both the buffered and the streaming client methods share one builder, so a
payload cannot drift between them, and one decoder, so a chunk parses the same
way in both. Ollama's request shape is easy to break silently — sampling knobs
live inside a nested ``options`` object while ``think`` and ``keep_alive`` sit at
the root — which is why all of it is assembled in one place.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

from src.features.llm.clients.tool_call_shape import arguments_to_object, normalize_tool_calls
from src.features.llm.clients.wire_events import Done, TextDelta, ToolCalls, Usage
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

# How a call treats tools. "none" is a plain history turn; "native" offers them
# over Ollama's tool schema; "auto" additionally honours a config's
# force_prompt_tools, which the streaming path deliberately does not support.
TOOLS_NONE = "none"
TOOLS_NATIVE = "native"
TOOLS_AUTO = "auto"


@dataclass
class OllamaRequest:
    """A ready-to-send call, plus the parts the trace record wants back."""

    endpoint: str
    payload: Dict[str, Any]
    messages: List[Dict[str, Any]]
    options: Dict[str, Any]
    system_message: str
    native_tools: bool


def build_prompt_tools_text(tools: List[Dict]) -> str:
    """Build a text description of tools for injection into the system prompt.

    Used when native tool calling doesn't work (e.g. Gemma 4 abliterated models).
    The model is instructed to emit <tool_call> XML blocks which the executor
    already knows how to parse.

    Cached (see ``_prompt_tools_text_cache``) since the same tool set is
    re-rendered on every tool-loop iteration in force_prompt_tools mode. The
    key canonicalizes each tool's whole ``function`` object (name,
    description, parameter schema) so a change ANYWHERE in the schema — a
    nested enum, a nested ``required`` list, an array's ``items`` contract,
    a bound like ``minimum``/``maxItems``, a ``$defs``/``anyOf`` alternative —
    busts the cache even when the tool's name and top-level description are
    unchanged. This matches what ``_render_prompt_tools_text`` itself now
    renders (see its docstring): the key was already correct for the FULL
    schema before this became true of the rendered text too.
    """
    cache_key = tuple(
        json.dumps(t.get("function", {}), sort_keys=True, separators=(",", ":"))
        for t in tools
    )
    cached = _prompt_tools_text_cache.get(cache_key)
    if cached is not None:
        return cached
    text = _render_prompt_tools_text(tools)
    _prompt_tools_text_cache.set(cache_key, text)
    return text


def _render_prompt_tools_text(tools: List[Dict]) -> str:
    """Actually render the tool-text block (the uncached half of ``build_prompt_tools_text``).

    Each tool's ``parameters`` value — the complete JSON Schema a caller
    supplied, whatever shape it takes — is embedded verbatim as compact JSON
    beside its name/description, rather than summarized through a second,
    lossy prose schema language. A hand-rolled "type (required): description"
    line per top-level property (the previous approach) can only describe a
    flat object schema's immediate properties; it silently drops everything
    else a JSON Schema can express — nested object/array item contracts,
    enums, defaults, numeric/length bounds, ``anyOf``/``oneOf`` alternatives,
    ``$defs`` — and misrepresents a schema with no top-level ``properties``
    (an explicitly empty object, one built entirely from ``anyOf``, one
    constrained only via ``additionalProperties``/``patternProperties``) as
    simply "no parameters". Serializing the schema itself has none of these
    gaps by construction, and needs no schema-shape special-casing: an
    explicitly-empty object (``{"type":"object","properties":{}}``) and one
    with constraints elsewhere (``{"type":"object","additionalProperties":false}``,
    an ``anyOf``-only schema, ...) simply serialize to different JSON.
    ``sort_keys=True`` keeps the output — and therefore the cache key
    interaction and any test pinning it — deterministic regardless of the
    key order a tool happened to build its schema dict in.
    """
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
        params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))

        lines.append(f"**{name}**: {desc}")
        lines.append(f"  Parameters: {params_json}")
        lines.append("")

    return "\n".join(lines)


def build_ollama_options(
    config: LLMConfig, options_override: Optional[Dict[str, Any]] = None
) -> tuple[Dict[str, Any], Any]:
    """Return the nested ``options`` object and the requested ``think`` value.

    The returned think value is the caller's explicit request, left
    unresolved: ``None`` when neither the saved config nor a per-call
    override named one. Resolving ``None`` to an automatic default is each
    caller's job — ``build_ollama_chat_request``'s automatic default depends
    on whether native tools end up on the wire, which this function has no
    visibility into. A per-call override only wins when it is itself
    explicit (non-``None``); an override dict that happens to carry
    ``"think": None`` leaves the saved value in place rather than clearing it.
    """
    provider_opts = config.provider_options or {}
    options_override = options_override or {}

    options: Dict[str, Any] = {
        "temperature": config.temperature,
        "num_predict": config.max_tokens,
    }

    think = provider_opts.get("think")

    for key in OLLAMA_OPTION_KEYS:
        if key in provider_opts:
            options[key] = provider_opts[key]

    if "temperature" in options_override:
        options["temperature"] = options_override["temperature"]
    if "top_p" in options_override:
        options["top_p"] = options_override["top_p"]
    if "top_k" in options_override:
        options["top_k"] = options_override["top_k"]
    if "min_p" in options_override:
        options["min_p"] = options_override["min_p"]
    if "max_tokens" in options_override:
        options["num_predict"] = options_override["max_tokens"]
    if options_override.get("think") is not None:
        think = options_override["think"]

    return options, think


def _attach_image(ollama_messages: List[Dict[str, Any]], image_data: str) -> None:
    for i in range(len(ollama_messages) - 1, -1, -1):
        if ollama_messages[i]["role"] == "user":
            ollama_messages[i]["images"] = [image_data]
            break


def _prompt_tools_assistant_turn(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Reconstruct an assistant's tool calls as XML text.

    In prompt-tools mode the conversation Ollama sees has no tool_calls field,
    so a replayed turn has to look the way the model was told to write it or the
    history stops being coherent.
    """
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
    return {"role": "assistant", "content": "\n".join(content_parts)}


def build_ollama_messages(
    messages: List[Dict[str, Any]],
    system_message: str,
    image_data: Optional[str],
    *,
    tool_aware: bool,
    force_prompt_tools: bool = False,
) -> List[Dict[str, Any]]:
    """Prepend the system turn, convert the history, attach an image if given.

    ``tool_aware`` is picked by the calling method, not by whether tools were
    passed: the tool-calling methods must round-trip `tool` results and
    assistant tool_calls even on a turn that offers no tools, while the plain
    history methods never see those roles.
    """
    ollama_messages: List[Dict[str, Any]] = []
    if system_message:
        ollama_messages.append({"role": "system", "content": system_message})

    for msg in messages:
        if not tool_aware:
            ollama_messages.append({"role": msg["role"], "content": msg["content"]})
        elif msg["role"] == "tool":
            if force_prompt_tools:
                # Ollama doesn't know about tool roles here, so label it as user text.
                tool_name = msg.get("name", "tool")
                ollama_messages.append({
                    "role": "user",
                    "content": f"[Tool Result: {tool_name}]\n{msg['content']}",
                })
            else:
                tool_msg: Dict[str, Any] = {"role": "tool", "content": msg["content"]}
                if "tool_call_id" in msg:
                    tool_msg["tool_call_id"] = msg["tool_call_id"]
                ollama_messages.append(tool_msg)
        elif msg["role"] == "assistant" and msg.get("tool_calls"):
            if force_prompt_tools:
                ollama_messages.append(_prompt_tools_assistant_turn(msg))
            else:
                # Ollama's native API needs object arguments (a string 400s).
                ollama_messages.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": normalize_tool_calls(msg["tool_calls"], as_object=True),
                })
        else:
            ollama_messages.append({"role": msg["role"], "content": msg.get("content", "")})

    if image_data:
        _attach_image(ollama_messages, image_data)

    return ollama_messages


def build_ollama_chat_request(
    config: LLMConfig,
    messages: List[Dict[str, Any]],
    system_message: str,
    image_data: Optional[str] = None,
    options_override: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict]] = None,
    *,
    tool_mode: str = TOOLS_NONE,
    stream: bool = False,
) -> OllamaRequest:
    """Assemble an /api/chat call, injecting prompt-tool text when asked for."""
    provider_opts = config.provider_options or {}
    options, think = build_ollama_options(config, options_override)

    force_prompt_tools = tool_mode == TOOLS_AUTO and bool(
        provider_opts.get("force_prompt_tools", False)
    )
    native_tools = bool(tools) and not force_prompt_tools

    if force_prompt_tools and tools:
        system_message = (system_message or "") + build_prompt_tools_text(tools)
        logging.info(f"[Ollama Tools] force_prompt_tools enabled — injected {len(tools)} tool(s) into system prompt")

    ollama_messages = build_ollama_messages(
        messages,
        system_message,
        image_data,
        tool_aware=tool_mode != TOOLS_NONE,
        force_prompt_tools=force_prompt_tools,
    )

    if think is None:
        # Automatic default, only when neither the saved config nor this call
        # requested an explicit mode: thinking makes a model reason about a
        # tool in the think phase and then describe the call in prose ("Let
        # me check...") instead of emitting a structured tool_call, so it
        # defaults off whenever native tools are on the wire. An explicit
        # true/false/level always wins over this default, tools or not.
        think = False if native_tools else True

    payload: Dict[str, Any] = {
        "model": config.model,
        "messages": ollama_messages,
        "stream": stream,
        "keep_alive": provider_opts.get("keep_alive", 0),
        "think": think,
        "options": options,
    }
    if native_tools:
        payload["tools"] = tools

    return OllamaRequest(
        endpoint="/api/chat",
        payload=payload,
        messages=ollama_messages,
        options=options,
        system_message=system_message,
        native_tools=native_tools,
    )


def build_ollama_generate_request(
    config: LLMConfig,
    prompt: str,
    system_message: str,
    image_data: Optional[str] = None,
) -> OllamaRequest:
    """Assemble a bare-prompt call: /api/generate for text, /api/chat for vision.

    Vision models take the system text in the root ``system`` field rather than
    as a system turn — matches OpenWebUI and works on more of them.
    """
    provider_opts = config.provider_options or {}
    options, think = build_ollama_options(config)
    # This call path never carries tools, so there is no "off by default
    # while tools are on the wire" case here — the automatic default is
    # always on, same as build_ollama_options returned unconditionally
    # before it started leaving None unresolved for build_ollama_chat_request
    # to interpret.
    think_enabled = True if think is None else think
    keep_alive = provider_opts.get("keep_alive", 0)

    if image_data:
        endpoint = "/api/chat"
        payload: Dict[str, Any] = {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt, "images": [image_data]}],
            "system": system_message,
            "stream": False,
            "keep_alive": keep_alive,
            "think": think_enabled,
            "options": options,
        }
    else:
        endpoint = "/api/generate"
        payload = {
            "model": config.model,
            "system": system_message,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
            "think": think_enabled,
            "options": options,
        }

    return OllamaRequest(
        endpoint=endpoint,
        payload=payload,
        messages=payload.get("messages", []),
        options=options,
        system_message=system_message,
        native_tools=False,
    )


class OllamaNDJSONDecoder:
    """Turns NDJSON lines into protocol-neutral events.

    Unlike OpenAI, Ollama never fragments tool_calls: a chunk's
    ``message.tool_calls``, when present, is already the complete list. The
    stream ends on a ``done`` object which also carries the token counts.

    ``label`` reaches only the warning logged for an undecodable line — the two
    stream methods have always logged under their own tag.
    """

    def __init__(self, label: str):
        self._label = label

    def feed(self, line: str) -> Iterator[Any]:
        if not line:
            return
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logging.warning(f"[{self._label}] Failed to parse line: {line}")
            return

        message = data.get("message", {})
        content = message.get("content", "")
        if content:
            yield TextDelta(content)
        if message.get("tool_calls"):
            yield ToolCalls(message["tool_calls"])
        if data.get("done", False):
            eval_count = data.get("eval_count")
            prompt_eval_count = data.get("prompt_eval_count")
            tokens_used = None
            if eval_count is not None or prompt_eval_count is not None:
                tokens_used = (eval_count or 0) + (prompt_eval_count or 0)
            yield Usage(
                tokens_used=tokens_used,
                prompt_tokens=prompt_eval_count,
                completion_tokens=eval_count,
            )
            yield Done(finish_reason=data.get("done_reason"))
