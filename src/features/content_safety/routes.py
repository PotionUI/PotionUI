from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.platform.http.base_controller import APIResponse
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


def build_router(container: "AppContainer") -> APIRouter:
    manager = container.content_safety
    router = APIRouter(prefix="/api/content-safety", tags=["Content Safety"])

    @router.get("/status", response_model=APIResponse, summary="Content Safety Status")
    async def status(current_user: User = Depends(get_current_admin_user)) -> APIResponse:
        return APIResponse(success=True, data=manager.status())

    @router.post("/backfill", response_model=APIResponse, summary="Rate Unrated Media")
    async def backfill(current_user: User = Depends(get_current_admin_user)) -> APIResponse:
        started = manager.start_backfill()
        return APIResponse(success=True, data={"started": started, **manager.status()["backfill"]})

    return router
