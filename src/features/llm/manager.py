"""
LLM operations coordinator.

This module provides the LLMManager class that orchestrates all LLM-related
business logic, including configuration management,
user assignments, and response generation.
"""

import logging
from typing import Dict, List, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.features.llm.tools.governance import ToolGovernanceRepository


from src.features.llm.dto import (
    LLMConfigRequest,
    LLMConfigResponse,
    LLMGenerateRequest,
    LLMGenerateResponse,
)
from src.features.llm.gateway import LLMGateway
from src.features.llm.clients.base import LLMResponse as ServiceLLMResponse
from src.features.llm.exceptions import (
    ConfigurationNotFoundException,
    ConfigurationExistsException,
    ConfigurationCreationFailedException,
    ConfigurationUpdateFailedException,
    ConfigurationDeletionFailedException,
    CannotDeleteDefaultConfigException,
    VisionNotSupportedException,
    ImageLoadFailedException,
    GenerationFailedException,
    AssignmentNotFoundException,
    AssignmentFailedException,
)
from src.features.llm.response_processor import LLMResponseProcessor
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.llm.hooks import LLM_CONFIG_HOOKS, LLM_HOOKS
from src.platform.settings.settings import SettingsManager
from src.features.llm.repository import (
    LLMRepository,
    LLMConfig,
)
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)


class LLMManager:
    """
    Orchestrates LLM configuration, style, and generation operations.

    Combines repository access, LLM service calls, response processing,
    and plugin hook execution into cohesive LLM workflows.
    """

    def __init__(
        self,
        llm_repository: LLMRepository,
        llm_service: LLMGateway,
        settings_manager: SettingsManager,
        plugin_registry: PluginRegistry,
        tool_governance_repository: Optional["ToolGovernanceRepository"] = None,
    ):
        """Initialize LLMManager.

        Args:
            llm_repository: Repository for LLM data access
            llm_service: Service for LLM API calls
            settings_manager: Application settings manager
            plugin_registry: Plugin registry for hook execution
        """
        self.repository = llm_repository
        self.llm_service = llm_service
        self.settings_manager = settings_manager
        self.plugins = plugin_registry
        self.tool_governance_repository = tool_governance_repository
        self.response_processor = LLMResponseProcessor()

    # --- Hook Execution Helpers ---

    # =========================================================================
    # Configuration Management
    # =========================================================================

    def get_all_configurations(self) -> Dict[str, Any]:
        """Get all LLM configurations.

        Returns:
            Dict with 'configurations' list and 'default_provider'
        """
        configs = self.repository.get_all_configurations()
        default_provider = self.repository.default_provider

        response_configs = []
        for config_id, config in configs.items():
            config_data = config.model_dump()
            config_data["is_default"] = (config_id == default_provider)
            # Never expose the stored API key; only report whether one is set.
            config_data["api_key_set"] = bool(config_data.pop("api_key", None))
            response_configs.append(LLMConfigResponse(**config_data))

        return {
            "configurations": response_configs,
            "default_provider": default_provider
        }

    def get_configuration(self, config_id: str) -> LLMConfigResponse:
        """Get a specific LLM configuration.

        Args:
            config_id: Configuration ID

        Returns:
            LLMConfigResponse

        Raises:
            ConfigurationNotFoundException: If configuration not found
        """
        config = self.repository.get_configuration(config_id)
        if not config:
            raise ConfigurationNotFoundException(
                f"LLM configuration '{config_id}' not found"
            )

        config_data = config.model_dump()
        config_data["is_default"] = (config_id == self.repository.default_provider)
        # Never expose the stored API key; only report whether one is set.
        config_data["api_key_set"] = bool(config_data.pop("api_key", None))
        return LLMConfigResponse(**config_data)

    def create_configuration(self, request: LLMConfigRequest) -> str:
        """Create a new LLM configuration.

        Executes hooks:
        - llm.config.before_create: Can modify/validate config data or block
        - llm.config.after_create: Notification of successful creation

        Args:
            request: Configuration request data

        Returns:
            Created configuration ID

        Raises:
            ConfigurationExistsException: If configuration already exists
            ConfigurationCreationFailedException: If creation fails or blocked
        """
        # Generate ID if not provided
        config_id = request.id or generate_ulid()
        request.id = config_id

        # Execute before_create hook
        hook_data, blocked = execute_hook(self.plugins,
            LLM_CONFIG_HOOKS.before_create,
            {"config_id": config_id, "request": request.model_dump()}
        )

        if blocked:
            reason = hook_data.get("block_reason", "Configuration creation blocked")
            logger.warning(f"LLM config creation blocked by plugin: {reason}")
            raise ConfigurationCreationFailedException(reason)

        # Check if configuration already exists
        if self.repository.get_configuration(config_id):
            raise ConfigurationExistsException(
                f"LLM configuration '{config_id}' already exists"
            )

        # Create configuration
        config = LLMConfig(**request.model_dump())
        success = self.repository.create_configuration(config)

        if not success:
            raise ConfigurationCreationFailedException(
                "Failed to create LLM configuration"
            )

        # Execute after_create hook
        execute_hook(self.plugins,
            LLM_CONFIG_HOOKS.after_create,
            {"config_id": config_id}
        )

        logger.info(f"LLM configuration created: {config_id}")
        return config_id

    def update_configuration(self, config_id: str, request: LLMConfigRequest) -> str:
        """Update an existing LLM configuration.

        Executes hooks:
        - llm.config.before_update: Can modify/validate config data or block
        - llm.config.after_update: Notification of successful update

        Args:
            config_id: Configuration ID
            request: Updated configuration data

        Returns:
            Updated configuration ID

        Raises:
            ConfigurationNotFoundException: If configuration not found
            ConfigurationUpdateFailedException: If update fails or blocked
        """
        # Check if configuration exists
        existing = self.repository.get_configuration(config_id)
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
        hook_data, blocked = execute_hook(self.plugins,
            LLM_CONFIG_HOOKS.before_update,
            {"config_id": config_id, "request": request.model_dump()}
        )

        if blocked:
            reason = hook_data.get("block_reason", "Configuration update blocked")
            logger.warning(f"LLM config update blocked by plugin: {reason}")
            raise ConfigurationUpdateFailedException(reason)

        # Ensure the ID in the request matches
        request.id = config_id

        # Update configuration
        config = LLMConfig(**request.model_dump())
        success = self.repository.update_configuration(config_id, config)

        if not success:
            raise ConfigurationUpdateFailedException(
                "Failed to update LLM configuration"
            )

        # Execute after_update hook
        execute_hook(self.plugins,
            LLM_CONFIG_HOOKS.after_update,
            {"config_id": config_id}
        )

        logger.info(f"LLM configuration updated: {config_id}")
        return config_id

    def delete_configuration(self, config_id: str) -> str:
        """Delete an LLM configuration.

        Executes hooks:
        - llm.config.before_delete: Can block deletion
        - llm.config.after_delete: Notification of successful deletion

        Args:
            config_id: Configuration ID

        Returns:
            Deleted configuration ID

        Raises:
            ConfigurationNotFoundException: If configuration not found
            CannotDeleteDefaultConfigException: If trying to delete default config
            ConfigurationDeletionFailedException: If deletion fails or blocked
        """
        # Check if configuration exists
        if not self.repository.get_configuration(config_id):
            raise ConfigurationNotFoundException(
                f"LLM configuration '{config_id}' not found"
            )

        # Check if it's the default configuration
        if self.repository.default_provider == config_id:
            raise CannotDeleteDefaultConfigException(
                "Cannot delete the default LLM configuration"
            )

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            LLM_CONFIG_HOOKS.before_delete,
            {"config_id": config_id}
        )

        if blocked:
            reason = hook_data.get("block_reason", "Configuration deletion blocked")
            logger.warning(f"LLM config deletion blocked by plugin: {reason}")
            raise ConfigurationDeletionFailedException(reason)

        # Delete configuration
        success = self.repository.delete_configuration(config_id)

        if not success:
            raise ConfigurationDeletionFailedException(
                "Failed to delete LLM configuration"
            )

        if self.tool_governance_repository is not None:
            self.tool_governance_repository.delete_config(config_id)

        # Execute after_delete hook
        execute_hook(self.plugins,
            LLM_CONFIG_HOOKS.after_delete,
            {"config_id": config_id}
        )

        logger.info(f"LLM configuration deleted: {config_id}")
        return config_id

    def set_default_provider(self, config_id: str) -> str:
        """Set the default LLM provider.

        Args:
            config_id: Configuration ID to set as default

        Returns:
            New default provider ID

        Raises:
            ConfigurationNotFoundException: If configuration not found
            ConfigurationUpdateFailedException: If setting default fails
        """
        # Check if configuration exists
        if not self.repository.get_configuration(config_id):
            raise ConfigurationNotFoundException(
                f"LLM configuration '{config_id}' not found"
            )

        # Set as default
        success = self.repository.set_default_provider(config_id)

        if not success:
            raise ConfigurationUpdateFailedException(
                "Failed to set default LLM provider"
            )

        logger.info(f"Default LLM provider set to: {config_id}")
        return config_id

    async def test_configuration(self, config_id: str) -> Dict[str, Any]:
        """Test an LLM configuration.

        Args:
            config_id: Configuration ID to test

        Returns:
            Test result dict with 'success' and optional 'error'

        Raises:
            ConfigurationNotFoundException: If configuration not found
        """
        config = self.repository.get_configuration(config_id)
        if not config:
            raise ConfigurationNotFoundException(
                f"LLM configuration '{config_id}' not found"
            )

        return await self.llm_service.test_configuration(config)

    # =========================================================================
    # LLM Generation
    # =========================================================================

    async def generate_response(
        self,
        request: LLMGenerateRequest,
        user_id: str
    ) -> LLMGenerateResponse:
        """Generate a response using an LLM.

        Executes hooks:
        - llm.before_generate: Can modify prompt or config or block
        - llm.after_generate: Notification with generated content

        Args:
            request: Generation request
            user_id: User ID making the request

        Returns:
            LLMGenerateResponse with generated content

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
            self.repository.get_configuration(request.config_id)
            if request.config_id
            else self.repository.get_default_configuration()
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
        hook_data, blocked = execute_hook(self.plugins,
            LLM_HOOKS.before_generate,
            {
                "user_id": user_id,
                "config_id": config.id,
                "prompt": request.prompt,
                "has_image": bool(request.image_data)
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Generation blocked")
            logger.warning(f"LLM generation blocked by plugin: {reason}")
            raise GenerationFailedException(reason)

        # Allow hooks to modify prompt
        prompt = hook_data.get("prompt", request.prompt)

        # Load and prepare image if provided
        image_base64 = None
        if request.image_data:
            storage_dir = self.settings_manager.get_file_storage_directory(user_id)
            image_base64 = await self.response_processor.load_and_prepare_image(
                request.image_data,
                storage_dir,
                max_size_mb=5
            )

        system_message = config.system_message

        # Generate response
        try:
            response = await self.llm_service.generate_response(
                prompt=prompt,
                config=config,
                system_message=system_message,
                image_data=image_base64
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
        cleaned_content = self.response_processor.remove_thinking_tags(response.content)
        logger.debug(
            f"LLM cleaned content (first 500 chars): "
            f"{cleaned_content[:500] if cleaned_content else 'EMPTY'}"
        )

        # Execute after_generate hook
        execute_hook(self.plugins,
            LLM_HOOKS.after_generate,
            {
                "user_id": user_id,
                "config_id": config.id,
                "content": cleaned_content,
                "model": response.model,
                "tokens_used": response.tokens_used
            }
        )

        return LLMGenerateResponse(
            content=cleaned_content,
            model=response.model,
            provider_id=response.provider_id,
            tokens_used=response.tokens_used
        )

    # =========================================================================
    # User LLM Assignments
    # =========================================================================

    def assign_llm_to_user(self, user_id: str, llm_config_id: str) -> Dict[str, str]:
        """Assign an LLM configuration to a user.

        Args:
            user_id: User ID
            llm_config_id: LLM configuration ID

        Returns:
            Dict with user_id and llm_config_id

        Raises:
            ConfigurationNotFoundException: If LLM config not found
            AssignmentFailedException: If assignment fails
        """
        # Check if LLM configuration exists
        config = self.repository.get_configuration(llm_config_id)
        if not config:
            raise ConfigurationNotFoundException(
                f"LLM configuration '{llm_config_id}' not found"
            )

        # Assign LLM to user
        success = self.repository.assign_llm_to_user(user_id, llm_config_id)

        if not success:
            raise AssignmentFailedException("Failed to assign LLM to user")

        logger.info(f"LLM '{config.name}' assigned to user {user_id}")
        return {"user_id": user_id, "llm_config_id": llm_config_id}

    def unassign_llm_from_user(self, user_id: str, llm_config_id: str) -> Dict[str, str]:
        """Remove LLM configuration assignment from a user.

        Args:
            user_id: User ID
            llm_config_id: LLM configuration ID

        Returns:
            Dict with user_id and llm_config_id

        Raises:
            AssignmentNotFoundException: If assignment not found
            AssignmentFailedException: If unassignment fails
        """
        # Check if assignment exists
        is_assigned = self.repository.is_llm_assigned_to_user(user_id, llm_config_id)
        if not is_assigned:
            raise AssignmentNotFoundException("LLM assignment not found for user")

        # Remove assignment
        success = self.repository.unassign_llm_from_user(user_id, llm_config_id)

        if not success:
            raise AssignmentFailedException("Failed to remove LLM assignment")

        logger.info(f"LLM '{llm_config_id}' unassigned from user {user_id}")
        return {"user_id": user_id, "llm_config_id": llm_config_id}

    def get_user_llm_assignments(self, user_id: str) -> Dict[str, Any]:
        """Get all LLM configurations assigned to a user.

        Args:
            user_id: User ID

        Returns:
            Dict with user_id and llm_configs list
        """
        user_configs = self.repository.get_user_llm_configurations(user_id)

        llm_configs = []
        for config in user_configs.values():
            llm_configs.append(LLMConfigResponse(
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
                is_default=False
            ))

        return {
            "user_id": user_id,
            "llm_configs": [config.model_dump() for config in llm_configs]
        }

    def get_llm_assignments(self, llm_config_id: str) -> Dict[str, Any]:
        """List the users directly assigned to an LLM configuration.

        Args:
            llm_config_id: LLM configuration ID

        Returns:
            Dict with llm_config_id and assignments list

        Raises:
            ConfigurationNotFoundException: If LLM config not found
        """
        if not self.repository.get_configuration(llm_config_id):
            raise ConfigurationNotFoundException(
                f"LLM configuration '{llm_config_id}' not found"
            )

        user_ids = self.repository.get_llm_users(llm_config_id)
        return {
            "llm_config_id": llm_config_id,
            "assignments": [{"user_id": user_id} for user_id in user_ids]
        }

    def get_assignment_summary(self) -> Dict[str, Dict[str, int]]:
        """Direct-user and group assignment counts, keyed by llm_config_id."""
        return self.repository.get_llm_assignment_summary()

    def get_all_user_llm_assignments(self) -> Dict[str, Any]:
        """Get all user LLM assignments (admin only).

        Returns:
            Dict with assignments list
        """
        assignments = self.repository.get_all_user_llm_assignments()
        all_configs = self.repository.get_all_configurations()

        formatted_assignments = []
        for user_id, llm_config_ids in assignments.items():
            user_llm_configs = []
            for config_id in llm_config_ids:
                if config_id in all_configs:
                    config = all_configs[config_id]
                    user_llm_configs.append(LLMConfigResponse(
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
                        is_default=False
                    ))

            formatted_assignments.append({
                "user_id": user_id,
                "llm_configs": [config.model_dump() for config in user_llm_configs]
            })

        return {"assignments": formatted_assignments}
