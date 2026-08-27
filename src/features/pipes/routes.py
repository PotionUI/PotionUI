"""Pipe installation API.

Two endpoints under /api/pipes, both ADMIN-only: installing a pipe runs
`pip install` and `git clone` with arguments a plugin declared, which is the
same privilege as enabling a plugin (`/api/plugins/{id}/enable`) and gated the
same way - `get_current_admin_user`, 403 for everyone else. The pipe catalog
itself is not privileged (`/api/docs/live/pipes` serves it to any authenticated
user), so there is nothing here to hide behind a 404.

Progress does not come back on the POST: it arrives on /ws/admin as
`pipe_install_status` (see `PipeInstallRunner`).

Both paths take `pipe_name` as `:path`, not a plain segment: a variant's
registry key contains a slash (`generator/trellis2`), which a plain segment
cannot match and percent-encoding does not rescue - the server decodes `%2F`
back to `/` before routing.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import User

from src.features.pipes.exceptions import (
    PipeInstallInProgressException,
    PipeNotFoundException,
)
from src.pipelines.installer import PipeNotAutoInstallableError

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


def build_router(container: "AppContainer") -> APIRouter:
    manager = container.pipe_install_runner

    router = APIRouter(prefix="/api/pipes", tags=["Pipes"])

    @router.get("/{pipe_name:path}", summary="Get Pipe Install State")
    async def get_pipe(pipe_name: str, current_user: User = Depends(get_current_admin_user)):
        """One pipe's status, requirements, and why its last install failed. Admin only."""
        try:
            return {"success": True, "data": manager.describe(pipe_name)}
        except PipeNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/{pipe_name:path}/install", summary="Install Pipe Requirements")
    async def install_pipe(pipe_name: str, current_user: User = Depends(get_current_admin_user)):
        """Start installing a pipe's requirements. Admin only.

        Returns as soon as the install is running - the outcome is broadcast on
        /ws/admin. 422 (not 400) when the pipe declines automatic installation:
        the request is well-formed, and its `detail` carries the commands that
        do install the requirements.
        """
        try:
            data = await manager.start_install(pipe_name)
            return {"success": True, "data": data, "message": f"Installing pipe '{pipe_name}'"}
        except PipeNotFoundException as e:
            raise HTTPException(status_code=404, detail=str(e))
        except PipeInstallInProgressException as e:
            raise HTTPException(status_code=409, detail=str(e))
        except PipeNotAutoInstallableError as e:
            raise HTTPException(status_code=422, detail=e.instructions)

    return router
