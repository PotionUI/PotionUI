"""
Keybinding Controller

Handles keybinding CRUD operations with thin route handlers delegating to controller methods.
"""
import logging
from typing import Optional, TYPE_CHECKING
from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.keybindings.dto import KeybindingResponse, UpdateKeybindingRequest
from src.features.keybindings.repository import KeybindingRepository
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class KeybindingController(BaseController):
    """
    Controller for keybinding operations.

    Handles CRUD operations for keybinding defaults and user overrides.
    """

    def __init__(self, repo: KeybindingRepository):
        super().__init__()
        self.repo = repo

    async def get_effective_keybindings(self, user: User) -> APIResponse:
        """Get effective keybindings for the current user (merged defaults + overrides)."""
        try:
            keybindings = self.repo.get_effective_keybindings(user.id)
            return self.success_response(data={
                "keybindings": keybindings,
                "total": len(keybindings)
            })
        except Exception as e:
            self.logger.error(f"Error getting effective keybindings: {e}")
            return self.error_api_response(error="get_keybindings_failed", message=str(e))

    async def get_defaults(self) -> APIResponse:
        """Get all default keybindings."""
        try:
            defaults = self.repo.get_all_defaults()
            return self.success_response(data={
                "keybindings": [
                    {
                        "action_id": d.id,
                        "key": d.key,
                        "modifiers": d.modifiers,
                        "label": d.label,
                        "category": d.category,
                        "context": d.context,
                        "description": d.description,
                        "enabled": d.enabled,
                        "is_custom": False,
                    }
                    for d in defaults
                ],
                "total": len(defaults)
            })
        except Exception as e:
            self.logger.error(f"Error getting default keybindings: {e}")
            return self.error_api_response(error="get_defaults_failed", message=str(e))

    async def update_keybinding(self, action_id: str, request: UpdateKeybindingRequest, user: User) -> APIResponse:
        """Set a user keybinding override."""
        try:
            self.repo.set_user_keybinding(
                user_id=user.id,
                action_id=action_id,
                key=request.key,
                modifiers=request.modifiers
            )
            return self.success_response(data={
                "message": f"Keybinding for '{action_id}' updated successfully"
            })
        except Exception as e:
            self.logger.error(f"Error updating keybinding: {e}")
            return self.error_api_response(error="update_keybinding_failed", message=str(e))

    async def reset_keybinding(self, action_id: str, user: User) -> APIResponse:
        """Reset a single user keybinding override back to default."""
        try:
            deleted = self.repo.reset_user_keybinding(user.id, action_id)
            if deleted:
                return self.success_response(data={
                    "message": f"Keybinding for '{action_id}' reset to default"
                })
            else:
                return self.success_response(data={
                    "message": f"No custom keybinding found for '{action_id}'"
                })
        except Exception as e:
            self.logger.error(f"Error resetting keybinding: {e}")
            return self.error_api_response(error="reset_keybinding_failed", message=str(e))

    async def reset_all_keybindings(self, user: User) -> APIResponse:
        """Reset all user keybinding overrides back to defaults."""
        try:
            count = self.repo.reset_all_user_keybindings(user.id)
            return self.success_response(data={
                "message": f"Reset {count} custom keybinding(s) to defaults",
                "count": count
            })
        except Exception as e:
            self.logger.error(f"Error resetting all keybindings: {e}")
            return self.error_api_response(error="reset_all_keybindings_failed", message=str(e))


# ========== Route Handlers ==========

def build_router(container: "AppContainer") -> APIRouter:
    from src.features.keybindings.repository import keybinding_repo

    controller = KeybindingController(keybinding_repo)
    router = APIRouter(prefix="/api/keybindings", tags=["Keybindings"])

    @router.get("/", response_model=APIResponse, summary="Get Effective Keybindings")
    async def get_keybindings(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Get effective keybindings for the current user."""
        return await controller.get_effective_keybindings(current_user)

    @router.get("/defaults", response_model=APIResponse, summary="Get Default Keybindings")
    async def get_defaults(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Get all default keybindings."""
        return await controller.get_defaults()

    @router.put("/{action_id}", response_model=APIResponse, summary="Update Keybinding")
    async def update_keybinding(
        action_id: str,
        request: UpdateKeybindingRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Set a user keybinding override."""
        return await controller.update_keybinding(action_id, request, current_user)

    @router.delete("/{action_id}", response_model=APIResponse, summary="Reset Keybinding")
    async def reset_keybinding(
        action_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Reset a single keybinding to default."""
        return await controller.reset_keybinding(action_id, current_user)

    @router.post("/reset", response_model=APIResponse, summary="Reset All Keybindings")
    async def reset_all_keybindings(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Reset all user keybinding overrides to defaults."""
        return await controller.reset_all_keybindings(current_user)

    return router
