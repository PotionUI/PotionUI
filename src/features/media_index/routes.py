"""Admin endpoints for the media index (system tags + queue)."""

import asyncio
from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import User

from src.features.media_index.dto import BackfillRequest, ProcessPendingRequest
from src.features.media_index.indexer import MediaIndexer
from src.features.media_index.tagger import WDTaggerProvider
from src.features.media_index.vision_embedder import SiglipVisionEmbedder

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class MediaIndexController(BaseController):
    def __init__(self, manager: MediaIndexer):
        super().__init__()
        self.manager = manager

    async def status(self, pass_type: str = None) -> APIResponse:
        try:
            return self.success_response(data=self.manager.status(pass_type))
        except Exception as e:
            self.logger.error(f"Error reading media index status: {e}")
            return self.error_api_response(error="media_index_status_failed", message=str(e))

    async def backfill(self, request: BackfillRequest) -> APIResponse:
        try:
            retagged = self.manager.retag_stale() if request.retag_stale else 0
            enqueued = self.manager.backfill(request.pass_type)
            return self.success_response(data={
                "enqueued": enqueued,
                "retag_requeued": retagged,
                "queue": self.manager.repository.queue_counts(request.pass_type),
            })
        except Exception as e:
            self.logger.error(f"Error backfilling media index: {e}")
            return self.error_api_response(error="media_index_backfill_failed", message=str(e))

    async def process_pending(self, request: ProcessPendingRequest) -> APIResponse:
        try:
            result = await asyncio.to_thread(
                self.manager.process_pending, request.pass_type, request.batch_size
            )
            return self.success_response(data={
                **result,
                "queue": self.manager.repository.queue_counts(request.pass_type),
            })
        except ValueError as e:
            return self.error_api_response(error="media_index_process_failed", message=str(e))
        except Exception as e:
            self.logger.error(f"Error processing media index queue: {e}")
            return self.error_api_response(error="media_index_process_failed", message=str(e))


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.media_index_controller
    settings = container.settings
    download_queue = container.download_queue
    router = APIRouter(prefix="/api/media-index", tags=["Media Index"])

    @router.get("/models-status", response_model=APIResponse, summary="Tagger + Vision-embedder Model Status")
    async def models_status(
        tagger_model: Optional[str] = Query(
            None, description="Override the saved tagger model id (e.g. an unsaved admin edit)"
        ),
        vision_model: Optional[str] = Query(
            None, description="Override the saved vision-embedder model id (e.g. an unsaved admin edit)"
        ),
        current_user: User = Depends(get_current_admin_user),
    ) -> APIResponse:
        """Presence/path/size of the local tagger and vision-embedder weights,
        plus whether each is currently resident in memory and any in-flight
        fetch job for it. Admin only.

        `present` is disk-only and never implies `loaded` - both models load
        through `ModelLifecycle` and can be evicted (or simply never
        yet loaded) while their weights remain on disk. The in-flight job is
        included so a reloading or reconnecting admin client can reconstruct
        "a fetch is already running" from this call alone - it never has to
        keep its own record of which download id maps to which asset.
        """
        models_dir = settings.get_models_dir()
        tagger_name = tagger_model or settings.get_setting(
            "media_tagger_model", WDTaggerProvider.DEFAULT_MODEL
        )
        vision_name = vision_model or settings.get_setting(
            "media_vision_model", SiglipVisionEmbedder.DEFAULT_MODEL
        )
        tagger_provider = controller.manager.tagger_provider
        vision_embedder = controller.manager.vision_embedder

        tagger_status = WDTaggerProvider.resolve_status(tagger_name, models_dir)
        active_tagger = download_queue.find_active_download_for_repo(tagger_name)
        tagger_status["active_download"] = active_tagger.to_dict() if active_tagger else None
        # Only the active provider instance can report residency, and only
        # for the model it was actually constructed with - an admin querying
        # an unsaved override model id gets an honest `false`, not a
        # residency reading for a different model.
        tagger_status["loaded"] = (
            tagger_provider.model_name == tagger_name and tagger_provider.is_loaded()
        )

        vision_status = SiglipVisionEmbedder.resolve_status(vision_name, models_dir)
        active_vision = download_queue.find_active_download_for_repo(vision_name)
        vision_status["active_download"] = active_vision.to_dict() if active_vision else None
        vision_status["loaded"] = (
            vision_embedder.model_name == vision_name and vision_embedder.is_loaded()
        )

        return APIResponse(
            success=True,
            data={
                "tagger": tagger_status,
                "vision": vision_status,
            },
        )

    @router.get("/status", response_model=APIResponse, summary="Media Index Status")
    async def status(
        pass_type: str = Query(None, description="Restrict queue counts to one pass type"),
        current_user: User = Depends(get_current_admin_user),
    ) -> APIResponse:
        """Queue counts, tagged-file count and the active tagger provenance."""
        return await controller.status(pass_type)

    @router.post("/backfill", response_model=APIResponse, summary="Backfill Media Index Queue")
    async def backfill(
        request: BackfillRequest,
        current_user: User = Depends(get_current_admin_user),
    ) -> APIResponse:
        """Queue untagged history; optionally requeue files tagged by an older model."""
        return await controller.backfill(request)

    @router.post("/process", response_model=APIResponse, summary="Process Media Index Queue")
    async def process_pending(
        request: ProcessPendingRequest,
        current_user: User = Depends(get_current_admin_user),
    ) -> APIResponse:
        """Drain a batch of pending queue items inline."""
        return await controller.process_pending(request)

    return router
