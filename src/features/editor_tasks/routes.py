import asyncio
from typing import TYPE_CHECKING, Literal, Optional

from fastapi import APIRouter, Depends, Query

from src.features.editor_tasks.dto import SetTaskDefaultRequest, StartEditorRunRequest
from src.features.editor_tasks.formats import ExpandFormats
from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.runs import EditorRunManager
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.api_scopes import ApiScope, key_access, key_scope
from src.platform.security.current_user import get_current_active_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class EditorTaskController(BaseController):
    def __init__(self, manager: EditorTaskManager, formats: Optional[ExpandFormats] = None):
        super().__init__()
        self.manager = manager
        self.formats = formats

    @guarded(fallback=Fail("list_expand_formats_failed", "Failed to list the formats", 400))
    async def list_formats(self, user) -> APIResponse:
        formats = await asyncio.to_thread(self.formats.for_user, user) if self.formats is not None else []
        return self.success_response(data={"formats": formats})

    @guarded(fallback=Fail("list_editor_tasks_failed", "Failed to list editor tasks", 400))
    async def list_tasks(self, user, editor: str) -> APIResponse:
        tasks = await asyncio.to_thread(self.manager.offers, user, editor)
        return self.success_response(data={"tasks": tasks})

    @guarded(fallback=Fail("set_editor_task_default_failed", "Failed to set the default", 400))
    async def set_default(self, user, task_id: str, body: SetTaskDefaultRequest) -> APIResponse:
        task = await asyncio.to_thread(self.manager.set_default, user, task_id, body)
        return self.success_response(data={"task": task})

    @guarded(fallback=Fail("clear_editor_task_default_failed", "Failed to clear the default", 400))
    async def clear_default(self, user, task_id: str) -> APIResponse:
        task = await asyncio.to_thread(self.manager.clear_default, user, task_id)
        return self.success_response(data={"task": task})


def build_router(container: "AppContainer") -> APIRouter:
    controller = EditorTaskController(container.editor_tasks, getattr(container, "expand_formats", None))
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

    @router.get(
        "/expand/formats",
        response_model=APIResponse,
        summary="List Expand Formats",
        description=(
            "The named formats the Expand task offers: the built-in ones and the house formats an administrator "
            "added for everyone or for a group you belong to. Each has a ratio as W:H."
        ),
    )
    async def list_formats(current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.list_formats(current_user)

    @router.put(
        "/tasks/{task_id}/default",
        response_model=APIResponse,
        summary="Set My Default Setup For A Task",
        description=(
            "Makes a preset mode and a starting point for its settings (one of your editor sessions, one of your "
            "formulas, or the mode's own defaults) your default for a task. Replaces any earlier default of yours "
            "for that task. Answers with the task entry."
        ),
    )
    async def set_default(
        task_id: str, body: SetTaskDefaultRequest, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.set_default(current_user, task_id, body)

    @router.delete(
        "/tasks/{task_id}/default",
        response_model=APIResponse,
        summary="Clear My Default Setup For A Task",
        description="Removes your default for a task. Answers with the task entry, whose default is null.",
    )
    async def clear_default(task_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.clear_default(current_user, task_id)

    return router


class EditorRunController(BaseController):
    def __init__(self, runs: EditorRunManager):
        super().__init__()
        self.runs = runs

    @guarded(fallback=Fail("start_editor_run_failed", "Failed to start the run", 400))
    async def start(self, user, task_id: str, body: StartEditorRunRequest) -> APIResponse:
        run = await self.runs.start(user, task_id, body)
        return self.success_response(data={"run": run})

    @guarded(fallback=Fail("get_editor_run_failed", "Failed to get the run", 400))
    async def get(self, user, run_id: str) -> APIResponse:
        return self.success_response(data={"run": await self.runs.get(user, run_id)})

    @guarded(fallback=Fail("cancel_editor_run_failed", "Failed to cancel the run", 400))
    async def cancel(self, user, run_id: str) -> APIResponse:
        return self.success_response(data={"run": await self.runs.cancel(user, run_id)})


def build_runs_router(container: "AppContainer") -> APIRouter:
    controller = EditorRunController(container.editor_runs)
    router = APIRouter(
        prefix="/api/editor", tags=["Editor"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.GENERATE)]
    )

    @router.post(
        "/tasks/{task_id}/runs",
        response_model=APIResponse,
        status_code=202,
        summary="Start An Editor Run",
        description=(
            "Crops the marked area with its surroundings, runs a preset mode on it as a normal generation, and "
            "blends each result back as a patch. On a video task it cuts the marked time range (in seconds) and "
            "splices each result into the clip. Answers at once with the run; follow it with the run route or "
            "the editor_run_update message on /ws/generation."
        ),
    )
    async def start_run(
        task_id: str, body: StartEditorRunRequest, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.start(current_user, task_id, body)

    @router.get(
        "/runs/{run_id}",
        response_model=APIResponse,
        summary="Get An Editor Run",
        description="One of your runs with its state, progress and finished patches. Someone else's run answers 404.",
        dependencies=[key_scope(ApiScope.READ)],
    )
    async def get_run(run_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.get(current_user, run_id)

    @router.delete(
        "/runs/{run_id}",
        response_model=APIResponse,
        summary="Cancel An Editor Run",
        description="Cancels a queued or running run and its generation. A run that has finished is returned as it is.",
    )
    async def cancel_run(run_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.cancel(current_user, run_id)

    return router
