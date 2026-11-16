import asyncio
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, Query

from src.features.edits.dto import CreateEditRequest, RenameEditRequest, UpdateEditStateRequest
from src.features.edits.manager import EditManager
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.api_scopes import ApiScope, key_access
from src.platform.security.current_user import get_current_active_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class EditController(BaseController):

    def __init__(self, manager: EditManager):
        super().__init__()
        self.manager = manager

    @guarded(fallback=Fail("list_edits_failed", "Failed to list edits", 400))
    async def list_edits(
        self, user, tool: Optional[str], search: Optional[str], limit: int, offset: int
    ) -> APIResponse:
        edits, total = await asyncio.to_thread(self.manager.list, user.id, tool, search, limit, offset)
        return self.success_response(data={
            "edits": [edit.to_dict() for edit in edits],
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @guarded(fallback=Fail("get_edit_failed", "Failed to get edit", 400))
    async def get_edit(self, edit_id: str, user) -> APIResponse:
        edit = await asyncio.to_thread(self.manager.get, user.id, edit_id)
        return self.success_response(data={"edit": edit.to_dict(with_state=True)})

    @guarded(fallback=Fail("create_edit_failed", "Failed to create edit", 400))
    async def create_edit(self, request: CreateEditRequest, user) -> APIResponse:
        edit = await asyncio.to_thread(
            self.manager.create, user.id, request.tool, request.name, request.source.kind,
            request.source.id, request.state, request.thumbnail_upload_id,
        )
        return self.success_response(data={"edit": edit.to_dict(with_state=True)})

    @guarded(fallback=Fail("update_edit_failed", "Failed to update edit", 400))
    async def update_state(self, edit_id: str, request: UpdateEditStateRequest, user) -> APIResponse:
        edit = await asyncio.to_thread(
            self.manager.update_state, user.id, edit_id, request.revision, request.state,
            "thumbnail_upload_id" in request.model_fields_set, request.thumbnail_upload_id,
        )
        return self.success_response(data={"edit": edit.to_dict()})

    @guarded(fallback=Fail("rename_edit_failed", "Failed to rename edit", 400))
    async def rename_edit(self, edit_id: str, request: RenameEditRequest, user) -> APIResponse:
        edit = await asyncio.to_thread(self.manager.rename, user.id, edit_id, request.name)
        return self.success_response(data={"edit": edit.to_dict()})

    @guarded(fallback=Fail("duplicate_edit_failed", "Failed to duplicate edit", 400))
    async def duplicate_edit(self, edit_id: str, user) -> APIResponse:
        edit = await asyncio.to_thread(self.manager.duplicate, user.id, edit_id)
        return self.success_response(data={"edit": edit.to_dict(with_state=True)})

    @guarded(fallback=Fail("delete_edit_failed", "Failed to delete edit", 400))
    async def delete_edit(self, edit_id: str, user) -> APIResponse:
        await asyncio.to_thread(self.manager.delete, user.id, edit_id)
        return self.success_response(data={"id": edit_id, "deleted": True})


def build_router(container: "AppContainer") -> APIRouter:
    controller: EditController = container.edit_controller
    router = APIRouter(prefix="/api/edits", tags=["Edits"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.CONTENT)])

    @router.get("", response_model=APIResponse, summary="List Edits")
    async def list_edits(
        tool: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.list_edits(current_user, tool, search, limit, offset)

    @router.post("", response_model=APIResponse, summary="Create Edit")
    async def create_edit(request: CreateEditRequest, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.create_edit(request, current_user)

    @router.get("/{edit_id}", response_model=APIResponse, summary="Get Edit")
    async def get_edit(edit_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.get_edit(edit_id, current_user)

    @router.put("/{edit_id}", response_model=APIResponse, summary="Save Edit State")
    async def update_state(
        edit_id: str, request: UpdateEditStateRequest, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.update_state(edit_id, request, current_user)

    @router.patch("/{edit_id}", response_model=APIResponse, summary="Rename Edit")
    async def rename_edit(
        edit_id: str, request: RenameEditRequest, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.rename_edit(edit_id, request, current_user)

    @router.post("/{edit_id}/duplicate", response_model=APIResponse, summary="Duplicate Edit")
    async def duplicate_edit(edit_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.duplicate_edit(edit_id, current_user)

    @router.delete("/{edit_id}", response_model=APIResponse, summary="Delete Edit")
    async def delete_edit(edit_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.delete_edit(edit_id, current_user)

    return router
