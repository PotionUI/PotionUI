"""
Media Controller - Unified controller for serving all media types (images, videos, etc.)
This replaces the image_controller endpoints with more appropriately named /api/media endpoints.
"""

import logging
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, Response
import aiofiles

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.generation.thumbnail_profile import (
    PROFILES,
    estimate_bytes,
    load_thumbnail_profile,
    match_profile,
)
from src.features.media import MediaStore, UnsupportedSizeError
from src.features.media.thumbnail_regeneration import ThumbnailJobRunning, ThumbnailRegeneration
from src.features.media.validators import UPLOAD_PURPOSE_USER

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class MediaController(BaseController):
    """Controller for handling all media storage and serving"""

    def __init__(self, media_store: MediaStore):
        super().__init__()
        self.manager = media_store

    async def _stream_file(self, file_path: str, chunk_size: int = 8192):
        """Stream file content asynchronously"""
        try:
            async with aiofiles.open(file_path, 'rb') as file:
                while chunk := await file.read(chunk_size):
                    yield chunk
        except Exception as e:
            logger.error(f"Error streaming file {file_path}: {str(e)}")
            raise

    async def serve_generation_media(
        self,
        generation_id: str,
        filename: str,
        request: Optional[Request] = None,
        size: Optional[str] = None,
        animated: Optional[bool] = False
    ):
        """Serve media file (image or video) from a generation."""
        try:
            result = self.manager.get_generation_media(
                generation_id, filename, size=size, animated=animated
            )

            # Check conditional request
            if request and request.headers.get("if-none-match") == result.headers.get("ETag"):
                return Response(status_code=304)

            if result.use_streaming and result.file_path:
                return StreamingResponse(
                    self._stream_file(result.file_path),
                    media_type=result.media_type,
                    headers=result.headers
                )
            elif result.content:
                return Response(
                    content=result.content,
                    media_type=result.media_type,
                    headers=result.headers
                )
            else:
                return FileResponse(
                    path=result.file_path,
                    media_type=result.media_type,
                    headers=result.headers
                )

        except ValueError as e:
            return self.error_api_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Error serving generation media: {str(e)}")
            return self.error_api_response(error="server_error", message="Failed to serve media")

    async def serve_temp_media(self, filename: str):
        """Serve temporary media files (no authentication required)"""
        try:
            result = self.manager.get_temp_media(filename)
            return FileResponse(
                path=result.file_path,
                media_type=result.media_type,
                filename=filename
            )
        except ValueError as e:
            return self.error_api_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Error serving temporary file: {str(e)}")
            return self.error_api_response(error="server_error", message="Failed to serve temporary file")

    async def serve_uploaded_media(self, filename: str, size: Optional[str] = None, animated: Optional[bool] = False):
        """Serve uploaded media files, or one of their thumbnails."""
        try:
            result = self.manager.get_uploaded_media(filename, size=size, animated=animated)
            if result.content is not None:
                return Response(content=result.content, media_type=result.media_type)
            return FileResponse(
                path=result.file_path,
                media_type=result.media_type,
                filename=filename
            )
        except ValueError as e:
            return self.error_api_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Error serving uploaded file: {str(e)}")
            return self.error_api_response(error="server_error", message="Failed to serve uploaded file")

    async def get_upload_info(self, filename: str, current_user):
        """Get best-effort metadata (width/height/duration/fps/size) for an
        already-uploaded file, for MediaSelect fields whose saved value has
        no metadata of its own."""
        try:
            user_id = current_user.id if current_user else None
            result = self.manager.get_upload_info(filename, user_id)
            return self.success_response(data=result.model_dump())
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Error getting upload info: {str(e)}")
            return self.error_response(error="get_info_failed", message="Failed to get upload info")

    async def upload_media(self, file: UploadFile, current_user, purpose: Optional[str] = None):
        """Upload a media file"""
        try:
            content = await file.read()
            user_id = current_user.id if current_user else None

            result = await self.manager.upload_media(
                file_data=content,
                filename=file.filename,
                content_type=file.content_type,
                user_id=user_id,
                purpose=purpose or UPLOAD_PURPOSE_USER,
            )

            return self.success_response(data={
                "path": result.path,
                "relative_path": result.relative_path,
                "filename": result.filename,
                "size": result.size,
                "url": result.url,
                "width": result.width,
                "height": result.height,
                "duration_seconds": result.duration_seconds,
                "fps": result.fps
            })

        except ValueError as e:
            return self.error_response(error="upload_failed", message=str(e))
        except Exception as e:
            logger.error(f"Failed to upload file: {str(e)}")
            return self.error_response(error="upload_failed", message="Failed to upload file")

    async def list_uploads(
        self,
        current_user,
        media_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ):
        """List the current user's media-loader uploads."""
        try:
            result = self.manager.list_uploads(
                current_user.id, media_type=media_type, limit=limit, offset=offset
            )
            return self.success_response(data=result.model_dump())
        except Exception as e:
            logger.error(f"Failed to list uploads: {str(e)}")
            return self.error_response(error="list_failed", message="Failed to list uploads")

    async def delete_upload(self, filename: str, current_user):
        """Delete one of the current user's uploads.

        Uniform 404 on both "doesn't exist" and "not yours" - never a 403 -
        so a filename probe can't confirm another user's upload exists
        (GenerationPolicy precedent).
        """
        try:
            self.manager.delete_upload(filename, current_user.id)
            return self.success_response(data={"filename": filename, "deleted": True})
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e), status_code=404)
        except Exception as e:
            logger.error(f"Failed to delete upload: {str(e)}")
            return self.error_response(error="delete_failed", message="Failed to delete upload")

    async def list_generation_media(self, generation_id: str, current_user):
        """List all media files for a generation"""
        try:
            result = self.manager.list_generation_media(generation_id, current_user.id)
            return self.success_response(data={
                "generation_id": result.generation_id,
                "media_count": result.media_count,
                "media": [m.model_dump() for m in result.media]
            })
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Failed to list generation media: {str(e)}")
            return self.error_response(error="list_failed", message="Failed to list generation media")

    async def delete_generation_media(self, generation_id: str, current_user):
        """Delete all media files for a generation"""
        try:
            result = self.manager.delete_generation_media(generation_id, current_user.id)
            return self.success_response(data={
                "generation_id": result.generation_id,
                "deleted_files": result.deleted_files,
                "failed_files": result.failed_files
            })
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Failed to delete generation media: {str(e)}")
            return self.error_response(error="delete_failed", message="Failed to delete generation media")

    async def serve_file_by_id(
        self,
        file_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        size: Optional[str] = None
    ):
        """Serve a file by its database ID with optional thumbnails"""
        try:
            result = self.manager.get_file_by_id(file_id, width, height, size)
            return Response(
                content=result.content,
                media_type=result.media_type,
                headers=result.headers
            )
        except ValueError as e:
            return self.error_api_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Error serving file by ID: {str(e)}")
            return self.error_api_response(error="server_error", message="Failed to serve file")

    async def get_file_blob(
        self,
        file_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        current_user=None
    ):
        """Get a file as blob data for frontend to create blob URLs"""
        try:
            user_id = current_user.id if current_user else None
            result = self.manager.get_file_blob(file_id, width, height, user_id)
            return Response(
                content=result.content,
                media_type=result.media_type,
                headers=result.headers
            )
        except ValueError as e:
            return self.error_api_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Error getting file blob: {str(e)}")
            return self.error_api_response(error="server_error", message="Failed to get file blob")

    async def serve_preset_file(
        self,
        preset_id: str,
        file_path: str,
        size: Optional[str] = None,
        request: Optional[Request] = None
    ):
        """Serve a static media file from a preset's `public/` directory.

        Unlike the sibling routes, this one answers with real HTTP status codes:
        browsers consume it through `<img src>`, where a 404 is what drives the
        element's error handler and stops a JSON error body being decoded as pixels.
        """
        try:
            result = self.manager.get_preset_file(preset_id, file_path, size)

            if request and request.headers.get("if-none-match") == result.headers.get("ETag"):
                return Response(status_code=304)

            if result.content:
                return Response(
                    content=result.content,
                    media_type=result.media_type,
                    headers=result.headers
                )
            elif result.use_streaming and result.file_path:
                return StreamingResponse(
                    self._stream_file(result.file_path),
                    media_type=result.media_type,
                    headers=result.headers
                )
            else:
                return FileResponse(
                    path=result.file_path,
                    media_type=result.media_type,
                    headers=result.headers
                )

        # Must precede ValueError - UnsupportedSizeError subclasses it.
        except UnsupportedSizeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError:
            # Uniform 404: never distinguish "wrong type" from "absent" to a caller.
            raise HTTPException(status_code=404, detail="Preset file not found")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error serving preset file: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to serve preset file")

    async def get_file_params(self, file_id: str, current_user):
        """Get parameters associated with a generated file"""
        try:
            result = self.manager.get_file_params(file_id, current_user.id)
            return self.success_response(data=result.model_dump())
        except ValueError as e:
            return self.error_response(error="not_found", message=str(e))
        except Exception as e:
            logger.error(f"Error getting file params: {str(e)}")
            return self.error_response(error="get_params_failed", message="Failed to get file parameters")


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.media_controller
    router = APIRouter(prefix="/api/media", tags=["Media"])

    @router.get("/generations/{generation_id}/{filename}", summary="Serve Generation Media")
    @router.head("/generations/{generation_id}/{filename}", summary="Get Generation Media Headers")
    async def serve_generation_media(
        generation_id: str,
        filename: str,
        size: Optional[str] = None,
        animated: Optional[bool] = False,
        request: Request = None
    ):
        """Serve media file from a generation"""
        return await controller.serve_generation_media(
            generation_id, filename, request, size, animated
        )

    @router.get("/tmp/{filename}", summary="Serve Temporary Media")
    async def serve_temp_media(filename: str):
        """Serve temporary media files (no authentication required)"""
        return await controller.serve_temp_media(filename)

    @router.get("/uploads/{filename}", summary="Serve Uploaded Media")
    async def serve_uploaded_media(
        filename: str,
        size: Optional[str] = None,
        animated: Optional[bool] = False,
    ):
        """Serve uploaded media files, or one of their thumbnails (no
        authentication required).

        Serving matches the generation and temporary media routes above: a
        browser rendering `<img src="/api/media/uploads/...">` cannot attach the
        bearer token the OAuth2 scheme expects, so a dependency here 401s every
        thumbnail. Listing, uploading and deleting stay authenticated - those go
        through the API client, which does send the header.
        """
        return await controller.serve_uploaded_media(filename, size, animated)

    @router.get("/uploads/{filename}/info", response_model=APIResponse, summary="Get Uploaded Media Info")
    async def get_upload_info(
        filename: str,
        current_user=Depends(get_current_active_user)
    ):
        """Get best-effort metadata for an already-uploaded file"""
        return await controller.get_upload_info(filename, current_user)

    @router.post("/upload", response_model=APIResponse, summary="Upload Media")
    async def upload_media(
        file: UploadFile = File(...),
        purpose: Optional[str] = Form(None),
        current_user=Depends(get_current_active_user)
    ):
        """Upload a media file"""
        return await controller.upload_media(file, current_user, purpose)

    @router.get("/uploads", response_model=APIResponse, summary="List My Uploads")
    async def list_uploads(
        media_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        current_user=Depends(get_current_active_user)
    ):
        """List the current user's media-loader uploads, newest first"""
        return await controller.list_uploads(current_user, media_type, limit, offset)

    @router.delete("/uploads/{filename}", response_model=APIResponse, summary="Delete My Upload")
    async def delete_upload(
        filename: str,
        current_user=Depends(get_current_active_user)
    ):
        """Delete one of the current user's uploads"""
        return await controller.delete_upload(filename, current_user)

    @router.get("/generations/{generation_id}", response_model=APIResponse, summary="List Generation Media")
    async def list_generation_media(
        generation_id: str,
        current_user=Depends(get_current_active_user)
    ):
        """List all media files for a generation"""
        return await controller.list_generation_media(generation_id, current_user)

    @router.delete("/generations/{generation_id}", response_model=APIResponse, summary="Delete Generation Media")
    async def delete_generation_media(
        generation_id: str,
        current_user=Depends(get_current_active_user)
    ):
        """Delete all media files for a generation"""
        return await controller.delete_generation_media(generation_id, current_user)

    @router.get("/files/{file_id}", summary="Serve File by ID")
    async def serve_file_by_id(
        file_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        size: Optional[str] = None
    ):
        """Serve a file by its database ID with optional resizing and thumbnails"""
        return await controller.serve_file_by_id(file_id, width, height, size)

    @router.get("/files/{file_id}/blob", summary="Get File as Blob")
    async def get_file_blob(
        file_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        current_user=Depends(get_current_active_user)
    ):
        """Get a file as blob data for frontend to create blob URLs"""
        return await controller.get_file_blob(file_id, width, height, current_user)

    @router.get("/files/{file_id}/params", response_model=APIResponse, summary="Get File Parameters")
    async def get_file_params(
        file_id: str,
        current_user=Depends(get_current_active_user)
    ):
        """Get parameters associated with a generated file"""
        return await controller.get_file_params(file_id, current_user)

    @router.get("/presets/{preset_id}/{file_path:path}", summary="Serve Preset File")
    async def serve_preset_file(
        preset_id: str,
        file_path: str,
        request: Request,
        size: Optional[str] = None
    ):
        """Serve static media files from a preset's `public/` directory"""
        return await controller.serve_preset_file(preset_id, file_path, size, request)

    return router


class ThumbnailAdminController(BaseController):
    """Admin view of thumbnail rendering: the active profile, what the named
    profiles would cost, what is on disk, and the regeneration run."""

    def __init__(self, settings, regeneration: ThumbnailRegeneration):
        super().__init__()
        self.settings = settings
        self.regeneration = regeneration

    def _job_payload(self):
        job = self.regeneration.current()
        return job.to_dict() if job else None

    async def get_overview(self) -> APIResponse:
        profile = load_thumbnail_profile(self.settings)
        counts = self.regeneration.counts()

        profiles = {}
        for name, candidate in PROFILES.items():
            payload = candidate.to_dict()
            payload["estimated_bytes"] = estimate_bytes(candidate, counts["images"], counts["videos"])
            profiles[name] = payload

        return self.success_response(data={
            "settings": profile.to_dict(),
            "profiles": profiles,
            "active_profile": match_profile(profile),
            "counts": counts,
            "usage": self.regeneration.usage(),
            "job": self._job_payload(),
        })

    async def regenerate(self) -> APIResponse:
        try:
            job = self.regeneration.start()
        except ThumbnailJobRunning as e:
            return self.error_response(
                error="thumbnail_job_running", message=str(e), status_code=409
            )
        return self.success_response(data=job.to_dict(), message="Thumbnail regeneration started")

    async def cancel(self) -> APIResponse:
        job = self.regeneration.cancel()
        if job is None:
            return self.error_response(
                error="thumbnail_job_not_found",
                message="No thumbnail regeneration is running",
                status_code=404,
            )
        return self.success_response(data=job.to_dict(), message="Cancelling after the current file")

    async def get_job(self) -> APIResponse:
        return self.success_response(data=self._job_payload())


def build_admin_router(container: "AppContainer") -> APIRouter:
    """Thumbnail rendering is admin configuration, not a media read - it lives
    under /api/admin alongside the other admin-only operations."""
    controller = ThumbnailAdminController(container.settings, container.thumbnail_regeneration)

    admin_router = APIRouter(prefix="/api/admin", tags=["Media"])

    @admin_router.get("/thumbnails", response_model=APIResponse, summary="Get Thumbnail Settings")
    async def get_thumbnails(current_user=Depends(get_current_admin_user)):
        """Active thumbnail profile, the named profiles and their estimated cost,
        current disk usage, and any regeneration run."""
        return await controller.get_overview()

    @admin_router.post("/thumbnails/regenerate", response_model=APIResponse, status_code=202, summary="Regenerate Thumbnails")
    async def regenerate_thumbnails(current_user=Depends(get_current_admin_user)):
        """Start re-rendering every thumbnail whose profile is out of date."""
        return await controller.regenerate()

    @admin_router.post("/thumbnails/regenerate/cancel", response_model=APIResponse, summary="Cancel Thumbnail Regeneration")
    async def cancel_thumbnail_regeneration(current_user=Depends(get_current_admin_user)):
        """Stop the running regeneration after the file it is on."""
        return await controller.cancel()

    @admin_router.get("/thumbnails/job", response_model=APIResponse, summary="Get Thumbnail Regeneration Job")
    async def get_thumbnail_job(current_user=Depends(get_current_admin_user)):
        """The current or most recent regeneration run, or null."""
        return await controller.get_job()

    return admin_router
