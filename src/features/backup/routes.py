"""Admin endpoints for taking and listing backups."""

from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.features.backup.admin import BackupRunning, BackupRuns
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_admin_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class BackupRunRequest(BaseModel):
    tier: Optional[str] = None


class BackupAdminController(BaseController):
    """Where backups go, what is already there, and the run in flight."""

    def __init__(self, runs: BackupRuns):
        super().__init__()
        self.runs = runs

    async def get_overview(self) -> APIResponse:
        return self.success_response(data=self.runs.overview())

    async def run(self, tier: Optional[str]) -> APIResponse:
        try:
            job = self.runs.start(tier)
        except BackupRunning as e:
            return self.error_response(error="backup_running", message=str(e), status_code=409)
        except ValueError as e:
            return self.error_response(error="invalid_tier", message=str(e), status_code=400)
        return self.success_response(data=job.to_dict(), message="Backup started")

    async def get_job(self) -> APIResponse:
        job = self.runs.current()
        return self.success_response(data=job.to_dict() if job else None)

    async def delete(self, name: str) -> APIResponse:
        if not self.runs.delete_archive(name):
            return self.error_response(
                error="archive_not_found",
                message=f"No backup archive named '{name}' in the backup directory",
                status_code=404,
            )
        return self.success_response(message=f"Deleted {name}")


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller = BackupAdminController(container.backup_runs)

    admin_router = APIRouter(prefix="/api/admin", tags=["Settings"])

    @admin_router.get("/backups", response_model=APIResponse, summary="Get Backup Overview")
    async def get_backups(current_user=Depends(get_current_admin_user)):
        """The backup settings, the archives on disk, the media mirror and the cron line."""
        return await controller.get_overview()

    @admin_router.post(
        "/backups/run", response_model=APIResponse, status_code=202, summary="Run Backup"
    )
    async def run_backup(
        request: Optional[BackupRunRequest] = None, current_user=Depends(get_current_admin_user)
    ):
        """Take a backup now, at the requested tier or the configured default."""
        return await controller.run(request.tier if request else None)

    @admin_router.get("/backups/job", response_model=APIResponse, summary="Get Backup Job")
    async def get_backup_job(current_user=Depends(get_current_admin_user)):
        """The last run's state, or null when nothing has run this process."""
        return await controller.get_job()

    @admin_router.delete("/backups/{name}", response_model=APIResponse, summary="Delete Backup")
    async def delete_backup(name: str, current_user=Depends(get_current_admin_user)):
        """Remove one archive from the backup directory."""
        return await controller.delete(name)

    return admin_router
