from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from src.features.tab_drafts.dto import ReplaceTabDraftRequest, TabDraftOperationsRequest
from src.features.tab_drafts.errors import (
    TabDraftInvalid,
    TabDraftLimitReached,
    TabDraftNotFound,
    TabDraftTooLarge,
)
from src.features.tab_drafts.manager import TabDraftManager
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.api_scopes import ApiScope, key_access
from src.platform.security.current_user import get_current_active_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


def _failure(status_code: int, error: str, message: str, **extra) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": error, "message": message, **extra},
    )


class TabDraftController(BaseController):
    def __init__(self, manager: TabDraftManager):
        super().__init__()
        self.manager = manager

    def _guarded(self, call):
        try:
            return call()
        except TabDraftNotFound:
            raise _failure(404, "tab_draft_not_found", "Tab draft not found")
        except TabDraftTooLarge as e:
            raise _failure(413, "tab_draft_too_large", str(e), limit=e.limit)
        except TabDraftLimitReached as e:
            raise _failure(409, "tab_draft_limit", str(e), limit=e.limit)
        except TabDraftInvalid as e:
            extra = {"operations": e.operations} if e.operations else {}
            raise _failure(422, "tab_draft_invalid", str(e), **extra)

    async def list_drafts(self, user_id: str) -> APIResponse:
        drafts = self.manager.list(user_id)
        return self.success_response(data=[d.to_dict() for d in drafts])

    async def get_draft(self, user_id: str, tab_id: str) -> APIResponse:
        draft = self._guarded(lambda: self.manager.find(user_id, tab_id))
        return self.success_response(data=draft.to_dict() if draft else None)

    async def replace_draft(self, user_id: str, tab_id: str, request: ReplaceTabDraftRequest) -> APIResponse:
        draft = self._guarded(lambda: self.manager.replace(user_id, tab_id, request.doc))
        return self.success_response(data=draft.to_dict())

    async def apply_operations(
        self, user_id: str, tab_id: str, request: TabDraftOperationsRequest
    ) -> APIResponse:
        draft = self._guarded(lambda: self.manager.apply_operations(user_id, tab_id, request.ops))
        return self.success_response(data=draft.to_dict())

    async def delete_draft(self, user_id: str, tab_id: str) -> APIResponse:
        self._guarded(lambda: self.manager.delete(user_id, tab_id))
        return self.success_response(message="Tab draft deleted")


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.tab_draft_controller
    router = APIRouter(prefix="/api/tab-drafts", tags=["Tab drafts"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.CONTENT)])

    @router.get("", response_model=APIResponse, summary="List Tab Drafts")
    async def list_drafts(current_user=Depends(get_current_active_user)):
        return await controller.list_drafts(current_user.id)

    @router.get("/{tab_id}", response_model=APIResponse, summary="Get Tab Draft")
    async def get_draft(tab_id: str, current_user=Depends(get_current_active_user)):
        return await controller.get_draft(current_user.id, tab_id)

    @router.put("/{tab_id}", response_model=APIResponse, summary="Replace Tab Draft")
    async def replace_draft(
        tab_id: str, request: ReplaceTabDraftRequest, current_user=Depends(get_current_active_user)
    ):
        return await controller.replace_draft(current_user.id, tab_id, request)

    @router.post("/{tab_id}/ops", response_model=APIResponse, summary="Apply Tab Draft Operations")
    async def apply_operations(
        tab_id: str, request: TabDraftOperationsRequest, current_user=Depends(get_current_active_user)
    ):
        return await controller.apply_operations(current_user.id, tab_id, request)

    @router.delete("/{tab_id}", response_model=APIResponse, summary="Delete Tab Draft")
    async def delete_draft(tab_id: str, current_user=Depends(get_current_active_user)):
        return await controller.delete_draft(current_user.id, tab_id)

    return router
