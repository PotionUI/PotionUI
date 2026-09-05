import logging
from contextlib import aclosing
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.features.llm import context_budget
from src.features.llm.clients import LLMClient, LLMResponse, NativeLLMClient, OllamaClient, OpenAIClient
from src.features.llm.repository import LLMConfig, LLMRepository
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle


class LLMGateway:
    """The entry point every caller holds to reach a configured LLM.

    Resolves a configuration id to its ``LLMConfig``, derives the effective
    system message from disable-prompt rules, selects the provider client
    that matches ``config.type``, and delegates the actual call. The shape of
    the request (chat vs. tools, buffered vs. streamed) is chosen by which
    method is invoked; the provider-specific wire format lives in the clients.
    """

    def __init__(self, llm_repository: LLMRepository, model_lifecycle: Optional[ModelLifecycle] = None):
        self.repository = llm_repository
        self._ollama = OllamaClient()
        self._openai = OpenAIClient()
        self._native = NativeLLMClient(model_lifecycle)

    def _client_for(self, config: LLMConfig) -> LLMClient:
        if config.type == "ollama":
            return self._ollama
        elif config.type == "openai":
            return self._openai
        elif config.type == "native":
            return self._native
        raise ValueError(f"Unsupported LLM type: {config.type}")

    @staticmethod
    def _safe_provider_hook(provider_method: Optional[Any], config: LLMConfig, label: str) -> Optional[Any]:
        """Call an optional per-provider accounting hook (``token_counter``/
        ``messages_token_counter``), degrading to ``None`` on any failure —
        a provider hook is best-effort, never allowed to break a send."""
        if provider_method is None:
            return None
        try:
            return provider_method(config)
        except Exception:
            logging.debug("[LLMGateway] %s failed for '%s'", label, config.id, exc_info=True)
            return None

    def token_counter_for(self, config: LLMConfig) -> Optional[context_budget.TokenCounter]:
        """A cheap, already-available tokenizer for *config*'s provider, or
        ``None`` when only the chars-per-token estimate is available.

        Ollama and OpenAI-compatible clients never have a compatible
        tokenizer in-process; ``NativeLLMClient`` exposes one via an optional
        ``token_counter(config)`` method, but only while the checkpoint is
        already warm (see its docstring) — this never triggers a model load
        just to count tokens for budgeting. A thin wrapper around
        ``accounting_inputs_for`` kept for callers that only need this one
        piece.
        """
        return self.accounting_inputs_for(config).counter

    def accounting_inputs_for(
        self, config: LLMConfig, options_override: Optional[Dict[str, Any]] = None,
    ) -> context_budget.AccountingInputs:
        """The single shared source of (capacity, reserve, tokenizer(s),
        per-image override) for *config* — built here ONCE and used both by
        ``estimate_context_budget`` (every real send, via ``_budgeted``) and
        by ``ConversationRunner``'s pre-flight ledger check
        (``_resolve_budget_inputs``), so the two can never compute different
        numbers for the same request.
        """
        client = self._client_for(config)
        capacity = context_budget.resolve_capacity(config)
        reserve_tokens = (options_override or {}).get("max_tokens", config.max_tokens)
        counter = self._safe_provider_hook(getattr(client, "token_counter", None), config, "token_counter")
        messages_counter = self._safe_provider_hook(
            getattr(client, "messages_token_counter", None), config, "messages_token_counter",
        )
        image_tokens_override = context_budget.resolve_image_token_override(config)
        return context_budget.AccountingInputs(
            capacity=capacity,
            reserve_tokens=reserve_tokens,
            counter=counter,
            messages_counter=messages_counter,
            image_tokens_override=image_tokens_override,
        )

    def estimate_context_budget(
        self,
        config: LLMConfig,
        system_message: Optional[str],
        messages: List[Dict[str, Any]],
        tool_schemas: Optional[List[Dict[str, Any]]] = None,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None,
    ) -> context_budget.BudgetOutcome:
        """The one shared budgeting call every gateway send path routes
        through (see ``_budgeted``) and that ``ConversationRunner`` also uses
        for its pre-flight context-ledger check — same numbers, not a second
        heuristic. Raises ``context_budget.ContextBudgetExceededError`` when
        the request doesn't fit even after trimming every eligible older
        message.
        """
        inputs = self.accounting_inputs_for(config, options_override)
        return context_budget.enforce_budget(
            capacity_tokens=inputs.capacity.capacity_tokens,
            capacity_source=inputs.capacity.source,
            reserve_tokens=inputs.reserve_tokens,
            system_message=system_message,
            messages=messages,
            tool_schemas=tool_schemas,
            image_data=image_data,
            image_tokens=inputs.image_tokens_override,
            counter=inputs.counter,
            messages_counter=inputs.messages_counter,
        )

    def _budgeted(
        self,
        config: LLMConfig,
        system_message: Optional[str],
        messages: List[Dict[str, Any]],
        tool_schemas: Optional[List[Dict[str, Any]]],
        image_data: Optional[str],
        options_override: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Budget-check and, if needed, trim *messages* before ANY client
        call — the single choke point every one of the four send methods
        below calls, so a provider-specific path can never skip it."""
        outcome = self.estimate_context_budget(
            config, system_message, messages, tool_schemas, image_data, options_override,
        )
        return outcome.messages

    def _resolve_system_message(
        self,
        config: LLMConfig,
        custom_system_message: Optional[str] = None,
        log_prefix: str = "[Chat]",
    ) -> str:
        """Resolve the effective system message for a chat call.

        Priority:
        1. Custom system message (e.g. the tool system prompt) - always used when present.
        2. Empty string when the config disables the system prompt.
        3. Config default system message.
        """
        if custom_system_message:
            system_message = custom_system_message
            logging.info(f"{log_prefix} Using custom_system_message (length: {len(system_message)})")
        elif config.disable_system_prompt:
            system_message = ""
            logging.info(f"{log_prefix} System prompt disabled for config '{config.id}'")
        else:
            system_message = config.system_message
            logging.info(f"{log_prefix} Using config default system_message")
        return system_message

    async def generate_with_config_id(
        self,
        prompt: str,
        llm_id: str,
        image_data: Optional[str] = None
    ) -> LLMResponse:
        """Generate a response using a configuration id from the repository.

        Args:
            prompt: The text prompt
            llm_id: LLM configuration ID
            image_data: Optional base64 encoded image for vision models

        Returns:
            LLMResponse with generated content

        Raises:
            ValueError: If LLM configuration not found
        """
        config = self.repository.get_configuration(llm_id)
        if not config:
            raise ValueError(f"LLM configuration '{llm_id}' not found")

        system_message = "" if config.disable_system_prompt else config.system_message

        return await self.generate_response(
            prompt=prompt,
            config=config,
            system_message=system_message,
            image_data=image_data
        )

    async def generate_response(self, prompt: str, config: LLMConfig, system_message: str, image_data: Optional[str] = None) -> LLMResponse:
        """Generate a response against an already-resolved configuration.

        Args:
            prompt: The text prompt
            config: LLM configuration
            system_message: System message/instructions
            image_data: Optional base64 encoded image for vision models
        """
        if not config.enabled:
            raise ValueError(f"LLM configuration '{config.id}' is disabled")

        return await self._client_for(config).generate(prompt, config, system_message, image_data)

    async def test_configuration(self, config: LLMConfig) -> Dict[str, Any]:
        """Probe a configuration with a fixed prompt and report reachability."""
        try:
            test_prompt = "Hello, this is a test. Please respond with 'Test successful!'"
            response = await self.generate_response(test_prompt, config, config.system_message)

            return {
                "success": True,
                "response": response.content,
                "model": response.model,
                "tokens_used": response.tokens_used
            }
        except Exception:
            logging.error(f"[Chat] Configuration test failed for '{config.id}'", exc_info=True)
            return {"success": False, "error": "Failed to reach the configured LLM provider."}

    async def generate_with_history(
        self,
        messages: list[Dict[str, str]],
        llm_id: str,
        image_data: Optional[str] = None,
        custom_system_message: Optional[str] = None,
        mode: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Generate a response over a full conversation history.

        Args:
            messages: List of messages with 'role' and 'content' keys
            llm_id: LLM configuration ID
            image_data: Optional base64 encoded image for vision models
            custom_system_message: Optional custom system message (overrides config default)
            mode: Optional chat mode id (informational)
            options_override: Optional sampling overrides (temperature, top_p, top_k, max_tokens,
                think) applied on top of config/provider_options. Keys not recognized by the
                target provider are ignored.

        Returns:
            LLMResponse with generated content

        Raises:
            ValueError: If LLM configuration not found
        """
        config = self.repository.get_configuration(llm_id)
        if not config:
            raise ValueError(f"LLM configuration '{llm_id}' not found")

        if not config.enabled:
            raise ValueError(f"LLM configuration '{config.id}' is disabled")

        system_message = self._resolve_system_message(
            config, custom_system_message, log_prefix="[Chat]"
        )
        messages = self._budgeted(config, system_message, messages, None, image_data, options_override)

        return await self._client_for(config).generate_with_history(
            messages, config, system_message, image_data, options_override
        )

    async def stream_with_history(
        self,
        messages: list[Dict[str, str]],
        llm_id: str,
        image_data: Optional[str] = None,
        custom_system_message: Optional[str] = None,
        mode: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[dict, None]:
        """Stream a response over a full conversation history.

        Args:
            messages: List of messages with 'role' and 'content' keys
            llm_id: LLM configuration ID
            image_data: Optional base64 encoded image for vision models
            custom_system_message: Optional custom system message (overrides config default)
            mode: Optional chat mode id (informational)
            options_override: Optional sampling overrides (temperature, top_p, top_k,
                max_tokens, think) applied on top of config/provider_options

        Yields:
            Dicts: {"type": "token", "content": str} for text chunks,
                   {"type": "usage", "tokens_used": int, "prompt_tokens": int, "completion_tokens": int} at end

        Raises:
            ValueError: If LLM configuration not found or unsupported type
        """
        config = self.repository.get_configuration(llm_id)
        if not config:
            raise ValueError(f"LLM configuration '{llm_id}' not found")

        if not config.enabled:
            raise ValueError(f"LLM configuration '{config.id}' is disabled")

        system_message = self._resolve_system_message(
            config, custom_system_message, log_prefix="[Chat Stream]"
        )
        messages = self._budgeted(config, system_message, messages, None, image_data, options_override)

        # Owns the client's stream: closing THIS generator (aclose(), or an
        # exception unwinding through it) must close the client's — an
        # unguarded `async for` here would leave a suspended provider stream
        # (and whatever it holds — an HTTP response, a lease) to the event
        # loop's async-generator finalizer instead of closing it at the
        # point this generator itself is closed.
        async with aclosing(self._client_for(config).stream_with_history(
            messages, config, system_message, image_data, options_override
        )) as agen:
            async for event in agen:
                yield event

    async def generate_with_tools(
        self,
        messages: list[Dict[str, Any]],
        llm_id: str,
        tools: List[Dict] = None,
        image_data: Optional[str] = None,
        custom_system_message: Optional[str] = None,
        mode: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Generate a response with native tool calling support.

        Args:
            messages: List of messages with 'role', 'content', and optional 'tool_calls'/'tool_call_id' keys
            llm_id: LLM configuration ID
            tools: Optional list of tool definitions to pass to the model
            image_data: Optional base64 encoded image for vision models
            custom_system_message: Optional custom system message (overrides config default)
            mode: Optional chat mode id (informational)
            options_override: Optional sampling overrides (temperature, top_p, top_k,
                max_tokens, think) applied on top of config/provider_options

        Returns:
            LLMResponse with generated content, and tool_calls/finish_reason populated when applicable

        Raises:
            ValueError: If LLM configuration not found
        """
        config = self.repository.get_configuration(llm_id)
        if not config:
            raise ValueError(f"LLM configuration '{llm_id}' not found")

        if not config.enabled:
            raise ValueError(f"LLM configuration '{config.id}' is disabled")

        system_message = self._resolve_system_message(
            config, custom_system_message, log_prefix="[Chat]"
        )
        messages = self._budgeted(config, system_message, messages, tools, image_data, options_override)

        return await self._client_for(config).generate_with_tools(
            messages, config, system_message, tools, image_data, options_override
        )

    async def stream_with_tools(
        self,
        messages: list[Dict[str, Any]],
        llm_id: str,
        tools: Optional[List[Dict]] = None,
        image_data: Optional[str] = None,
        custom_system_message: Optional[str] = None,
        mode: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[dict, None]:
        """Stream a tool-calling turn, or the final response after tool iterations complete.

        With `tools`, this is a native-tool-calling turn: the model may emit
        tool_calls instead of (or, per-provider contract, never alongside) text.
        Without `tools`, this is the plain final-answer stream used once the
        tool loop has decided no further tools are needed. Messages may
        contain tool_calls and tool result messages from prior iterations.

        Yields:
            Dicts: {"type": "token", "content": str} for text chunks,
                   {"type": "tool_calls", "tool_calls": [...]} at most once, iff the
                       model requested tool calls this turn,
                   {"type": "usage", "tokens_used": int, "prompt_tokens": int, "completion_tokens": int} at end
        """
        config = self.repository.get_configuration(llm_id)
        if not config:
            raise ValueError(f"LLM configuration '{llm_id}' not found")

        if not config.enabled:
            raise ValueError(f"LLM configuration '{config.id}' is disabled")

        system_message = self._resolve_system_message(
            config, custom_system_message, log_prefix="[Chat Stream]"
        )
        messages = self._budgeted(config, system_message, messages, tools, image_data, options_override)

        # See stream_with_history's comment above — same ownership rule.
        async with aclosing(self._client_for(config).stream_with_tools(
            messages, config, system_message, tools, image_data, options_override
        )) as agen:
            async for event in agen:
                yield event
