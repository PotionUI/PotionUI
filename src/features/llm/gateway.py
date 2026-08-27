import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

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

        async for event in self._client_for(config).stream_with_history(
            messages, config, system_message, image_data, options_override
        ):
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

        async for event in self._client_for(config).stream_with_tools(
            messages, config, system_message, tools, image_data, options_override
        ):
            yield event
