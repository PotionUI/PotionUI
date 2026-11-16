import asyncio
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, Query

from src.features.editor_tasks.manager import EditorTaskManager
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.api_scopes import ApiScope, key_access
from src.platform.security.current_user import get_current_active_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class EditorTaskController(BaseController):
    def __init__(self, manager: EditorTaskManager):
        super().__init__()
        self.manager = manager

    @guarded(fallback=Fail("list_editor_tasks_failed", "Failed to list editor tasks", 400))
    async def list_tasks(self, user, editor: str) -> APIResponse:
        tasks = await asyncio.to_thread(self.manager.offers, user, editor)
        return self.success_response(data={"tasks": tasks})


def build_router(container: "AppContainer") -> APIRouter:
    controller = EditorTaskController(container.editor_tasks)
    router = APIRouter(
        prefix="/api/editor", tags=["Editor"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.CONTENT)]
    )

    @router.get(
        "/tasks",
        response_model=APIResponse,
        summary="List Editor Tasks",
        description="The jobs the editor can do and which preset modes offer each, limited to the presets you may use.",
    )
    async def list_tasks(
        editor: Literal["image", "video", "audio"] = Query("image"),
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.list_tasks(current_user, editor)

    return router
