from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends

from src.features.presets.fixed_prompt import read_fixed_prompt, write_fixed_prompt
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.api_scopes import ApiScope, key_access
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class FixedPromptController(BaseController):
    def __init__(self, preset_loader, repository, content_safety=None):
        super().__init__()
        self.preset_loader = preset_loader
        self.repository = repository
        self.content_safety = content_safety

    @guarded(None, Fail("fixed_prompt_read_failed", "Failed to read the fixed prompt text", status=500), admin="current_user")
    async def get_fixed_prompt(self, preset_id: str, current_user: User) -> APIResponse:
        fixed = read_fixed_prompt(self.preset_loader, self.repository, preset_id)
        return self.success_response(data=fixed.to_dict())

    @guarded(None, Fail("fixed_prompt_update_failed", "Failed to save the fixed prompt text", status=500), admin="current_user")
    async def set_fixed_prompt(self, preset_id: str, payload: Any, current_user: User) -> APIResponse:
        fixed = write_fixed_prompt(self.preset_loader, self.repository, preset_id, payload, self.content_safety)
        return self.success_response(data=fixed.to_dict())


def build_router(container: "AppContainer") -> APIRouter:
    controller = FixedPromptController(
        container.preset_template_loader, container.preset_fixed_prompt_repository, container.content_safety,
    )
    router = APIRouter(prefix="/api/presets", tags=["Presets"], dependencies=[key_access(read=ApiScope.ADMIN, write=ApiScope.ADMIN)])

    @router.get("/{preset_id}/fixed-prompt", response_model=APIResponse, summary="Get Preset Fixed Prompt Text")
    async def get_fixed_prompt(preset_id: str, current_user: User = Depends(get_current_admin_user)):
        return await controller.get_fixed_prompt(preset_id, current_user)

    @router.put("/{preset_id}/fixed-prompt", response_model=APIResponse, summary="Set Preset Fixed Prompt Text")
    async def set_fixed_prompt(
        preset_id: str,
        payload: Any = Body(...),
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.set_fixed_prompt(preset_id, payload, current_user)

    return router
