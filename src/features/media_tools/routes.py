import asyncio
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile
from fastapi.responses import FileResponse

from src.features.media_tools.dto import (
    CatalogResponse,
    ClipPreviewRequest,
    EffectAnalysisRequest,
    FramePreviewRequest,
    PanelsRequest,
    PreviewRequest,
    RunListResponse,
    StartRunRequest,
    StartRunResponse,
)
from src.features.media_tools.service import MediaToolService
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.media_tools import FfmpegUnavailable, ToolFailed
from src.platform.security.current_user import get_current_active_user, require_media_viewer

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

_TOOL_ERRORS = {
    FfmpegUnavailable: Fail("ffmpeg_unavailable", status=500),
    ToolFailed: Fail("tool_failed", status=500),
}


class MediaToolController(BaseController):

    def __init__(self, service: MediaToolService):
        super().__init__()
        self.service = service

    @guarded(_TOOL_ERRORS, Fail("media_tools_failed", "Failed to list media tools", 500))
    async def catalog(self, kind: Optional[str], scope: Optional[str], current_user) -> APIResponse:
        tools = await asyncio.to_thread(self.service.catalog, current_user, kind, scope)
        return self.success_response(data=CatalogResponse(tools=tools).model_dump())

    @guarded(_TOOL_ERRORS, Fail("media_tool_run_failed", "Failed to start the media tool run", 500))
    async def start_run(self, request: StartRunRequest, current_user) -> APIResponse:
        run_id = await asyncio.to_thread(
            self.service.start_run, current_user, request.steps, request.inputs, request.output
        )
        return self.success_response(data=StartRunResponse(run_id=run_id).model_dump())

    @guarded(_TOOL_ERRORS, Fail("media_tool_run_failed", "Failed to read the media tool run", 500))
    async def get_run(self, run_id: str, current_user) -> APIResponse:
        run = await asyncio.to_thread(self.service.get_run, current_user, run_id)
        return self.success_response(data=run.model_dump())

    @guarded(_TOOL_ERRORS, Fail("media_tool_run_failed", "Failed to cancel the media tool run", 500))
    async def cancel_run(self, run_id: str, current_user) -> APIResponse:
        run = await asyncio.to_thread(self.service.cancel_run, current_user, run_id)
        return self.success_response(data=run.model_dump())

    @guarded(_TOOL_ERRORS, Fail("media_tool_preview_failed", "Failed to render the preview", 500))
    async def preview(self, request: PreviewRequest, current_user) -> Response:
        data = await asyncio.to_thread(
            self.service.preview,
            current_user,
            request.tool,
            request.params,
            request.input,
            request.time_seconds,
        )
        return Response(content=data, media_type="image/webp", headers={"Cache-Control": "no-store"})

    @guarded(_TOOL_ERRORS, Fail("media_tool_run_failed", "Failed to list media tool runs", 500))
    async def list_runs(self, current_user) -> APIResponse:
        runs = await asyncio.to_thread(self.service.list_runs, current_user)
        return self.success_response(data=RunListResponse(runs=runs).model_dump())

    @guarded(_TOOL_ERRORS, Fail("media_tool_analysis_failed", "Failed to detect the panels", 500))
    async def detect_panels(self, request: PanelsRequest, current_user) -> APIResponse:
        found = await asyncio.to_thread(self.service.detect_panels, current_user, request)
        return self.success_response(data=found)

    @guarded(_TOOL_ERRORS, Fail("media_tool_preview_failed", "Failed to render the preview", 500))
    async def preview_frame(self, request: FramePreviewRequest, current_user) -> Response:
        frame = await asyncio.to_thread(
            self.service.preview_frame,
            current_user,
            request.input,
            request.steps,
            request.time_seconds,
            request.max_side,
            request.format,
        )
        headers = {
            "Cache-Control": "no-store",
            "X-Preview-Cache": "hit" if frame.cached else "miss",
            "X-Frame-Width": str(frame.width),
            "X-Frame-Height": str(frame.height),
        }
        return Response(content=frame.data, media_type=frame.media_type, headers=headers)

    @guarded(_TOOL_ERRORS, Fail("media_tool_preview_failed", "Failed to start the preview", 500))
    async def preview_clip(self, request: ClipPreviewRequest, current_user) -> APIResponse:
        started = await asyncio.to_thread(
            self.service.start_clip_preview,
            current_user,
            request.input,
            request.steps,
            request.start_seconds,
            request.duration_seconds,
            request.height,
        )
        return self.success_response(data=started.model_dump())

    @guarded(_TOOL_ERRORS, Fail("media_tool_preview_failed", "Failed to read the preview", 500))
    async def preview_clip_file(self, preview_id: str, current_user) -> FileResponse:
        path, media_type = await asyncio.to_thread(self.service.preview_clip_file, current_user, preview_id)
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=300"})

    @guarded(_TOOL_ERRORS, Fail("media_tools_failed", "Failed to read the effect shader", 500))
    async def effect_shader(self, tool_id: str, if_none_match: Optional[str], current_user) -> Response:
        text, etag = self.service.effect_shader(tool_id)
        headers = {"ETag": etag, "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"}
        if if_none_match and etag in [tag.strip() for tag in if_none_match.split(",")]:
            return Response(status_code=304, headers=headers)
        return Response(content=text, media_type="text/plain; charset=utf-8", headers=headers)

    @guarded(_TOOL_ERRORS, Fail("media_tool_preview_failed", "Failed to measure the effect", 500))
    async def effect_analysis(self, request: EffectAnalysisRequest, current_user) -> APIResponse:
        result = await asyncio.to_thread(
            self.service.effect_analysis, current_user, request.input, request.steps, request.step_index
        )
        return self.success_response(data=result.model_dump())

    @guarded(_TOOL_ERRORS, Fail("upload_failed", "Failed to store the file", 400))
    async def upload_artifact(self, file: UploadFile, current_user) -> APIResponse:
        await asyncio.to_thread(
            self.service.check_artifact, current_user, getattr(file, "size", None), file.content_type
        )
        data = await file.read()
        artifact = await self.service.upload_artifact(current_user, data, file.filename, file.content_type)
        return self.success_response(data=artifact.model_dump())


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.media_tool_controller
    router = APIRouter(tags=["Media Tools"])

    @router.get("/api/media-tools", response_model=APIResponse, summary="List Media Tools")
    async def list_media_tools(
        kind: Optional[str] = Query(None),
        scope: Optional[str] = Query(None),
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.catalog(kind, scope, current_user)

    @router.get("/api/media-tools/runs", response_model=APIResponse, summary="List My Media Tool Runs")
    async def list_media_tool_runs(current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.list_runs(current_user)

    @router.post(
        "/api/media-tools/runs", response_model=APIResponse, status_code=202, summary="Start A Media Tool Run"
    )
    async def start_media_tool_run(
        request: StartRunRequest, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.start_run(request, current_user)

    @router.get("/api/media-tools/runs/{run_id}", response_model=APIResponse, summary="Get A Media Tool Run")
    async def get_media_tool_run(
        run_id: str, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.get_run(run_id, current_user)

    @router.delete(
        "/api/media-tools/runs/{run_id}", response_model=APIResponse, summary="Cancel A Media Tool Run"
    )
    async def cancel_media_tool_run(
        run_id: str, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.cancel_run(run_id, current_user)

    @router.post("/api/media-tools/analyse/panels", response_model=APIResponse, summary="Detect Panels In A Picture")
    async def detect_media_tool_panels(
        request: PanelsRequest, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.detect_panels(request, current_user)

    @router.post("/api/media-tools/preview", response_model=None, summary="Preview A Media Tool")
    async def preview_media_tool(
        request: PreviewRequest, current_user=Depends(get_current_active_user)
    ) -> Response:
        return await controller.preview(request, current_user)

    @router.post("/api/media-tools/preview/frame", response_model=None, summary="Preview One Frame Of An Edit Stack")
    async def preview_media_tool_frame(
        request: FramePreviewRequest, current_user=Depends(get_current_active_user)
    ) -> Response:
        return await controller.preview_frame(request, current_user)

    @router.post(
        "/api/media-tools/preview/clip", response_model=APIResponse, status_code=202,
        summary="Render A Short Preview Clip Of An Edit Stack",
    )
    async def preview_media_tool_clip(
        request: ClipPreviewRequest, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.preview_clip(request, current_user)

    @router.get("/api/media-tools/previews/{preview_id}", response_model=None, summary="Get A Preview Clip")
    async def get_media_tool_preview(
        preview_id: str, current_user=Depends(require_media_viewer)
    ) -> FileResponse:
        return await controller.preview_clip_file(preview_id, current_user)

    @router.get(
        "/api/media-tools/effects/{tool_id}/shader", response_model=None, summary="Get An Effect Preview Shader"
    )
    async def get_media_tool_effect_shader(
        tool_id: str,
        if_none_match: Optional[str] = Header(None),
        current_user=Depends(get_current_active_user),
    ) -> Response:
        return await controller.effect_shader(tool_id, if_none_match, current_user)

    @router.post(
        "/api/media-tools/effects/analysis", response_model=APIResponse, summary="Measure An Effect Over A Clip"
    )
    async def measure_media_tool_effect(
        request: EffectAnalysisRequest, current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.effect_analysis(request, current_user)

    @router.post("/api/media-tools/artifacts", response_model=APIResponse, summary="Upload An Editor Artifact")
    async def upload_media_tool_artifact(
        file: UploadFile = File(...), current_user=Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.upload_artifact(file, current_user)

    return router
