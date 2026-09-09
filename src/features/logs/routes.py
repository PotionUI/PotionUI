"""Admin endpoint for tailing the server's rotating log file.

Read-only and paginated - there is no download endpoint, since a traceback in
the log can carry third-party error text that shouldn't leave the app as a
raw file.
"""

from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, Query

from src.features.logs.tail import DEFAULT_LINES, MAX_LINES, LogLevel, tail_log
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.observability.logger import LOG_FILE_NAME, log_directory
from src.platform.security.current_user import get_current_admin_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class LogsAdminController(BaseController):
    """The tail of the server's rotating log file."""

    async def get_tail(self, lines: int, level: Optional[LogLevel]) -> APIResponse:
        directory = log_directory()
        path = directory / LOG_FILE_NAME if directory is not None else None
        result = tail_log(path, lines=lines, level=level.value if level else None)
        return self.success_response(data=result)


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller = LogsAdminController()

    admin_router = APIRouter(prefix="/api/admin", tags=["Settings"])

    @admin_router.get("/logs/tail", response_model=APIResponse, summary="Get Log Tail")
    async def get_log_tail(
        lines: int = Query(DEFAULT_LINES, ge=1, le=MAX_LINES),
        level: Optional[LogLevel] = Query(None),
        current_user=Depends(get_current_admin_user),
    ):
        """The last `lines` log entries, optionally filtered to `level` and above.

        `lines` out of `[1, 5000]` is rejected with a 422; an unrecognized
        `level` is rejected with a 422 (the accepted values are DEBUG, INFO,
        WARNING, ERROR, CRITICAL).
        """
        return await controller.get_tail(lines, level)

    return admin_router
