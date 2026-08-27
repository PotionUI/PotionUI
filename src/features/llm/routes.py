"""
LLM Controller - thin layer for HTTP handling.

Pure reads (configuration list/detail, user/config assignment listings, the
assignment summary) go straight to `LLMRepository` and the llm `mappers`;
mutations (create/update/delete/set-default, generation, assign/unassign) go
through `src.features.llm.operations`. See
`src/features/plugins/operations/` for the reference shape.
"""

import logging
from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.llm.dto import (
    LLMConfigRequest,
    LLMGenerateRequest,
    UserLLMAssignmentRequest,
)
from src.features.llm import (
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
from src.features.llm import operations
from src.features.llm.mappers import config_to_response, assignment_config_to_response
from src.features.llm.repository import LLMRepository
from src.features.llm.gateway import LLMGateway
from src.platform.plugins import PluginRegistry
from src.platform.settings.settings import Settings
from src.platform.security.user import User, AccountType

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer
    from src.features.downloads import DownloadQueue
    from src.features.llm.tools.governance import ToolGovernanceRepository

logger = logging.getLogger(__name__)


class LLMController(BaseController):
    """Controller for LLM operations: configuration, generation, assignments."""

    def __init__(
        self,
        llm_repository: LLMRepository,
        llm_service: LLMGateway,
        settings: Settings,
        plugin_registry: PluginRegistry,
        tool_governance_repository: Optional["ToolGovernanceRepository"] = None,
        download_queue: Optional["DownloadQueue"] = None,
    ):
        super().__init__()
        self.repository = llm_repository
        self.llm_service = llm_service
        self.settings = settings
        self.plugins = plugin_registry
        self.tool_governance_repository = tool_governance_repository
        # Optional: only needed for the gemma3 chat-tokenizer on-demand fetch —
        # every other endpoint works without it.
        self.download_queue = download_queue

    # =========================================================================
    # Configuration Endpoints
    # =========================================================================

    def get_all_configurations(self) -> APIResponse:
        """Get all LLM configurations."""
        try:
            configs = self.repository.get_all_configurations()
            default_provider = self.repository.default_provider
            data = {
                "configurations": [
                    config_to_response(config, config_id == default_provider)
                    for config_id, config in configs.items()
                ],
                "default_provider": default_provider,
            }
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error getting configurations: {e}")
            return self.error_api_response(
                error="get_configurations_failed",
                message=f"Failed to get LLM configurations: {str(e)}"
            )

    def list_native_checkpoints(self) -> APIResponse:
        """List HF-layout checkpoints under models/llm/, for the 'native'
        provider's config.model picker."""
        try:
            from dataclasses import asdict

            from src.features.llm.native_library import list_native_checkpoints

            entries = [asdict(e) for e in list_native_checkpoints()]
            return self.success_response(data=entries)
        except Exception as e:
            logger.exception(f"Error listing native LLM checkpoints: {e}")
            return self.error_api_response(
                error="list_native_checkpoints_failed",
                message=f"Failed to list native LLM checkpoints: {str(e)}"
            )

    async def fetch_gemma3_chat_tokenizer(self) -> APIResponse:
        """Fetch the gemma3 chat-tokenizer assets (tokenizer.json +
        tokenizer_config.json) that lift the "tokenizer assets not downloaded
        yet" gate on a gemma3 TE-adoption candidate. Blocking (goes
        through the core download queue's synchronous HF-repo fetch), so it
        runs off the event loop; landed next to the TE depot — see
        ``native_te_adoption.ensure_gemma3_chat_tokenizer``. A subsequent
        ``GET /native/checkpoints`` reflects the lifted gate."""
        if self.download_queue is None:
            return self.error_api_response(
                error="download_queue_unavailable",
                message="Native LLM provider: no download manager available yet "
                        "(the app container hasn't finished composing)",
            )
        try:
            import asyncio

            from src.features.llm.native_te_adoption import ensure_gemma3_chat_tokenizer

            path = await asyncio.to_thread(ensure_gemma3_chat_tokenizer, self.download_queue)
            return self.success_response(
                data={"path": str(path)},
                message="gemma3 chat tokenizer assets fetched",
            )
        except Exception as e:
            logger.exception(f"Error fetching gemma3 chat tokenizer assets: {e}")
            return self.error_api_response(
                error="fetch_gemma3_chat_tokenizer_failed",
                message=f"Failed to fetch gemma3 chat tokenizer assets: {str(e)}"
            )

    def get_configuration(self, config_id: str) -> APIResponse:
        """Get a specific LLM configuration."""
        try:
            config = self.repository.get_configuration(config_id)
            if not config:
                raise ConfigurationNotFoundException(
                    f"LLM configuration '{config_id}' not found"
                )
            response = config_to_response(config, config_id == self.repository.default_provider)
            return self.success_response(data=response)
        except ConfigurationNotFoundException as e:
            return self.error_api_response(
                error="configuration_not_found",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error getting configuration: {e}")
            return self.error_api_response(
                error="get_configuration_failed",
                message=f"Failed to get LLM configuration: {str(e)}"
            )

    def create_configuration(self, request: LLMConfigRequest) -> APIResponse:
        """Create a new LLM configuration."""
        try:
            config_id = operations.create_configuration(self.repository, self.plugins, request)
            return self.success_response(
                data={"id": config_id},
                message=f"LLM configuration '{config_id}' created successfully"
            )
        except ConfigurationExistsException as e:
            return self.error_api_response(
                error="configuration_already_exists",
                message=str(e)
            )
        except ConfigurationCreationFailedException as e:
            return self.error_api_response(
                error="create_configuration_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error creating configuration: {e}")
            return self.error_api_response(
                error="create_configuration_failed",
                message=f"Failed to create LLM configuration: {str(e)}"
            )

    def update_configuration(self, config_id: str, request: LLMConfigRequest) -> APIResponse:
        """Update an existing LLM configuration."""
        try:
            updated_id = operations.update_configuration(self.repository, self.plugins, config_id, request)
            return self.success_response(
                data={"id": updated_id},
                message=f"LLM configuration '{updated_id}' updated successfully"
            )
        except ConfigurationNotFoundException as e:
            return self.error_api_response(
                error="configuration_not_found",
                message=str(e)
            )
        except ConfigurationUpdateFailedException as e:
            return self.error_api_response(
                error="update_configuration_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error updating configuration: {e}")
            return self.error_api_response(
                error="update_configuration_failed",
                message=f"Failed to update LLM configuration: {str(e)}"
            )

    def delete_configuration(self, config_id: str) -> APIResponse:
        """Delete an LLM configuration."""
        try:
            deleted_id = operations.delete_configuration(
                self.repository, self.plugins, self.tool_governance_repository, config_id
            )
            return self.success_response(
                data={"id": deleted_id},
                message=f"LLM configuration '{deleted_id}' deleted successfully"
            )
        except ConfigurationNotFoundException as e:
            return self.error_api_response(
                error="configuration_not_found",
                message=str(e)
            )
        except CannotDeleteDefaultConfigException as e:
            return self.error_api_response(
                error="cannot_delete_default",
                message=str(e)
            )
        except ConfigurationDeletionFailedException as e:
            return self.error_api_response(
                error="delete_configuration_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error deleting configuration: {e}")
            return self.error_api_response(
                error="delete_configuration_failed",
                message=f"Failed to delete LLM configuration: {str(e)}"
            )

    def set_default_provider(self, config_id: str) -> APIResponse:
        """Set the default LLM provider."""
        try:
            default_id = operations.set_default_provider(self.repository, config_id)
            return self.success_response(
                data={"default_provider": default_id},
                message=f"Default LLM provider set to '{default_id}'"
            )
        except ConfigurationNotFoundException as e:
            return self.error_api_response(
                error="configuration_not_found",
                message=str(e)
            )
        except ConfigurationUpdateFailedException as e:
            return self.error_api_response(
                error="set_default_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error setting default provider: {e}")
            return self.error_api_response(
                error="set_default_failed",
                message=f"Failed to set default LLM provider: {str(e)}"
            )

    async def test_configuration(self, config_id: str) -> APIResponse:
        """Test an LLM configuration."""
        try:
            config = self.repository.get_configuration(config_id)
            if not config:
                raise ConfigurationNotFoundException(
                    f"LLM configuration '{config_id}' not found"
                )
            result = await self.llm_service.test_configuration(config)
            if result["success"]:
                return self.success_response(
                    data=result,
                    message=f"LLM configuration '{config_id}' test successful"
                )
            else:
                return self.error_api_response(
                    error="test_failed",
                    message=f"LLM configuration test failed: {result.get('error', 'Unknown error')}"
                )
        except ConfigurationNotFoundException as e:
            return self.error_api_response(
                error="configuration_not_found",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error testing configuration: {e}")
            return self.error_api_response(
                error="test_failed",
                message=f"Failed to test LLM configuration: {str(e)}"
            )

    # =========================================================================
    # Generation Endpoint
    # =========================================================================

    async def generate_response(self, request: LLMGenerateRequest, user: User) -> APIResponse:
        """Generate a response using an LLM."""
        try:
            response = await operations.generate_response(
                self.repository, self.llm_service, self.settings, self.plugins, request, user.id
            )
            return self.success_response(data=response)
        except ConfigurationNotFoundException as e:
            return self.error_api_response(
                error="configuration_not_found",
                message=str(e)
            )
        except VisionNotSupportedException as e:
            return self.error_api_response(
                error="vision_not_supported",
                message=str(e)
            )
        except ImageLoadFailedException as e:
            return self.error_api_response(
                error="image_load_failed",
                message=str(e)
            )
        except GenerationFailedException as e:
            return self.error_api_response(
                error="generation_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error generating response: {e}")
            return self.error_api_response(
                error="generation_failed",
                message=f"Failed to generate response: {str(e)}"
            )

    # =========================================================================
    # User LLM Assignment Endpoints
    # =========================================================================

    def assign_llm_to_user(self, request: UserLLMAssignmentRequest) -> APIResponse:
        """Assign an LLM configuration to a user."""
        try:
            result = operations.assign_llm_to_user(self.repository, request.user_id, request.llm_config_id)
            return self.success_response(
                data=result,
                message="LLM assigned to user successfully"
            )
        except ConfigurationNotFoundException as e:
            return self.error_api_response(
                error="llm_config_not_found",
                message=str(e)
            )
        except AssignmentFailedException as e:
            return self.error_api_response(
                error="assignment_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error assigning LLM to user: {e}")
            return self.error_api_response(
                error="assignment_failed",
                message=f"Failed to assign LLM to user: {str(e)}"
            )

    def unassign_llm_from_user(self, user_id: str, llm_config_id: str) -> APIResponse:
        """Remove LLM configuration assignment from a user."""
        try:
            result = operations.unassign_llm_from_user(self.repository, user_id, llm_config_id)
            return self.success_response(
                data=result,
                message="LLM assignment removed successfully"
            )
        except AssignmentNotFoundException as e:
            return self.error_api_response(
                error="assignment_not_found",
                message=str(e)
            )
        except AssignmentFailedException as e:
            return self.error_api_response(
                error="unassignment_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error unassigning LLM from user: {e}")
            return self.error_api_response(
                error="unassignment_failed",
                message=f"Failed to remove LLM assignment: {str(e)}"
            )

    def get_user_llm_assignments(self, user_id: str) -> APIResponse:
        """Get all LLM configurations assigned to a user."""
        try:
            user_configs = self.repository.get_user_llm_configurations(user_id)
            llm_configs = [assignment_config_to_response(c) for c in user_configs.values()]
            data = {
                "user_id": user_id,
                "llm_configs": [c.model_dump() for c in llm_configs],
            }
            return self.success_response(
                data=data,
                message=f"Found {len(data.get('llm_configs', []))} LLM configurations for user"
            )
        except Exception as e:
            logger.exception(f"Error getting user LLM assignments: {e}")
            return self.error_api_response(
                error="get_assignments_failed",
                message=f"Failed to get user LLM assignments: {str(e)}"
            )

    def get_all_user_llm_assignments(self) -> APIResponse:
        """Get all user LLM assignments (admin only)."""
        try:
            assignments = self.repository.get_all_user_llm_assignments()
            all_configs = self.repository.get_all_configurations()

            formatted_assignments = []
            for user_id, llm_config_ids in assignments.items():
                user_llm_configs = [
                    assignment_config_to_response(all_configs[config_id])
                    for config_id in llm_config_ids
                    if config_id in all_configs
                ]
                formatted_assignments.append({
                    "user_id": user_id,
                    "llm_configs": [c.model_dump() for c in user_llm_configs],
                })

            data = {"assignments": formatted_assignments}
            return self.success_response(
                data=data,
                message=f"Found {len(data.get('assignments', []))} user LLM assignments"
            )
        except Exception as e:
            logger.exception(f"Error getting all user LLM assignments: {e}")
            return self.error_api_response(
                error="get_all_assignments_failed",
                message=f"Failed to get all user LLM assignments: {str(e)}"
            )

    def get_llm_assignments(self, config_id: str) -> APIResponse:
        """Get the users directly assigned to an LLM configuration (admin only)."""
        try:
            if not self.repository.get_configuration(config_id):
                raise ConfigurationNotFoundException(
                    f"LLM configuration '{config_id}' not found"
                )
            user_ids = self.repository.get_llm_users(config_id)
            data = {
                "llm_config_id": config_id,
                "assignments": [{"user_id": user_id} for user_id in user_ids],
            }
            return self.success_response(data=data)
        except ConfigurationNotFoundException as e:
            return self.error_api_response(
                error="configuration_not_found",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error getting LLM assignments: {e}")
            return self.error_api_response(
                error="get_assignments_failed",
                message=f"Failed to get LLM assignments: {str(e)}"
            )

    def get_llm_assignment_summary(self) -> APIResponse:
        """Direct-user and group assignment counts for every LLM configuration (admin only)."""
        try:
            data = self.repository.get_llm_assignment_summary()
            return self.success_response(data=data)
        except Exception as e:
            logger.exception(f"Error getting LLM assignment summary: {e}")
            return self.error_api_response(
                error="get_assignment_summary_failed",
                message=f"Failed to get LLM assignment summary: {str(e)}"
            )


# ============================================================================
# Route Handlers
# ============================================================================

def build_router(container: "AppContainer") -> APIRouter:
    controller = container.llm_controller
    router = APIRouter(prefix="/api/llm", tags=["LLM"])

    # LLM Configuration routes
    @router.get("/configurations", response_model=APIResponse, summary="Get LLM Configurations")
    async def get_llm_configurations(current_user: User = Depends(get_current_admin_user)):
        """Get all configured LLM providers and their settings."""
        return controller.get_all_configurations()

    @router.get("/native/checkpoints", response_model=APIResponse, summary="List Native LLM Checkpoints")
    async def list_native_llm_checkpoints(current_user: User = Depends(get_current_admin_user)):
        """List HF-layout checkpoints available to the 'native' LLM provider."""
        return controller.list_native_checkpoints()

    @router.post(
        "/native/checkpoints/gemma3-tokenizer/fetch", response_model=APIResponse,
        summary="Fetch Gemma-3 Chat Tokenizer Assets",
    )
    async def fetch_gemma3_chat_tokenizer(current_user: User = Depends(get_current_admin_user)):
        """Fetch the gemma3 chat-tokenizer assets that lift a gemma3 TE-adoption
        candidate's "not downloaded yet" gate."""
        return await controller.fetch_gemma3_chat_tokenizer()

    @router.get("/configurations/my", response_model=APIResponse, summary="Get My LLM Configurations")
    async def get_my_llm_configurations(current_user: User = Depends(get_current_active_user)):
        """Get LLM configurations assigned to the current user."""
        return controller.get_user_llm_assignments(current_user.id)

    @router.get("/configurations/{config_id}", response_model=APIResponse, summary="Get LLM Configuration")
    async def get_llm_configuration(config_id: str, current_user: User = Depends(get_current_admin_user)):
        """Get detailed configuration for a specific LLM provider."""
        return controller.get_configuration(config_id)

    @router.post("/configurations", response_model=APIResponse, summary="Create LLM Configuration")
    async def create_llm_configuration(request: LLMConfigRequest, current_user: User = Depends(get_current_admin_user)):
        """Create a new LLM provider configuration with API keys and settings."""
        return controller.create_configuration(request)

    @router.put("/configurations/{config_id}", response_model=APIResponse, summary="Update LLM Configuration")
    async def update_llm_configuration(config_id: str, request: LLMConfigRequest, current_user: User = Depends(get_current_admin_user)):
        """Update an existing LLM provider configuration."""
        return controller.update_configuration(config_id, request)

    @router.delete("/configurations/{config_id}", response_model=APIResponse, summary="Delete LLM Configuration")
    async def delete_llm_configuration(config_id: str, current_user: User = Depends(get_current_admin_user)):
        """Delete an LLM provider configuration."""
        return controller.delete_configuration(config_id)

    @router.post("/configurations/{config_id}/set-default", response_model=APIResponse, summary="Set Default LLM Provider")
    async def set_default_llm_provider(config_id: str, current_user: User = Depends(get_current_admin_user)):
        """Set a specific LLM configuration as the default provider."""
        return controller.set_default_provider(config_id)

    @router.post("/configurations/{config_id}/test", response_model=APIResponse, summary="Test LLM Configuration")
    async def test_llm_configuration(config_id: str, current_user: User = Depends(get_current_admin_user)):
        """Test connectivity and functionality of an LLM configuration."""
        return await controller.test_configuration(config_id)

    # Generation route
    @router.post("/generate", response_model=APIResponse, summary="Generate LLM Response")
    async def generate_llm_response(request: LLMGenerateRequest, current_user: User = Depends(get_current_active_user)):
        """Generate a text response using the configured LLM provider."""
        return await controller.generate_response(request, current_user)

    # User LLM assignment routes (admin only)
    @router.post("/user-assignments", response_model=APIResponse, summary="Assign LLM to User")
    async def assign_llm_to_user(request: UserLLMAssignmentRequest, current_user: User = Depends(get_current_admin_user)):
        """Assign an LLM configuration to a user (admin only)."""
        return controller.assign_llm_to_user(request)

    @router.delete("/user-assignments/{user_id}/{llm_config_id}", response_model=APIResponse, summary="Unassign LLM from User")
    async def unassign_llm_from_user(user_id: str, llm_config_id: str, current_user: User = Depends(get_current_admin_user)):
        """Remove LLM configuration assignment from a user (admin only)."""
        return controller.unassign_llm_from_user(user_id, llm_config_id)

    @router.get("/user-assignments/{user_id}", response_model=APIResponse, summary="Get User LLM Assignments")
    async def get_user_llm_assignments(user_id: str, current_user: User = Depends(get_current_active_user)):
        """Get all LLM configurations assigned to a specific user.

        A user may read their own assignments; only administrators may read
        another user's.
        """
        if current_user.account_type != AccountType.ADMIN and current_user.id != user_id:
            raise HTTPException(status_code=403, detail="Administrator privileges required")
        return controller.get_user_llm_assignments(user_id)

    @router.get("/user-assignments", response_model=APIResponse, summary="Get All User LLM Assignments")
    async def get_all_user_llm_assignments(current_user: User = Depends(get_current_admin_user)):
        """Get all user LLM assignments (admin only)."""
        return controller.get_all_user_llm_assignments()

    @router.get("/assignment-summary", response_model=APIResponse, summary="Get LLM Assignment Summary")
    async def get_llm_assignment_summary(current_user: User = Depends(get_current_admin_user)):
        """Direct-user and group assignment counts for every LLM configuration (admin only)."""
        return controller.get_llm_assignment_summary()

    @router.get("/configurations/{config_id}/assignments", response_model=APIResponse, summary="Get LLM Configuration Assignments")
    async def get_llm_configuration_assignments(config_id: str, current_user: User = Depends(get_current_admin_user)):
        """Get the users directly assigned to an LLM configuration (admin only)."""
        return controller.get_llm_assignments(config_id)

    return router
