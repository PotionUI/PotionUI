"""
Create/update/delete an LLM configuration; set the default provider.

Module-level functions, collaborators as explicit leading args - no class
holds them together.
"""
import logging
from typing import Optional, TYPE_CHECKING

from src.features.llm.dto import LLMConfigRequest
from src.features.llm.exceptions import (
    ConfigurationNotFoundException,
    ConfigurationExistsException,
    ConfigurationCreationFailedException,
    ConfigurationUpdateFailedException,
    ConfigurationDeletionFailedException,
    CannotDeleteDefaultConfigException,
)
from src.features.llm.hooks import LLM_CONFIG_HOOKS
from src.features.llm.repository import LLMConfig, LLMRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.util.ids import generate_ulid

if TYPE_CHECKING:
    from src.features.llm.tools.governance import ToolGovernanceRepository

logger = logging.getLogger(__name__)


def create_configuration(
    repo: LLMRepository, plugins: PluginRegistry, request: LLMConfigRequest
) -> str:
    """
    Create a new LLM configuration.

    Executes hooks:
    - llm.config.before_create: Can modify/validate config data or block
    - llm.config.after_create: Notification of successful creation

    Raises:
        ConfigurationExistsException: If configuration already exists
        ConfigurationCreationFailedException: If creation fails or blocked
    """
    # Generate ID if not provided
    config_id = request.id or generate_ulid()
    request.id = config_id

    # Execute before_create hook
    hook_data, blocked = execute_hook(
        plugins,
        LLM_CONFIG_HOOKS.before_create,
        {"config_id": config_id, "request": request.model_dump()},
    )

    if blocked:
        reason = hook_data.get("block_reason", "Configuration creation blocked")
        logger.warning(f"LLM config creation blocked by plugin: {reason}")
        raise ConfigurationCreationFailedException(reason)

    # Check if configuration already exists
    if repo.get_configuration(config_id):
        raise ConfigurationExistsException(
            f"LLM configuration '{config_id}' already exists"
        )

    # Create configuration
    config = LLMConfig(**request.model_dump())
    success = repo.create_configuration(config)

    if not success:
        raise ConfigurationCreationFailedException(
            "Failed to create LLM configuration"
        )

    # Execute after_create hook
    execute_hook(plugins, LLM_CONFIG_HOOKS.after_create, {"config_id": config_id})

    logger.info(f"LLM configuration created: {config_id}")
    return config_id


def update_configuration(
    repo: LLMRepository,
    plugins: PluginRegistry,
    config_id: str,
    request: LLMConfigRequest,
) -> str:
    """
    Update an existing LLM configuration.

    Executes hooks:
    - llm.config.before_update: Can modify/validate config data or block
    - llm.config.after_update: Notification of successful update

    Raises:
        ConfigurationNotFoundException: If configuration not found
        ConfigurationUpdateFailedException: If update fails or blocked
    """
    # Check if configuration exists
    existing = repo.get_configuration(config_id)
    if not existing:
        raise ConfigurationNotFoundException(
            f"LLM configuration '{config_id}' not found"
        )

    # Keep-existing-key semantics: responses never return the stored API
    # key, so a client editing a config sends back either the sentinel
    # "__unchanged__" or an empty value to mean "leave the key as-is".
    # Only a real, non-empty, non-sentinel value replaces the stored key.
    if request.api_key in (None, "", "__unchanged__"):
        request.api_key = existing.api_key

    # Execute before_update hook
    hook_data, blocked = execute_hook(
        plugins,
        LLM_CONFIG_HOOKS.before_update,
        {"config_id": config_id, "request": request.model_dump()},
    )

    if blocked:
        reason = hook_data.get("block_reason", "Configuration update blocked")
        logger.warning(f"LLM config update blocked by plugin: {reason}")
        raise ConfigurationUpdateFailedException(reason)

    # Ensure the ID in the request matches
    request.id = config_id

    # Update configuration
    config = LLMConfig(**request.model_dump())
    success = repo.update_configuration(config_id, config)

    if not success:
        raise ConfigurationUpdateFailedException("Failed to update LLM configuration")

    # Execute after_update hook
    execute_hook(plugins, LLM_CONFIG_HOOKS.after_update, {"config_id": config_id})

    logger.info(f"LLM configuration updated: {config_id}")
    return config_id


def delete_configuration(
    repo: LLMRepository,
    plugins: PluginRegistry,
    tool_governance_repository: Optional["ToolGovernanceRepository"],
    config_id: str,
) -> str:
    """
    Delete an LLM configuration.

    Executes hooks:
    - llm.config.before_delete: Can block deletion
    - llm.config.after_delete: Notification of successful deletion

    Raises:
        ConfigurationNotFoundException: If configuration not found
        CannotDeleteDefaultConfigException: If trying to delete default config
        ConfigurationDeletionFailedException: If deletion fails or blocked
    """
    # Check if configuration exists
    if not repo.get_configuration(config_id):
        raise ConfigurationNotFoundException(
            f"LLM configuration '{config_id}' not found"
        )

    # Check if it's the default configuration
    if repo.default_provider == config_id:
        raise CannotDeleteDefaultConfigException(
            "Cannot delete the default LLM configuration"
        )

    # Execute before_delete hook
    hook_data, blocked = execute_hook(
        plugins, LLM_CONFIG_HOOKS.before_delete, {"config_id": config_id}
    )

    if blocked:
        reason = hook_data.get("block_reason", "Configuration deletion blocked")
        logger.warning(f"LLM config deletion blocked by plugin: {reason}")
        raise ConfigurationDeletionFailedException(reason)

    # Delete configuration
    success = repo.delete_configuration(config_id)

    if not success:
        raise ConfigurationDeletionFailedException("Failed to delete LLM configuration")

    if tool_governance_repository is not None:
        tool_governance_repository.delete_config(config_id)

    # Execute after_delete hook
    execute_hook(plugins, LLM_CONFIG_HOOKS.after_delete, {"config_id": config_id})

    logger.info(f"LLM configuration deleted: {config_id}")
    return config_id


def set_default_provider(repo: LLMRepository, config_id: str) -> str:
    """
    Set the default LLM provider.

    Raises:
        ConfigurationNotFoundException: If configuration not found
        ConfigurationUpdateFailedException: If setting default fails
    """
    if not repo.get_configuration(config_id):
        raise ConfigurationNotFoundException(
            f"LLM configuration '{config_id}' not found"
        )

    success = repo.set_default_provider(config_id)

    if not success:
        raise ConfigurationUpdateFailedException("Failed to set default LLM provider")

    logger.info(f"Default LLM provider set to: {config_id}")
    return config_id
