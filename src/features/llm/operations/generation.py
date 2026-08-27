"""
Generate an LLM response for a user prompt.

Module-level function, collaborators as explicit leading args - no class
holds them together.
"""
import logging

from src.features.llm.dto import LLMGenerateRequest, LLMGenerateResponse
from src.features.llm.exceptions import (
    ConfigurationNotFoundException,
    VisionNotSupportedException,
    GenerationFailedException,
)
from src.features.llm.gateway import LLMGateway
from src.features.llm.hooks import LLM_HOOKS
from src.features.llm.repository import LLMRepository
from src.features.llm.response_processor import LLMResponseProcessor
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)


async def generate_response(
    repo: LLMRepository,
    llm_service: LLMGateway,
    settings: Settings,
    plugins: PluginRegistry,
    request: LLMGenerateRequest,
    user_id: str,
) -> LLMGenerateResponse:
    """
    Generate a response using an LLM.

    Executes hooks:
    - llm.before_generate: Can modify prompt or config or block
    - llm.after_generate: Notification with generated content

    Raises:
        ConfigurationNotFoundException: If no LLM configuration found
        VisionNotSupportedException: If vision requested but not supported
        ImageLoadFailedException: If image loading fails
        GenerationFailedException: If generation fails
    """
    logger.debug(
        f"LLM Generate request - config_id: {request.config_id}, "
        f"has_image: {bool(request.image_data)}"
    )

    # Get configuration
    config = (
        repo.get_configuration(request.config_id)
        if request.config_id
        else repo.get_default_configuration()
    )
    if not config:
        raise ConfigurationNotFoundException("No LLM configuration found")

    # Validate vision support
    if request.image_data and not config.supports_vision:
        raise VisionNotSupportedException(
            f"LLM configuration '{config.name}' does not support vision. "
            "Please enable vision support in the configuration."
        )

    # Execute before_generate hook
    hook_data, blocked = execute_hook(
        plugins,
        LLM_HOOKS.before_generate,
        {
            "user_id": user_id,
            "config_id": config.id,
            "prompt": request.prompt,
            "has_image": bool(request.image_data),
        },
    )

    if blocked:
        reason = hook_data.get("block_reason", "Generation blocked")
        logger.warning(f"LLM generation blocked by plugin: {reason}")
        raise GenerationFailedException(reason)

    # Allow hooks to modify prompt
    prompt = hook_data.get("prompt", request.prompt)

    # Load and prepare image if provided
    response_processor = LLMResponseProcessor()
    image_base64 = None
    if request.image_data:
        storage_dir = settings.get_file_storage_directory(user_id)
        image_base64 = await response_processor.load_and_prepare_image(
            request.image_data, storage_dir, max_size_mb=5
        )

    system_message = config.system_message

    # Generate response
    try:
        response = await llm_service.generate_response(
            prompt=prompt,
            config=config,
            system_message=system_message,
            image_data=image_base64,
        )
    except ValueError as e:
        raise GenerationFailedException(str(e))
    except Exception as e:
        logger.error(f"LLM generation error: {e}")
        raise GenerationFailedException(f"Failed to generate response: {str(e)}")

    # Remove thinking tags from the response content
    logger.debug(
        f"LLM raw response content (first 500 chars): "
        f"{response.content[:500] if response.content else 'EMPTY'}"
    )
    cleaned_content = response_processor.remove_thinking_tags(response.content)
    logger.debug(
        f"LLM cleaned content (first 500 chars): "
        f"{cleaned_content[:500] if cleaned_content else 'EMPTY'}"
    )

    # Execute after_generate hook
    execute_hook(
        plugins,
        LLM_HOOKS.after_generate,
        {
            "user_id": user_id,
            "config_id": config.id,
            "content": cleaned_content,
            "model": response.model,
            "tokens_used": response.tokens_used,
        },
    )

    return LLMGenerateResponse(
        content=cleaned_content,
        model=response.model,
        provider_id=response.provider_id,
        tokens_used=response.tokens_used,
    )
