"""
Preset controller for handling preset-related API endpoints.

This controller is responsible for:
- Route definitions and HTTP method handling
- Request validation and parameter extraction
- Exception-to-HTTP-status mapping
- Response formatting

All business logic is delegated to `src.features.presets.operations`.
"""

import asyncio
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.backends.backend_registry import BackendRegistry
from src.features.forms.exceptions import FormNotFoundException
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets import operations
from src.features.presets.mode_labels import MODE_LABELS
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


_PRESET_REFUSALS = {
    PresetNotFoundException: Fail("preset_not_found"),
    ModeNotFoundException: Fail("mode_not_found"),
    FormNotFoundException: Fail("form_not_found"),
    NoModesAvailableException: Fail("no_modes_available"),
    PresetNotInstalledException: Fail("preset_not_installed"),
    PresetAlreadyInstalledException: Fail("preset_already_installed"),
    PresetNotAssignedException: Fail("preset_not_assigned"),
    UserNotFoundException: Fail("user_not_found"),
    InvalidUsersException: Fail("invalid_users"),
    PermissionDeniedException: Fail("permission_denied"),
    InvalidModeDataException: Fail("invalid_mode_data"),
    InvalidConfigurationException: Fail("invalid_configuration"),
    InvalidFormOverridesException: Fail("invalid_form_overrides"),
}


def _guarded(code: str, message: str):
    return guarded(_PRESET_REFUSALS, Fail(code, message, status=500), soft=True)


class PresetController(BaseController):
    """Controller for preset endpoints - delegates to `src.features.presets.operations`."""

    def __init__(
        self,
        preset_collaborators: PresetCollaborators,
        backend_registry: BackendRegistry,
        media_store: Optional["MediaStore"] = None,
        model_access_policy: Optional["ModelAccessPolicy"] = None,
        recipe_links: Optional[Any] = None,
    ):
        super().__init__()
        self.recipe_links = recipe_links
        self.collaborators = preset_collaborators
        self.backend_registry = backend_registry
        # Only used to reclaim a reloaded preset's rendered thumbnails. Optional so
        # the presets feature stays free of any media dependency.
        self.media_store = media_store
        # Scopes GET .../models to the requesting user's assigned models.
        # Optional (defaults to unfiltered) only so tests can construct this
        # controller without the full container; the composition root always
        # supplies it.
        self.model_access_policy = model_access_policy

    def require_preset_access(self, preset_id: str, current_user: User) -> None:
        try:
            operations.require_preset_access(
                self.collaborators.db_repo, current_user.id, preset_id, operations.is_admin_user(current_user)
            )
        except PresetNotFoundException:
            self.error_response(
                error="preset_not_found",
                message=f"Preset '{preset_id}' not found",
                status_code=404,
            )

    def _supports_prompt_batch(self, engine: Optional[str]) -> bool:
        return self.backend_registry.engine_supports_prompt_batch(engine)

    def _engine_available(self, engine: Optional[str]) -> bool:
        return bool(engine) and bool(self.backend_registry.get_backends_for_engine(engine))

    def _list_presets(self, current_user: User, include_uninstalled: bool) -> List[Dict[str, Any]]:
        data = operations.list_presets(self.collaborators, current_user, include_uninstalled)
        by_preset = (
            self.recipe_links.recipes_by_preset(current_user) if self.recipe_links is not None else {}
        )
        engines: Dict[Optional[str], bool] = {}
        for preset in data:
            engine = preset.get('engine')
            if engine not in engines:
                engines[engine] = self._engine_available(engine)
            preset['recipes'] = by_preset.get(preset['id'], [])
            preset['supports_prompt_batch'] = self._supports_prompt_batch(engine)
            preset['engine_available'] = engines[engine]
        return data

    @guarded(None, Fail("preset_list_failed", "Failed to list presets", status=500))
    async def list_presets(
        self,
        current_user: User,
        include_uninstalled: bool = False
    ) -> APIResponse:
        """Get list of available presets."""
        data = await asyncio.to_thread(self._list_presets, current_user, include_uninstalled)
        return self.success_response(data=data)

    @_guarded("preset_get_failed", "Failed to get preset")
    async def get_preset(self, preset_id: str, current_user: User) -> APIResponse:
        """Get specific preset information."""
        data = operations.get_preset(self.collaborators, preset_id)
        data['recipes'] = (
            self.recipe_links.recipes_for_preset(preset_id, current_user)
            if self.recipe_links is not None
            else []
        )
        data['supports_prompt_batch'] = self._supports_prompt_batch(data.get('engine'))
        return self.success_response(data=data)

    @_guarded("requirements_check_failed", "Failed to check preset requirements")
    async def get_preset_requirements(
        self, preset_id: str, backend_id: Optional[str] = None, refresh: bool = False
    ) -> APIResponse:
        """Check this preset's declared `requirements:` against this instance (admin only)."""
        data = await operations.get_preset_requirements(
            self.collaborators, preset_id, backend_id=backend_id, refresh=refresh
        )
        recipe_id = self.recipe_links.install_recipe_id(preset_id) if self.recipe_links is not None else None
        if recipe_id:
            for item in data.get("results") or []:
                if item.get("type") == "model" and item.get("status") == "missing" and item.get("mode"):
                    item["action"] = {
                        "kind": "install_mode",
                        "payload": {"preset_id": preset_id, "mode": item["mode"], "recipe_id": recipe_id},
                    }
        return self.success_response(data=data)

    @_guarded("preset_models_failed", "Failed to list models for preset")
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
        tasks: Optional[str] = None,
        model_types: Optional[str] = None,
        unquantized_only: bool = False,
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

        preset = self.collaborators.preset_loader.load_preset_by_id(preset_id)
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
            user_allowed_model_ids = self.model_access_policy.get_allowed_model_ids(
                current_user, all_models=True
            )

        driver = preset.driver if isinstance(getattr(preset, "driver", None), str) else None
        task_list = [t.strip() for t in tasks.split(",") if t.strip()] if tasks else None
        type_list = [t.strip() for t in model_types.split(",") if t.strip()] if model_types else None
        locator = getattr(self.collaborators.model_index, "locator", None) if unquantized_only else None
        models = await asyncio.to_thread(
            models_for_engine,
            engine=preset.engine,
            driver=driver,
            tasks=task_list,
            preset_id=preset.id if isinstance(getattr(preset, "id", None), str) else None,
            backend_registry=self.backend_registry,
            model_type=model_type,
            model_types=type_list,
            unquantized_only=unquantized_only,
            path_for_model=locator.path_for_model if locator is not None else None,
            search=search,
            limit=limit,
            offset=offset,
            tag_ids=tag_ids.split(",") if tag_ids else None,
            any_tag_ids=any_tag_ids.split(",") if any_tag_ids else None,
            favorites_only=favorites_only,
            library_user_id=user_id,
            admin=admin,
            user_allowed_model_ids=user_allowed_model_ids,
        )

        backend_ids = [
            b.backend_id
            for b in self.backend_registry.get_backends_for_engine(preset.engine)
            if driver is None or b.config.effective_driver == driver
        ]

        return self.success_response(data={
            "engine": preset.engine,
            "models": models,
            "total": len(models),
            "indexed": model_availability_repo.any_indexed(backend_ids) if admin else None,
        })

    async def get_mode_labels(self) -> APIResponse:
        return self.success_response(data={"labels": dict(MODE_LABELS)})

    @_guarded("preset_picker_state_failed", "Failed to load the preset picker state")
    async def get_picker_state(self, current_user: User) -> APIResponse:
        data = await asyncio.to_thread(operations.get_picker_state, self.collaborators, current_user)
        return self.success_response(data=data)

    @_guarded("preset_pin_failed", "Failed to pin the preset")
    async def pin_preset(self, preset_id: str, current_user: User) -> APIResponse:
        data = await asyncio.to_thread(operations.pin_preset, self.collaborators, current_user, preset_id)
        return self.success_response(data=data)

    @_guarded("preset_unpin_failed", "Failed to unpin the preset")
    async def unpin_preset(self, preset_id: str, current_user: User) -> APIResponse:
        data = await asyncio.to_thread(operations.unpin_preset, self.collaborators, current_user, preset_id)
        return self.success_response(data=data)

    @_guarded("preset_recent_failed", "Failed to record the recent preset")
    async def push_recent_preset(self, preset_id: str, current_user: User) -> APIResponse:
        data = await asyncio.to_thread(operations.push_recent_preset, self.collaborators, current_user, preset_id)
        return self.success_response(data=data)

    @_guarded("modes_get_failed", "Failed to get modes")
    async def get_available_modes(self, preset_id: str, current_user: Optional[User] = None) -> APIResponse:
        """Get available modes for a specific preset."""
        await operations.refresh_mode_requirements(self.collaborators, preset_id)
        data = operations.get_available_modes(self.collaborators, preset_id)
        install = (
            await asyncio.to_thread(self.recipe_links.mode_install, preset_id, current_user)
            if self.recipe_links is not None and current_user is not None
            else {}
        )
        for mode in data.get("modes") or []:
            mode["install"] = install.get(mode["name"])
        return self.success_response(data=data)

    @_guarded("form_schema_failed", "Failed to get form schema")
    async def get_form_schema(
        self,
        preset_id: str,
        mode: str = None,
        form_name: str = None
    ) -> APIResponse:
        """Get form schema for a specific preset and mode."""
        data = operations.get_form_schema(self.collaborators, preset_id, mode, form_name)
        return self.success_response(data=data)

    @_guarded("pipes_get_failed", "Failed to get pipes")
    async def get_pipeline(
        self,
        preset_id: str,
        mode: str = "txt2img",
        form_data: dict = None
    ) -> APIResponse:
        """Get pipe configuration and connections for a specific preset."""
        result = operations.get_pipeline(self.collaborators, preset_id, mode, form_data)
        return self.success_response(data=result.to_dict())

    @_guarded("preset_reload_failed", "Failed to reload preset")
    async def reload_preset(self, preset_id: str) -> APIResponse:
        """Reload a preset from disk and return its current state."""
        data = operations.reload_preset(self.collaborators, preset_id)
        if self.media_store is not None:
            self.media_store.purge_preset_thumbnail_cache(preset_id)
        return self.success_response(
            data=data,
            message=f"Preset '{preset_id}' reloaded successfully"
        )

    @_guarded("preset_install_failed", "Failed to install preset")
    async def install_preset(self, preset_id: str, current_user: User) -> APIResponse:
        """Install a preset (admin only)."""
        data = operations.install_preset(self.collaborators, preset_id, current_user)
        return self.success_response(
            data=data,
            message=f"Preset '{preset_id}' installed successfully"
        )

    @_guarded("preset_uninstall_failed", "Failed to uninstall preset")
    async def uninstall_preset(self, preset_id: str, current_user: User) -> APIResponse:
        """Uninstall a preset (admin only) - removes all user assignments."""
        message = operations.uninstall_preset(self.collaborators, preset_id, current_user)
        return self.success_response(message=message)

    @_guarded("preset_assignment_failed", "Failed to assign preset")
    async def assign_preset_to_users(
        self,
        preset_id: str,
        user_ids: list,
        current_user: User
    ) -> APIResponse:
        """Assign a preset to multiple users (admin only)."""
        data = operations.assign_preset_to_users(self.collaborators, preset_id, user_ids, current_user)
        return self.success_response(
            data=data,
            message=f"Preset '{preset_id}' assigned to {data['assigned_count']} users"
        )

    @_guarded("preset_unassignment_failed", "Failed to unassign preset")
    async def unassign_preset_from_user(
        self,
        preset_id: str,
        user_id: str,
        current_user: User
    ) -> APIResponse:
        """Unassign a preset from a user (admin only)."""
        message = operations.unassign_preset_from_user(self.collaborators, preset_id, user_id, current_user)
        return self.success_response(message=message)

    @_guarded("preset_assignments_failed", "Failed to get preset assignments")
    async def get_preset_assignments(self, preset_id: str, current_user: User) -> APIResponse:
        """Get assignment summary for a preset (admin only)."""
        data = operations.get_preset_assignments(self.collaborators, preset_id, current_user)
        return self.success_response(data=data)

    @_guarded("preset_configuration_get_failed", "Failed to get preset configuration")
    async def get_preset_configuration(self, preset_id: str, current_user: User) -> APIResponse:
        """Get a preset's declared configuration schema merged with its stored values."""
        data = operations.get_preset_configuration(self.collaborators, preset_id, current_user)
        return self.success_response(data=data)

    @_guarded("preset_configuration_set_failed", "Failed to set preset configuration")
    async def set_preset_configuration(
        self,
        preset_id: str,
        values: Dict[str, Any],
        current_user: User
    ) -> APIResponse:
        """Set admin-set configuration values for a preset (admin only)."""
        data = operations.set_preset_configuration(self.collaborators, preset_id, values, current_user)
        return self.success_response(data=data)

    @_guarded("form_overrides_get_failed", "Failed to get form overrides")
    async def get_form_overrides(
        self,
        preset_id: str,
        mode: Optional[str],
        current_user: User,
    ) -> APIResponse:
        """Get a mode's unmerged field inventory and current admin overrides (admin only)."""
        data = operations.get_form_overrides_inventory(self.collaborators, preset_id, mode, current_user)
        return self.success_response(data=data)

    @_guarded("form_overrides_set_failed", "Failed to set form overrides")
    async def set_form_overrides(
        self,
        preset_id: str,
        mode: str,
        overrides: Dict[str, Any],
        current_user: User,
    ) -> APIResponse:
        """Set admin per-field form overrides for one mode of a preset (admin only)."""
        data = operations.set_form_overrides(self.collaborators, preset_id, mode, overrides, current_user)
        return self.success_response(data=data)


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.preset_controller
    router = APIRouter(prefix="/api/presets", tags=["Presets"])

    def preset_access(preset_id: str, current_user: User = Depends(get_current_active_user)) -> User:
        controller.require_preset_access(preset_id, current_user)
        return current_user

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

    @router.get("/mode-labels", response_model=APIResponse, summary="Get Mode Display Names")
    async def get_mode_labels(current_user=Depends(get_current_active_user)):
        return await controller.get_mode_labels()

    @router.get("/picker-state", response_model=APIResponse, summary="Get Pinned and Recent Presets")
    async def get_picker_state(current_user: User = Depends(get_current_active_user)):
        return await controller.get_picker_state(current_user)

    @router.put("/picker-state/pinned/{preset_id}", response_model=APIResponse, summary="Pin a Preset")
    async def pin_preset(preset_id: str, current_user: User = Depends(get_current_active_user)):
        return await controller.pin_preset(preset_id, current_user)

    @router.delete("/picker-state/pinned/{preset_id}", response_model=APIResponse, summary="Unpin a Preset")
    async def unpin_preset(preset_id: str, current_user: User = Depends(get_current_active_user)):
        return await controller.unpin_preset(preset_id, current_user)

    @router.post("/picker-state/recent/{preset_id}", response_model=APIResponse, summary="Record a Recently Chosen Preset")
    async def push_recent_preset(preset_id: str, current_user: User = Depends(get_current_active_user)):
        return await controller.push_recent_preset(preset_id, current_user)

    @router.get("/{preset_id}", response_model=APIResponse, summary="Get Preset Details")
    async def get_preset(preset_id: str, current_user=Depends(preset_access)):
        """Get detailed information about a specific preset including its configuration."""
        return await controller.get_preset(preset_id, current_user)

    @router.get("/{preset_id}/requirements", response_model=APIResponse, summary="Get Preset Requirements")
    async def get_preset_requirements(
        preset_id: str,
        backend_id: Optional[str] = None,
        refresh: bool = False,
        current_user=Depends(get_current_admin_user),
    ):
        """Check this preset's declared `requirements:` against this instance (admin only).

        `results`/`summary` reflect the host-scoped entries plus one backend's -
        `?backend_id=` if given and enabled for this preset's engine, else the
        engine's default backend, else the best-scoring enabled backend;
        `backends` lists every enabled backend of the engine with its own
        summary. Cached per (preset, requirements-block fingerprint[, backend]);
        pass `?refresh=1` to force a fresh evaluation. See docs/presets.md
        "Requirements"."""
        return await controller.get_preset_requirements(preset_id, backend_id, refresh)

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
        tasks: Optional[str] = None,
        model_types: Optional[str] = None,
        unquantized_only: bool = False,
        current_user=Depends(preset_access)
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
            tasks=tasks,
            model_types=model_types,
            unquantized_only=unquantized_only,
        )

    @router.get("/{preset_id}/modes", response_model=APIResponse, summary="Get Preset Modes")
    async def get_preset_modes(preset_id: str, current_user=Depends(preset_access)):
        """Get available generation modes (txt2img, img2img, etc.) for a specific preset."""
        return await controller.get_available_modes(preset_id, current_user)

    @router.get("/{preset_id}/form", response_model=APIResponse, summary="Get Form Schema")
    async def get_preset_form_schema(preset_id: str, mode: str = None, form_name: str = None, current_user=Depends(preset_access)):
        """Get the dynamic form schema for a preset, including field definitions and validation rules."""
        return await controller.get_form_schema(preset_id, mode, form_name)

    @router.get("/{preset_id}/pipes", response_model=APIResponse, summary="Get Pipeline Configuration")
    async def get_preset_pipes(preset_id: str, mode: str = "txt2img", current_user=Depends(preset_access)):
        """Get the pipeline configuration and pipe connections for a preset and mode."""
        return await controller.get_pipeline(preset_id, mode)

    @router.post("/{preset_id}/pipes", response_model=APIResponse, summary="Get Pipeline with Form Data")
    async def get_preset_pipes_with_form_data(preset_id: str, request: Dict[str, Any], mode: str = "txt2img", current_user=Depends(preset_access)):
        """Get pipeline configuration evaluated with specific form data for dynamic pipe configuration."""
        form_data = request.get('form_data', {})
        return await controller.get_pipeline(preset_id, mode, form_data)

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
        return await controller.get_preset_configuration(preset_id, current_user)

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
