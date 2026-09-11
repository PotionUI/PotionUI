"""Admin endpoints for the retention passes and for ad-hoc generation deletion."""

from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, HTTPException

from src.features.generation.criteria_delete import (
    GenerationDeleteCriteria,
    delete_generations,
    preview_generations,
)
from src.features.generation.status_tracker import TERMINAL_STATES
from src.features.housekeeping.worker import HousekeepingRunning, HousekeepingWorker
from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_admin_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer
    from src.features.generation.history_facade import GenerationHistoryFacade
    from src.features.generation.repository import GenerationRepository


class HousekeepingAdminController(BaseController):
    """The retention windows, what they would remove now, and the last pass."""

    def __init__(
        self,
        worker: HousekeepingWorker,
        generation_repository: "GenerationRepository",
        generation_history_facade: "GenerationHistoryFacade",
    ):
        super().__init__()
        self.worker = worker
        self.generation_repository = generation_repository
        self.generation_history_facade = generation_history_facade

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

    async def preview_generation_deletion(self, criteria: GenerationDeleteCriteria) -> APIResponse:
        count = preview_generations(self.generation_repository, criteria)
        return self.success_response(data={"count": count})

    async def delete_generations(self, criteria: GenerationDeleteCriteria) -> APIResponse:
        if criteria.is_empty():
            return self.error_response(
                error="no_criteria",
                message="Choose at least one criterion before deleting generations",
                status_code=400,
            )
        summary = delete_generations(self.generation_repository, self.generation_history_facade, criteria)
        return self.success_response(data=summary, message="Generations deleted")


def _criteria(
    older_than_days: Optional[int], without_media: bool, statuses: Optional[str], keep_favorites: bool
) -> GenerationDeleteCriteria:
    if older_than_days is not None and older_than_days < 0:
        raise HTTPException(status_code=400, detail="older_than_days must not be negative")

    status_list = None
    if statuses:
        status_list = [s.strip() for s in statuses.split(",") if s.strip()]
        invalid = [s for s in status_list if s not in TERMINAL_STATES]
        if invalid:
            raise HTTPException(status_code=400, detail=f"invalid status: {', '.join(invalid)}")

    return GenerationDeleteCriteria(
        older_than_days=older_than_days,
        without_media=without_media,
        statuses=status_list,
        keep_favorites=keep_favorites,
    )


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller = HousekeepingAdminController(
        container.housekeeping_worker,
        container.generation_repository,
        container.generation_history_facade,
    )

    admin_router = APIRouter(prefix="/api/admin", tags=["Settings"])

    @admin_router.get("/housekeeping", response_model=APIResponse, summary="Get Housekeeping Overview")
    async def get_housekeeping(current_user=Depends(get_current_admin_user)):
        """Retention windows, what they would remove right now, and the last pass."""
        return await controller.get_overview()

    @admin_router.post("/housekeeping/run", response_model=APIResponse, status_code=202, summary="Run Housekeeping")
    async def run_housekeeping(current_user=Depends(get_current_admin_user)):
        """Apply every retention window once, now."""
        return await controller.run()

    @admin_router.get(
        "/housekeeping/generations/preview", response_model=APIResponse,
        summary="Preview Generation Deletion",
    )
    async def preview_housekeeping_generations(
        older_than_days: Optional[int] = None,
        without_media: bool = False,
        statuses: Optional[str] = None,
        keep_favorites: bool = True,
        current_user=Depends(get_current_admin_user),
    ):
        """How many generations, across every user, the given criteria match."""
        criteria = _criteria(older_than_days, without_media, statuses, keep_favorites)
        return await controller.preview_generation_deletion(criteria)

    @admin_router.delete(
        "/housekeeping/generations", response_model=APIResponse,
        summary="Delete Generations Matching Criteria",
    )
    async def delete_housekeeping_generations(
        older_than_days: Optional[int] = None,
        without_media: bool = False,
        statuses: Optional[str] = None,
        keep_favorites: bool = True,
        current_user=Depends(get_current_admin_user),
    ):
        """Delete every generation, for every user, matching the given criteria."""
        criteria = _criteria(older_than_days, without_media, statuses, keep_favorites)
        return await controller.delete_generations(criteria)

    return admin_router
