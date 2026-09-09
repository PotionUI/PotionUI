"""Admin endpoints for the retention passes."""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.features.housekeeping.worker import HousekeepingRunning, HousekeepingWorker
from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_admin_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class HousekeepingAdminController(BaseController):
    """The retention windows, what they would remove now, and the last pass."""

    def __init__(self, worker: HousekeepingWorker):
        super().__init__()
        self.worker = worker

    async def get_overview(self) -> APIResponse:
        return self.success_response(data={
            "settings": self.worker.retention(),
            "running": self.worker.running,
            "last_run": self.worker.last_run,
            "next_run_at": self.worker.next_run_at,
            "preview": self.worker.preview(),
        })

    async def run(self) -> APIResponse:
        try:
            summary = await self.worker.run_now()
        except HousekeepingRunning as e:
            return self.error_response(
                error="housekeeping_running", message=str(e), status_code=409
            )
        return self.success_response(data=summary, message="Housekeeping finished")


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller = HousekeepingAdminController(container.housekeeping_worker)

    admin_router = APIRouter(prefix="/api/admin", tags=["Settings"])

    @admin_router.get("/housekeeping", response_model=APIResponse, summary="Get Housekeeping Overview")
    async def get_housekeeping(current_user=Depends(get_current_admin_user)):
        """Retention windows, what they would remove right now, and the last pass."""
        return await controller.get_overview()

    @admin_router.post("/housekeeping/run", response_model=APIResponse, status_code=202, summary="Run Housekeeping")
    async def run_housekeeping(current_user=Depends(get_current_admin_user)):
        """Apply every retention window once, now."""
        return await controller.run()

    return admin_router
