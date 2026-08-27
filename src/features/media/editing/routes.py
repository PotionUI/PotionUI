"""
Media Editing Controller.

Edits a library resource and returns the resource the edit produced. Thin
handlers over `MediaEditor`.

These live in the media feature rather than the library one because the
`uploads` table, its repository, the path resolver and every image/video/audio
helper an edit needs are owned here; `src.features.library` is the curation
layer built on top of that and imports it, so an edit endpoint there would have
to reach back down. It also keeps the MediaLoader form field - which knows about
uploads but nothing about collections - off the library router.

`InvalidEditError` is caught before `ValueError`: the first means the caller
asked for an impossible edit (400), the second that the resource is missing or
someone else's, which answer identically at 404 so a probe cannot tell them
apart.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.media.editing.dto import (
    EditMediaRequest,
    ExtractFrameRequest,
    SplitMediaRequest,
    SplitMediaResult,
)
from src.features.media.editing.editor import MediaEditor
from src.features.media.editing.operations import InvalidEditError, MediaEditFailedError

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class MediaEditController(BaseController):
    """Controller for editing a user's library resources."""

    def __init__(self, media_editor: MediaEditor):
        super().__init__()
        self.manager = media_editor

    async def edit_item(
        self,
        item_id: str,
        request: EditMediaRequest,
        current_user
    ) -> APIResponse:
        """Apply an ordered list of operations to one library resource."""
        try:
            result = await self.manager.edit_item(
                item_id, current_user.id, request.operations, request.mode
            )
            return self.success_response(data=result.model_dump())
        except InvalidEditError as e:
            return self.error_response(error="invalid_edit", message=str(e))
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except MediaEditFailedError as e:
            self.logger.error(f"Failed to edit media {item_id}: {e}")
            return self.error_response(error="edit_failed", message=str(e), status_code=500)
        except Exception as e:
            self.logger.error(f"Failed to edit media {item_id}: {e}")
            return self.error_response(error="edit_failed", message="Failed to edit media", status_code=500)

    async def extract_frame(
        self,
        item_id: str,
        request: ExtractFrameRequest,
        current_user
    ) -> APIResponse:
        """Save one frame of a video as a new image resource."""
        try:
            result = await self.manager.extract_frame(
                item_id, current_user.id, request.time_seconds
            )
            return self.success_response(data=result.model_dump())
        except InvalidEditError as e:
            return self.error_response(error="invalid_edit", message=str(e))
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except MediaEditFailedError as e:
            self.logger.error(f"Failed to extract a frame from {item_id}: {e}")
            return self.error_response(error="edit_failed", message=str(e), status_code=500)
        except Exception as e:
            self.logger.error(f"Failed to extract a frame from {item_id}: {e}")
            return self.error_response(error="edit_failed", message="Failed to extract frame", status_code=500)

    async def split_item(
        self,
        item_id: str,
        request: SplitMediaRequest,
        current_user
    ) -> APIResponse:
        """Split one audio resource into fixed-length parts, none of them replacing it."""
        try:
            items = await self.manager.split_item(
                item_id, current_user.id, request.part_seconds
            )
            return self.success_response(data=SplitMediaResult(items=items).model_dump())
        except InvalidEditError as e:
            return self.error_response(error="invalid_edit", message=str(e))
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except MediaEditFailedError as e:
            self.logger.error(f"Failed to split media {item_id}: {e}")
            return self.error_response(error="edit_failed", message=str(e), status_code=500)
        except Exception as e:
            self.logger.error(f"Failed to split media {item_id}: {e}")
            return self.error_response(error="edit_failed", message="Failed to split media", status_code=500)


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.media_edit_controller
    # No shared prefix: /split lives in the /api/media/split namespace, not
    # nested under /api/media/edit, so each route names its own full path.
    router = APIRouter(tags=["Media Editing"])

    @router.post(
        "/api/media/edit/{item_id}", response_model=APIResponse, summary="Edit Library Resource"
    )
    async def edit_item(
        item_id: str,
        request: EditMediaRequest,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Crop / resize / rotate / flip / trim one library resource."""
        return await controller.edit_item(item_id, request, current_user)

    @router.post(
        "/api/media/edit/{item_id}/frame", response_model=APIResponse, summary="Extract Video Frame"
    )
    async def extract_frame(
        item_id: str,
        request: ExtractFrameRequest,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Save a single frame of a video as a new image resource."""
        return await controller.extract_frame(item_id, request, current_user)

    @router.post(
        "/api/media/split/{item_id}", response_model=APIResponse, summary="Split Audio Into Parts"
    )
    async def split_item(
        item_id: str,
        request: SplitMediaRequest,
        current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        """Split an audio resource into fixed-length parts."""
        return await controller.split_item(item_id, request, current_user)

    return router
