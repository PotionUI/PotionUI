"""
Preset controller for handling preset-related API endpoints.

This controller is responsible for:
- Route definitions and HTTP method handling
- Request validation and parameter extraction
- Exception-to-HTTP-status mapping
- Response formatting

All business logic is delegated to PresetManager.
"""

from typing import Dict, Any, Optional, TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.backends.backend_registry import BackendRegistry
from src.features.forms.exceptions import FormNotFoundException
from src.features.presets.manager import PresetManager
from src.features.presets.exceptions import (
    PresetNotFoundException,
    ModeNotFoundException,
    NoModesAvailableException,
    PresetNotInstalledException,
    PresetAlreadyInstalledException,
    PresetNotAssignedException,
    UserNotFoundException,
    InvalidUsersException,
    PermissionDeniedException,
    InvalidModeDataException,
    InvalidConfigurationException,
    InvalidFormOverridesException,
)
from src.platform.security.user import User, AccountType

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer
    from src.features.models.access_policy import ModelAccessPolicy


class PresetController(BaseController):
    """Controller for preset endpoints - delegates to PresetManager."""

    def __init__(
        self,
        preset_manager: PresetManager,
        backend_registry: BackendRegistry,
        media_manager: Optional["MediaManager"] = None,
        model_access_policy: Optional["ModelAccessPolicy"] = None,
    ):
        super().__init__()
        self.manager = preset_manager
        self.backend_registry = backend_registry
        # Only used to reclaim a reloaded preset's rendered thumbnails. Optional so
        # PresetManager stays free of any media dependency.
        self.media_manager = media_manager
        # Scopes GET .../models to the requesting user's assigned models.
        # Optional (defaults to unfiltered) only so tests can construct this
        # controller without the full container; the composition root always
        # supplies it.
        self.model_access_policy = model_access_policy

    def _handle_preset_exception(self, e: Exception, error_code: str, message: str):
        """Convert domain exceptions to API responses.

        Uses error_api_response for domain exceptions (returns APIResponse with success=False)
        and handle_exception for unexpected errors (raises HTTPException).
        """
        if isinstance(e, PresetNotFoundException):
            return self.error_api_response(
                error="preset_not_found",
                message=str(e)
            )
        elif isinstance(e, ModeNotFoundException):
            return self.error_api_response(
                error="mode_not_found",
                message=str(e)
            )
        elif isinstance(e, FormNotFoundException):
            return self.error_api_response(
                error="form_not_found",
                message=str(e)
            )
        elif isinstance(e, NoModesAvailableException):
            return self.error_api_response(
                error="no_modes_available",
                message=str(e)
            )
        elif isinstance(e, PresetNotInstalledException):
            return self.error_api_response(
                error="preset_not_installed",
                message=str(e)
            )
        elif isinstance(e, PresetAlreadyInstalledException):
            return self.error_api_response(
                error="preset_already_installed",
                message=str(e)
            )
        elif isinstance(e, PresetNotAssignedException):
            return self.error_api_response(
                error="preset_not_assigned",
                message=str(e)
            )
        elif isinstance(e, UserNotFoundException):
            return self.error_api_response(
                error="user_not_found",
                message=str(e)
            )
        elif isinstance(e, InvalidUsersException):
            return self.error_api_response(
                error="invalid_users",
                message=str(e)
            )
        elif isinstance(e, PermissionDeniedException):
            return self.error_api_response(
                error="permission_denied",
                message=str(e)
            )
        elif isinstance(e, InvalidModeDataException):
            return self.error_api_response(
                error="invalid_mode_data",
                message=str(e)
            )
        elif isinstance(e, InvalidConfigurationException):
            return self.error_api_response(
                error="invalid_configuration",
                message=str(e)
            )
        elif isinstance(e, InvalidFormOverridesException):
            return self.error_api_response(
                error="invalid_form_overrides",
                message=str(e)
            )
        else:
            raise self.handle_exception(e, error_code, message)

    async def list_presets(
        self,
        current_user: User,
        include_uninstalled: bool = False
    ) -> APIResponse:
        """Get list of available presets."""
        try:
            data = self.manager.list_presets(current_user, include_uninstalled)
            return self.success_response(data=data)
        except Exception as e:
            raise self.handle_exception(e, "preset_list_failed", "Failed to list presets")

    async def get_preset(self, preset_id: str) -> APIResponse:
        """Get specific preset information."""
        try:
            data = self.manager.get_preset(preset_id)
            return self.success_response(data=data)
        except Exception as e:
            return self._handle_preset_exception(e, "preset_get_failed", "Failed to get preset")

    async def get_preset_models(
        self,
        preset_id: str,
        model_type: Optional[str] = None,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        tag_ids: Optional[str] = None,
        any_tag_ids: Optional[str] = None,
        favorites_only: bool = False,
        user_id: Optional[str] = None,
        admin: bool = False,
        current_user: Optional[User] = None,
    ) -> APIResponse:
        """Models this preset could actually load, with the backends that hold each.

        Sourced from availability rather than from this host's filesystem: a ComfyUI
        server's models are named, not pathed, and need never exist locally. Scoping to
        the union across the engine's backends (instead of to one backend) keeps
        selection from being circular - see docs/models.md.

        Also scoped to `current_user`'s model access (STRICT: a
        non-admin with no assignments sees nothing) when `model_access_policy`
        was supplied to this controller; admins are always unrestricted.
        """
        from src.features.models.availability import models_for_engine

        try:
            preset = self.manager.preset_loader.load_preset_by_id(preset_id)
            if not preset:
                return self.error_response(
                    error="preset_not_found",
                    message=f"Preset '{preset_id}' not found",
                    status_code=404
                )

            from src.features.models.availability_repository import (
                model_availability_repo,
            )

            user_allowed_model_ids = None
            if self.model_access_policy is not None and current_user is not None:
                # all_models=True: an admin gets None (unrestricted); everyone
                # else gets their real (possibly empty) assigned-model list
                # regardless of the flag - see ModelAccessPolicy.get_allowed_model_ids.
                user_allowed_model_ids = self.model_access_policy.get_allowed_model_ids(
                    current_user, all_models=True
                )

            # Every filter is applied server-side, alongside availability, so a page is a
            # page: filtering a LIMITed result client-side would drop rows that belong on it.
            models = models_for_engine(
                engine=preset.engine,
                backend_registry=self.backend_registry,
                model_type=model_type,
                search=search,
                limit=limit,
                offset=offset,
                tag_ids=tag_ids.split(",") if tag_ids else None,
                # OR semantics - see model_repository.get_all's docstring. Fed by a
                # `model` field's resolved `filter_tags:` (src/features/fields/model.py).
                any_tag_ids=any_tag_ids.split(",") if any_tag_ids else None,
                favorites_only=favorites_only,
                library_user_id=user_id,
                admin=admin,
                user_allowed_model_ids=user_allowed_model_ids,
            )

            backend_ids = [
                b.backend_id
                for b in self.backend_registry.get_backends_for_engine(preset.engine)
            ]

            return self.success_response(data={
                "engine": preset.engine,
                "models": models,
                "total": len(models),
                # Admin-only. False means the list is unfiltered because nothing has been
                # indexed; `backend_ids` on each model will be empty and must not be read
                # as "no backend has this".
                "indexed": model_availability_repo.any_indexed(backend_ids) if admin else None,
            })

        except HTTPException:
            raise
        except Exception as e:
            return self._handle_preset_exception(
                e, "preset_models_failed", "Failed to list models for preset"
            )

    async def get_available_modes(self, preset_id: str) -> APIResponse:
        """Get available modes for a specific preset."""
        try:
            data = self.manager.get_available_modes(preset_id)
            return self.success_response(data=data)
        except Exception as e:
            return self._handle_preset_exception(e, "modes_get_failed", "Failed to get modes")

    async def get_preset_form_schema(
        self,
        preset_id: str,
        mode: str = None,
        form_name: str = None
    ) -> APIResponse:
        """Get form schema for a specific preset and mode."""
        try:
            data = self.manager.get_form_schema(preset_id, mode, form_name)
            return self.success_response(data=data)
        except Exception as e:
            return self._handle_preset_exception(e, "form_schema_failed", "Failed to get form schema")

    async def get_pipes(
        self,
        preset_id: str,
        mode: str = "txt2img",
        form_data: dict = None
    ) -> APIResponse:
        """Get pipe configuration and connections for a specific preset."""
        try:
            result = self.manager.get_pipeline(preset_id, mode, form_data)
            return self.success_response(data=result.to_dict())
        except Exception as e:
            return self._handle_preset_exception(e, "pipes_get_failed", "Failed to get pipes")

    async def reload_preset(self, preset_id: str) -> APIResponse:
        """Reload a preset from disk and return its current state."""
        try:
            data = self.manager.reload_preset(preset_id)
            # Rendered thumbnails are keyed by source mtime, so a stale one can never
            # be served; this only reclaims renders whose source was renamed or removed.
            if self.media_manager is not None:
                self.media_manager.purge_preset_thumbnail_cache(preset_id)
            return self.success_response(
                data=data,
                message=f"Preset '{preset_id}' reloaded successfully"
            )
        except Exception as e:
            return self._handle_preset_exception(e, "preset_reload_failed", "Failed to reload preset")

    async def install_preset(self, preset_id: str, current_user: User) -> APIResponse:
        """Install a preset (admin only)."""
        try:
            data = self.manager.install_preset(preset_id, current_user)
            return self.success_response(
                data=data,
                message=f"Preset '{preset_id}' installed successfully"
            )
        except Exception as e:
            return self._handle_preset_exception(e, "preset_install_failed", "Failed to install preset")

    async def uninstall_preset(self, preset_id: str, current_user: User) -> APIResponse:
        """Uninstall a preset (admin only) - removes all user assignments."""
        try:
            message = self.manager.uninstall_preset(preset_id, current_user)
            return self.success_response(message=message)
        except Exception as e:
            return self._handle_preset_exception(e, "preset_uninstall_failed", "Failed to uninstall preset")

    async def assign_preset_to_users(
        self,
        preset_id: str,
        user_ids: list,
        current_user: User
    ) -> APIResponse:
        """Assign a preset to multiple users (admin only)."""
        try:
            data = self.manager.assign_preset_to_users(preset_id, user_ids, current_user)
            return self.success_response(
                data=data,
                message=f"Preset '{preset_id}' assigned to {data['assigned_count']} users"
            )
        except Exception as e:
            return self._handle_preset_exception(e, "preset_assignment_failed", "Failed to assign preset")

    async def unassign_preset_from_user(
        self,
        preset_id: str,
        user_id: str,
        current_user: User
    ) -> APIResponse:
        """Unassign a preset from a user (admin only)."""
        try:
            message = self.manager.unassign_preset_from_user(preset_id, user_id, current_user)
            return self.success_response(message=message)
        except Exception as e:
            return self._handle_preset_exception(e, "preset_unassignment_failed", "Failed to unassign preset")

    async def get_preset_assignments(self, preset_id: str, current_user: User) -> APIResponse:
        """Get assignment summary for a preset (admin only)."""
        try:
            data = self.manager.get_preset_assignments(preset_id, current_user)
            return self.success_response(data=data)
        except Exception as e:
            return self._handle_preset_exception(e, "preset_assignments_failed", "Failed to get preset assignments")

    async def get_preset_configuration(self, preset_id: str) -> APIResponse:
        """Get a preset's declared configuration schema merged with its stored values."""
        try:
            data = self.manager.get_preset_configuration(preset_id)
            return self.success_response(data=data)
        except Exception as e:
            return self._handle_preset_exception(
                e, "preset_configuration_get_failed", "Failed to get preset configuration"
            )

    async def set_preset_configuration(
        self,
        preset_id: str,
        values: Dict[str, Any],
        current_user: User
    ) -> APIResponse:
        """Set admin-set configuration values for a preset (admin only)."""
        try:
            data = self.manager.set_preset_configuration(preset_id, values, current_user)
            return self.success_response(data=data)
        except Exception as e:
            return self._handle_preset_exception(
                e, "preset_configuration_set_failed", "Failed to set preset configuration"
            )

    async def get_form_overrides(
        self,
        preset_id: str,
        mode: Optional[str],
        current_user: User,
    ) -> APIResponse:
        """Get a mode's unmerged field inventory and current admin overrides (admin only)."""
        try:
            data = self.manager.get_form_overrides_inventory(preset_id, mode, current_user)
            return self.success_response(data=data)
        except Exception as e:
            return self._handle_preset_exception(
                e, "form_overrides_get_failed", "Failed to get form overrides"
            )

    async def set_form_overrides(
        self,
        preset_id: str,
        mode: str,
        overrides: Dict[str, Any],
        current_user: User,
    ) -> APIResponse:
        """Set admin per-field form overrides for one mode of a preset (admin only)."""
        try:
            data = self.manager.set_form_overrides(preset_id, mode, overrides, current_user)
            return self.success_response(data=data)
        except Exception as e:
            return self._handle_preset_exception(
                e, "form_overrides_set_failed", "Failed to set form overrides"
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.preset_controller
    router = APIRouter(prefix="/api/presets", tags=["Presets"])

    @router.get("", response_model=APIResponse, summary="List Presets")
    async def list_presets(
        include_uninstalled: bool = False,
        current_user: User = Depends(get_current_active_user)
    ):
        """List presets. For admin users with include_uninstalled=True, shows all presets from files with installation status.
        Otherwise, shows only installed presets for the user."""

        # Only allow include_uninstalled for admin users
        if include_uninstalled and current_user.account_type != AccountType.ADMIN:
            include_uninstalled = False

        return await controller.list_presets(current_user, include_uninstalled)

    @router.get("/{preset_id}", response_model=APIResponse, summary="Get Preset Details")
    async def get_preset(preset_id: str, current_user=Depends(get_current_active_user)):
        """Get detailed information about a specific preset including its configuration."""
        return await controller.get_preset(preset_id)

    @router.get("/{preset_id}/models", response_model=APIResponse, summary="Get Preset Models")
    async def get_preset_models(
        preset_id: str,
        model_type: Optional[str] = None,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        tag_ids: Optional[str] = None,
        any_tag_ids: Optional[str] = None,
        favorites_only: bool = False,
        current_user=Depends(get_current_active_user)
    ):
        """List models loadable by this preset's engine, each badged with the backends holding it.

        `tag_ids` requires ALL listed tags (comma-separated, AND). `any_tag_ids` requires AT
        LEAST ONE (comma-separated, OR) - the shape a `model` field's resolved `filter_tags:`
        should be sent as.
        """
        from src.platform.security.user import AccountType
        is_admin = getattr(current_user, "account_type", None) == AccountType.ADMIN
        return await controller.get_preset_models(
            preset_id, model_type, search, limit, offset,
            tag_ids, any_tag_ids, favorites_only, getattr(current_user, "id", None), is_admin,
            current_user=current_user,
        )

    @router.get("/{preset_id}/modes", response_model=APIResponse, summary="Get Preset Modes")
    async def get_preset_modes(preset_id: str, current_user=Depends(get_current_active_user)):
        """Get available generation modes (txt2img, img2img, etc.) for a specific preset."""
        return await controller.get_available_modes(preset_id)

    @router.get("/{preset_id}/form", response_model=APIResponse, summary="Get Form Schema")
    async def get_preset_form_schema(preset_id: str, mode: str = None, form_name: str = None, current_user=Depends(get_current_active_user)):
        """Get the dynamic form schema for a preset, including field definitions and validation rules."""
        return await controller.get_preset_form_schema(preset_id, mode, form_name)

    @router.get("/{preset_id}/pipes", response_model=APIResponse, summary="Get Pipeline Configuration")
    async def get_preset_pipes(preset_id: str, mode: str = "txt2img", current_user=Depends(get_current_active_user)):
        """Get the pipeline configuration and pipe connections for a preset and mode."""
        return await controller.get_pipes(preset_id, mode)

    @router.post("/{preset_id}/pipes", response_model=APIResponse, summary="Get Pipeline with Form Data")
    async def get_preset_pipes_with_form_data(preset_id: str, request: Dict[str, Any], mode: str = "txt2img", current_user=Depends(get_current_active_user)):
        """Get pipeline configuration evaluated with specific form data for dynamic pipe configuration."""
        form_data = request.get('form_data', {})
        return await controller.get_pipes(preset_id, mode, form_data)

    @router.post("/{preset_id}/reload", response_model=APIResponse, summary="Reload Preset")
    async def reload_preset(preset_id: str, current_user=Depends(get_current_admin_user)):
        """Reload a preset configuration from disk and return its updated state."""
        return await controller.reload_preset(preset_id)

    @router.post("/{preset_id}/install", response_model=APIResponse, summary="Install Preset")
    async def install_preset(preset_id: str, current_user: User = Depends(get_current_active_user)):
        """Install a preset for the current user."""
        return await controller.install_preset(preset_id, current_user)

    @router.post("/{preset_id}/uninstall", response_model=APIResponse, summary="Uninstall Preset")
    async def uninstall_preset(preset_id: str, current_user: User = Depends(get_current_active_user)):
        """Uninstall a preset (admin only) - removes all user assignments."""
        return await controller.uninstall_preset(preset_id, current_user)

    @router.post("/{preset_id}/assign", response_model=APIResponse, summary="Assign Preset to Users")
    async def assign_preset_to_users(preset_id: str, request: Dict[str, Any], current_user: User = Depends(get_current_active_user)):
        """Assign a preset to multiple users (admin only)."""
        user_ids = request.get('user_ids', [])
        return await controller.assign_preset_to_users(preset_id, user_ids, current_user)

    @router.post("/{preset_id}/unassign/{user_id}", response_model=APIResponse, summary="Unassign Preset from User")
    async def unassign_preset_from_user(preset_id: str, user_id: str, current_user: User = Depends(get_current_active_user)):
        """Unassign a preset from a user (admin only)."""
        return await controller.unassign_preset_from_user(preset_id, user_id, current_user)

    @router.get("/{preset_id}/assignments", response_model=APIResponse, summary="Get Preset Assignments")
    async def get_preset_assignments(preset_id: str, current_user: User = Depends(get_current_active_user)):
        """Get assignment summary for a preset (admin only)."""
        return await controller.get_preset_assignments(preset_id, current_user)

    @router.get("/{preset_id}/configuration", response_model=APIResponse, summary="Get Preset Configuration")
    async def get_preset_configuration(preset_id: str, current_user: User = Depends(get_current_active_user)):
        """Get a preset's declared `configuration:` schema merged with its stored admin-set values."""
        return await controller.get_preset_configuration(preset_id)

    @router.put("/{preset_id}/configuration", response_model=APIResponse, summary="Set Preset Configuration")
    async def set_preset_configuration(
        preset_id: str,
        request: Dict[str, Any],
        current_user: User = Depends(get_current_active_user)
    ):
        """Set admin-set configuration values for a preset (admin only)."""
        values = request.get('values', {})
        return await controller.set_preset_configuration(preset_id, values, current_user)

    @router.get("/{preset_id}/form-overrides", response_model=APIResponse, summary="Get Preset Form Overrides")
    async def get_form_overrides(
        preset_id: str,
        mode: Optional[str] = None,
        current_user: User = Depends(get_current_active_user),
    ):
        """Get `mode`'s unmerged field inventory and current admin per-field overrides
        (admin only; defaults to the preset's first mode when `mode` is omitted)."""
        return await controller.get_form_overrides(preset_id, mode, current_user)

    @router.put("/{preset_id}/form-overrides", response_model=APIResponse, summary="Set Preset Form Overrides")
    async def set_form_overrides(
        preset_id: str,
        request: Dict[str, Any],
        current_user: User = Depends(get_current_active_user),
    ):
        """Set admin per-field form overrides for one mode of a preset (admin only).
        Sending an empty object or `null` for a field clears its override."""
        mode = request.get('mode')
        overrides = request.get('overrides', {})
        return await controller.set_form_overrides(preset_id, mode, overrides, current_user)

    return router
