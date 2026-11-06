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
from src.features.llm.harness.build import user_message
from src.features.llm.harness.events import Usage
from src.features.llm.generation_hooks import fire_after_generate, fire_before_generate
from src.features.llm.repository import LLMRepository
from src.features.llm.response_processor import LLMResponseProcessor
from src.platform.plugins import PluginRegistry
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

    prompt = await fire_before_generate(
        plugins, user_id, config.id, request.prompt, bool(request.image_data), GenerationFailedException
    )

    # Load and prepare image if provided
    response_processor = LLMResponseProcessor()
    image_base64 = None
    if request.image_data:
        storage_dir = settings.get_file_storage_directory(user_id)
        image_base64 = await response_processor.load_and_prepare_image(
            request.image_data, storage_dir, max_size_mb=5
        )

    try:
        completion = await llm_service.complete(
            config,
            [user_message(prompt, images=(image_base64,) if image_base64 else ())],
            system=config.system_message,
        )
    except ValueError as e:
        raise GenerationFailedException(str(e))
    except Exception as e:
        logger.error(f"LLM generation error: {e}")
        raise GenerationFailedException(f"Failed to generate response: {str(e)}")

    content = completion.text
    usage = completion.usage or Usage()
    fire_after_generate(plugins, user_id, config.id, content, config.model, usage.total_tokens)

    return LLMGenerateResponse(
        content=content,
        model=config.model,
        provider_id=config.type,
        tokens_used=usage.total_tokens,
    )
