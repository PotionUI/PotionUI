"""
Response mappers for the llm feature.

Plain functions that turn an `LLMConfig` record into its API response DTO. No
class, no state - `is_default` is computed by the caller (it depends on
`repository.default_provider`, which these functions have no reason to read
themselves) and passed in explicitly.
"""
from src.features.llm.dto import LLMConfigResponse
from src.features.llm.repository import LLMConfig


def config_to_response(config: LLMConfig, is_default: bool) -> LLMConfigResponse:
    """
    Full config -> response mapping used by the configuration list/detail
    endpoints: every field on `config` (including `disable_system_prompt` and
    `provider_options`) passes through via `model_dump()`. The API key itself
    is popped and replaced with the `api_key_set` boolean - never returned.
    """
    config_data = config.model_dump()
    config_data["is_default"] = is_default
    config_data["api_key_set"] = bool(config_data.pop("api_key", None))
    return LLMConfigResponse(**config_data)


def assignment_config_to_response(config: LLMConfig) -> LLMConfigResponse:
    """
    Config -> response mapping used by the user/assignment listing endpoints.

    Deliberately narrower than `config_to_response`: it does not pass through
    `disable_system_prompt`/`provider_options` (those fall back to the
    response DTO's own defaults) and always reports `is_default=False` -
    matching the pre-dissolution manager behavior for these endpoints exactly.
    """
    return LLMConfigResponse(
        id=config.id,
        name=config.name,
        type=config.type,
        enabled=config.enabled,
        base_url=config.base_url,
        api_key_set=bool(config.api_key),
        model=config.model,
        system_message=config.system_message,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout,
        supports_vision=config.supports_vision,
        memory_reflection=config.memory_reflection,
        is_default=False,
    )
