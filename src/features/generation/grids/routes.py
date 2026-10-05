from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from src.features.generation.grids.dto import CreateGridRequest
from src.features.generation.grids.repository import grid_repo
from src.features.generation.grids.service import GridError, GridService
from src.features.generation.policy import GenerationPolicy
from src.features.generation.repository import generation_repo
from src.features.plans.errors import LimitExceeded
from src.platform.http.base_controller import APIResponse
from src.platform.security.current_user import get_current_active_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


def _grid_error(error: GridError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"success": False, "error": error.code, "message": error.message, **error.extra},
    )


def _service(container: "AppContainer") -> GridService:
    service = getattr(container, "_generation_grid_service", None)
    if service is not None:
        return service
    from src.features.generation.routes import _get_generation_controller

    controller = _get_generation_controller(container)

    async def submit(request, user, ref):
        response = await controller.start_generation(request, user, grid_cell=ref)
        return response.data

    service = GridService(
        grid_repo,
        generation_repo,
        container.generation_history_facade,
        container.preset_template_loader,
        container.settings,
        submit,
        container.generation_orchestrator.cancel_generation,
        limit_guard=getattr(container, "limit_guard", None),
        is_admin=GenerationPolicy.is_admin,
    )
    container._generation_grid_service = service
    return service


def build_router(container: "AppContainer") -> APIRouter:
    service = _service(container)
    router = APIRouter(prefix="/api/generations/grids", tags=["Generation"])

    async def guarded(call):
        try:
            return APIResponse(success=True, data=await call())
        except GridError as error:
            raise _grid_error(error)
        except LimitExceeded as error:
            raise error.http()

    @router.get("/settings", response_model=APIResponse, summary="Get Compare Grid Settings")
    async def get_settings(current_user=Depends(get_current_active_user)):
        return APIResponse(success=True, data=service.read_settings())

    @router.post("", response_model=APIResponse, summary="Start X/Y Compare Grid")
    async def create_grid(body: CreateGridRequest, current_user=Depends(get_current_active_user)):
        return await guarded(lambda: service.create(current_user, body))

    @router.get("/{grid_id}", response_model=APIResponse, summary="Get Compare Grid")
    async def get_grid(grid_id: str, current_user=Depends(get_current_active_user)):
        return await guarded(lambda: service.get(grid_id, current_user))

    @router.post("/{grid_id}/retry-failed", response_model=APIResponse, summary="Retry Failed Compare Cells")
    async def retry_failed(grid_id: str, current_user=Depends(get_current_active_user)):
        return await guarded(lambda: service.retry_failed(grid_id, current_user))

    @router.delete("/{grid_id}", response_model=APIResponse, summary="Delete Compare Grid")
    async def delete_grid(grid_id: str, current_user=Depends(get_current_active_user)):
        async def run():
            await service.delete(grid_id, current_user)
            return None

        return await guarded(run)

    return router
