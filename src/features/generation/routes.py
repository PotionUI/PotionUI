import io
import logging
import traceback
from typing import List, Optional, TYPE_CHECKING
from fastapi import APIRouter, WebSocket, Depends, Query, UploadFile, File as FastAPIFile, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, PlainTextResponse

# Import services - needed for injector
from src.platform.filesystem import FileStore
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation import profile_paths
from src.platform.observability.profiling import render_report_from_file

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.generation.dto import (
    ClearTabQueueRequest,
    GenerationRequest,
    GenerationStatus,
    UpdateTagsRequest,
    BulkDeleteRequest,
    BulkDeleteByTagsRequest,
    RatingRequest,
    FavoriteRequest,
    ExportRequest,
)
from src.platform.websocket import ConnectionManager
from src.features.generation.websocket_handler import WebSocketHandler
from src.features.forms.exceptions import FormNotFoundException
from src.features.forms.binding import FormBindingError
from src.features.models.exceptions import ModelNotFoundException, ModelAccessDeniedException
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.run_report_recorder import RunReportRecorder
from src.pipelines.outputs import GenerationOutput
from src.features.generation import (
    GenerationHistoryManager,
    GenerationNotFoundException,
    GenerationDeleteFailedException,
    UploadFailedException,
    InvalidTagException,
    InvalidDateFilterException,
    InvalidGenerationSourceException,
    GenerationBundleImportError,
    GenerationPolicy,
)
from src.features.generation.repository import generation_repo
from src.features.generation.file_repository import file_repo

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class GenerationController(BaseController):
    def __init__(
        self,
        generation_orchestrator: GenerationOrchestrator,
        generation_history_manager: GenerationHistoryManager,
        file_service: FileStore,
        run_report_recorder: RunReportRecorder
    ):
        super().__init__()  # Initialize BaseController
        self.generation_orchestrator = generation_orchestrator
        self.history_manager = generation_history_manager
        self.history_query = generation_history_manager.query
        self.file_service = file_service
        self.run_report_recorder = run_report_recorder
        self.connection_manager = ConnectionManager()
        self.websocket_handler = WebSocketHandler(self.connection_manager)
        # Queued generations have no outputs to broadcast yet, so position
        # changes reach the client through this side channel instead.
        self.generation_orchestrator.set_queue_listener(self._broadcast_queue_update)

    async def _broadcast_queue_update(self, generation_id: str, message: dict) -> None:
        """Push a `queue_update` to the clients subscribed to this generation."""
        await self.connection_manager.broadcast_to_generation(generation_id, message)

    async def start_generation(self, request: GenerationRequest, current_user) -> APIResponse:
        """Start a new generation using the generation orchestrator"""
        try:
            # Delegate to generation orchestrator
            result = await self.generation_orchestrator.start_generation(
                request,
                current_user.id,
                output_callback=self._handle_generation_output
            )

            return self.success_response(data=result)

        except FormNotFoundException as e:
            return self.error_response(
                error="form_not_found",
                message=str(e),
                status_code=404
            )
        except FormBindingError as e:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "form_validation_failed",
                    "field_errors": e.field_errors,
                    "coercions": e.coercions,
                    "stripped": e.stripped,
                    "message": str(e),
                },
            )
        except (ModelNotFoundException, ModelAccessDeniedException):
            # 404 (not 403/422) for a model the user may not reach - same
            # existence-concealing rationale as GenerationPolicy: a 403 (or a
            # message naming the model) would confirm the model id exists.
            return self.error_response(
                error="model_not_found",
                message="One or more selected models are not available",
                status_code=404
            )
        except InvalidGenerationSourceException as e:
            # Same 404-not-403 rationale as the model-access block above,
            # applied to `<field>__origin` source-generation references.
            return self.error_response(
                error="source_generation_not_found",
                message=str(e),
                status_code=404
            )
        except ValueError as e:
            return self.error_response(
                error="validation_error",
                message=str(e),
                status_code=400
            )
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(error_details)
            return self.error_response(
                error="generation_start_failed",
                message=f"Failed to start generation: {str(e)}"
            )

    async def _handle_generation_output(self, generation_id: str, output: GenerationOutput):
        """Handle generation output and broadcast to WebSocket clients"""
        # Get generation status from orchestrator
        status = await self.generation_orchestrator.get_generation_status(generation_id)
        if not status:
            return

        # Check for completion signal
        if output is None:
            status_dict = status.model_dump()
            try:
                self.run_report_recorder.flush(
                    generation_id,
                    terminal_status=status_dict.get('status'),
                    terminal_message=status_dict.get('message'),
                )
            except Exception:
                logging.exception(f"Failed to flush run report for {generation_id}")
            await self.connection_manager.broadcast_to_generation(
                generation_id,
                {'type': 'generation_complete', 'data': status_dict}
            )
            return

        # Serialize and broadcast the output
        await self._broadcast_generation_output(generation_id, output, status)


    async def _broadcast_generation_output(self, generation_id: str, output: GenerationOutput, status):
        """Record and broadcast generation output.

        The run report is recorded unconditionally (a generation nobody is
        watching still gets a report); only the WebSocket broadcast itself is
        gated on having subscribers.
        """
        has_subscribers = (
            generation_id in self.connection_manager.generation_connections and
            len(self.connection_manager.generation_connections[generation_id]) > 0
        )

        try:
            # Create serializer instance
            serializer = GenerationOutputSerializer(
                generation_id=generation_id,
                preset_id=status.preset_id
            )

            # Serialize the output
            message = serializer.serialize_output(output)

            try:
                self.run_report_recorder.record_output(generation_id, message)
            except Exception:
                logging.exception(f"Failed to record run report output for {generation_id}")

            if not has_subscribers:
                return

            # Broadcast the message
            await self.connection_manager.broadcast_to_generation(generation_id, message)

        except Exception as e:
            logging.error(f"Failed to broadcast generation output: {str(e)}")

            # Send error message
            await self.connection_manager.broadcast_to_generation(
                generation_id,
                {
                    'type': 'generation_error',
                    'data': {
                        'generation_id': generation_id,
                        'error': f"Output processing failed: {str(e)}",
                        'status': status.model_dump()
                    }
                }
            )

    async def _resolve_generation_owner(self, generation_id: str):
        """Resolve the owning user id for a generation.

        Checks the live status tracker first (active generations), then falls
        back to the history database. Returns ``(exists, owner_id)`` where
        ``exists`` is False when the generation is unknown in both places.
        """
        record = await self.generation_orchestrator.get_generation_status(generation_id)
        if record is not None:
            return True, getattr(record, "user_id", None)

        generation = generation_repo.get_by_id(generation_id)
        if generation is not None:
            return True, getattr(generation, "user_id", None)

        return False, None

    async def get_generation_status(self, generation_id: str, current_user) -> APIResponse:
        """Get status of a specific generation"""
        status = await self.generation_orchestrator.get_generation_status(generation_id)

        if not status:
            # The status tracker is in-memory: uploaded generations never enter
            # it, and finished runs fall out on restart/prune. A generation that
            # still has a DB row must not report not-found - the frontend's
            # reload-restore path takes a 404 as "gone" and silently drops the
            # tab's completed result.
            generation = generation_repo.get_by_id(generation_id)
            if generation is None:
                return self.error_response(
                    error="generation_not_found",
                    message=f"Generation '{generation_id}' not found",
                    status_code=404
                )
            if not GenerationPolicy.can_access(current_user, getattr(generation, "user_id", None)):
                return self.error_response(
                    error="generation_not_found",
                    message=f"Generation '{generation_id}' not found",
                    status_code=404
                )
            return self.success_response(data=generation.to_dict())

        # Return 404 (not 403) for a non-owner: a 403 would confirm that the
        # generation id exists and belongs to someone else.
        if not GenerationPolicy.can_access(current_user, getattr(status, "user_id", None)):
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )

        return self.success_response(data=status.model_dump())

    async def get_generation_profile(
        self, generation_id: str, current_user, file: Optional[str] = None,
        format: Optional[str] = None,
    ):
        """
        Serve an artifact from the per-generation resource profiler (see
        ``src.platform.observability.profiling``), when profiling was enabled for
        this run. Admin-gated at the route (profiles bundle captured application
        logs).

        - ``format=report`` renders the human-readable text report (the same
          output as ``scripts/profile_report.py``) as ``text/plain``.
        - ``file=log`` serves the ``generation.log`` cut sitting next to the
          profile.
        - default serves the raw ``profile.jsonl`` download.

        404 when the requested file doesn't exist -- either profiling was off, or
        the generation hasn't produced one yet. The id must be a single path
        segment; an unsafe id is treated as a missing profile (404).
        """
        base_dir = self.file_service.base_storage_dir
        jsonl_path = profile_paths.profile_jsonl_path(base_dir, generation_id)

        if format == "report":
            if jsonl_path is None or not jsonl_path.is_file():
                return self.error_response(
                    error="profile_not_found",
                    message=f"No profile found for generation '{generation_id}'",
                    status_code=404,
                )
            report_text = render_report_from_file(jsonl_path)
            return PlainTextResponse(report_text)

        if file == "log":
            target_path = profile_paths.profile_log_path(base_dir, generation_id)
            media_type, filename = "text/plain", "generation.log"
        else:
            target_path = jsonl_path
            media_type, filename = "application/x-ndjson", "profile.jsonl"

        if target_path is None or not target_path.is_file():
            return self.error_response(
                error="profile_not_found",
                message=f"No {filename} found for generation '{generation_id}'",
                status_code=404,
            )
        return FileResponse(str(target_path), media_type=media_type, filename=filename)

    async def clear_tab_queue(self, tab_id: str, current_user) -> APIResponse:
        """
        Drop every generation this tab has queued but not yet started.

        Scoped to the calling user, so one user's tab id can never clear
        another's queue - tab ids are minted client-side and collide freely
        across users.
        """
        try:
            cancelled = await self.generation_orchestrator.clear_tab_queue(
                current_user.id, tab_id
            )

            for generation_id in cancelled:
                status = await self.generation_orchestrator.get_generation_status(generation_id)
                if status:
                    await self.connection_manager.broadcast_to_generation(
                        generation_id,
                        {'type': 'generation_cancelled', 'data': status.model_dump()}
                    )

            return self.success_response(
                data={'cancelled': cancelled, 'count': len(cancelled)},
                message=f"Cleared {len(cancelled)} queued generation(s)"
            )

        except Exception as e:
            logging.error(f"Failed to clear queue for tab {tab_id}: {str(e)}")
            return self.error_response(
                error="clear_queue_failed",
                message=f"Failed to clear tab queue: {str(e)}"
            )

    async def get_queue(self, current_user, tab_id: Optional[str] = None) -> APIResponse:
        """The caller's pending generations in FIFO order, plus their running ones."""
        try:
            return self.success_response(
                data=self.generation_orchestrator.get_queue_snapshot(current_user.id, tab_id)
            )
        except Exception as e:
            logging.error(f"Failed to read generation queue: {str(e)}")
            return self.error_response(
                error="queue_read_failed",
                message=f"Failed to read generation queue: {str(e)}"
            )

    async def cancel_generation(self, generation_id: str, current_user) -> APIResponse:
        """Cancel a running generation"""
        # Enforce ownership before cancelling. Return 404 (not 403) on a denied
        # non-owner so the response can't be used to probe for the existence of
        # another user's generation ids. This runs outside the try/except below
        # so the 404 isn't swallowed and re-wrapped as a generic cancel error.
        exists, owner_id = await self._resolve_generation_owner(generation_id)
        if exists and not GenerationPolicy.can_access(current_user, owner_id):
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )

        try:
            success = await self.generation_orchestrator.cancel_generation(generation_id)

            if not success:
                return self.error_response(
                    error="cancel_failed",
                    message="Failed to cancel generation",
                    status_code=400
                )

            # Broadcast cancellation to WebSocket clients
            status = await self.generation_orchestrator.get_generation_status(generation_id)
            if status:
                await self.connection_manager.broadcast_to_generation(
                    generation_id,
                    {'type': 'generation_cancelled', 'data': status.model_dump()}
                )

            return self.success_response(message="Generation cancelled successfully")

        except Exception as e:
            logging.error(f"Failed to cancel generation {generation_id}: {str(e)}")
            return self.error_response(
                error="cancel_failed",
                message=f"Failed to cancel generation: {str(e)}"
            )

    async def list_generations(self, current_user) -> APIResponse:
        """List active generations visible to the caller.

        Regular users see only their own active generations; administrators
        see every user's. Filtering happens over the status-tracker records
        (which carry ``user_id``) rather than the serialized dicts, which do
        not expose the owner.
        """
        visible = [
            record.model_dump()
            for record in self.generation_orchestrator.status_tracker.list_active()
            if GenerationPolicy.can_access(current_user, record.user_id)
        ]
        return self.success_response(data=visible)

    async def get_generation_history(
        self,
        current_user,
        limit: Optional[int] = 50,
        offset: int = 0,
        status: Optional[str] = None,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
        completed_from: Optional[str] = None,
        completed_to: Optional[str] = None,
        tag_ids: Optional[str] = None,
        include_tags: bool = True,
        media_type: Optional[str] = None,
        search: Optional[str] = None,
        mode: Optional[str] = None,
        preset_id: Optional[str] = None,
        model_name: Optional[str] = None,
        min_rating: Optional[int] = None,
        favorites_only: bool = False,
        collection_id: Optional[str] = None,
        used_phrasebook_value_id: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        system_tag: Optional[str] = None,
        semantic_query: Optional[str] = None
    ) -> APIResponse:
        """Get generation history from database with optional filtering"""
        try:
            # Parse tag_ids if provided
            parsed_tag_ids = None
            if tag_ids:
                parsed_tag_ids = [tid.strip() for tid in tag_ids.split(',') if tid.strip()]

            result = self.history_manager.get_history(
                user_id=current_user.id,
                limit=limit,
                offset=offset,
                status=status,
                created_from=created_from,
                created_to=created_to,
                completed_from=completed_from,
                completed_to=completed_to,
                tag_ids=parsed_tag_ids,
                include_tags=include_tags,
                media_type=media_type,
                search=search,
                mode=mode,
                preset_id=preset_id,
                model_name=model_name,
                min_rating=min_rating,
                favorites_only=favorites_only,
                collection_id=collection_id,
                used_phrasebook_value_id=used_phrasebook_value_id,
                sort_by=sort_by,
                sort_dir=sort_dir,
                system_tag=system_tag,
                semantic_query=semantic_query
            )

            return self.success_response(data=result)

        except InvalidDateFilterException as e:
            return self.error_response(
                error="invalid_date_format",
                message=str(e),
                status_code=400
            )
        except Exception as e:
            logging.error(f"Failed to get generation history: {str(e)}")
            return self.error_response(
                error="history_fetch_failed",
                message=f"Failed to fetch generation history: {str(e)}"
            )

    async def get_generation_by_id(
        self,
        generation_id: str,
        current_user,
        include_files: bool = True
    ) -> APIResponse:
        """Get specific generation by ID from database"""
        try:
            result = self.history_manager.get_by_id(
                generation_id=generation_id,
                user_id=current_user.id,
                include_files=include_files
            )
            return self.success_response(data=result)

        except GenerationNotFoundException:
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )
        except Exception as e:
            logging.error(f"Failed to get generation {generation_id}: {str(e)}")
            return self.error_response(
                error="generation_fetch_failed",
                message=f"Failed to fetch generation: {str(e)}"
            )

    async def get_generation_params(
        self,
        generation_id: str,
        index: int,
        current_user
    ) -> APIResponse:
        """Get parameters for a specific generation and image index"""
        try:
            result = self.history_query.get_params(
                generation_id=generation_id,
                index=index,
                user_id=current_user.id
            )
            return self.success_response(data=result)

        except GenerationNotFoundException:
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )
        except Exception as e:
            logging.error(f"Error getting generation parameters: {str(e)}")
            return self.error_response(
                error="params_fetch_failed",
                message=f"Error retrieving generation parameters: {str(e)}"
            )

    async def set_generation_rating(
        self,
        generation_id: str,
        rating: int,
        current_user
    ) -> APIResponse:
        """Set the star rating (0-5) for a generation."""
        try:
            value = self.history_manager.set_rating(generation_id, rating, current_user.id)
            return self.success_response(data={"id": generation_id, "rating": value})
        except GenerationNotFoundException:
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )
        except ValueError as e:
            return self.error_response(error="invalid_rating", message=str(e), status_code=400)
        except Exception as e:
            logging.error(f"Failed to set rating for {generation_id}: {str(e)}")
            return self.error_response(error="rating_update_failed", message=str(e))

    async def set_generation_favorite(
        self,
        generation_id: str,
        is_favorite: bool,
        current_user
    ) -> APIResponse:
        """Set the favorite flag for a generation."""
        try:
            value = self.history_manager.set_favorite(generation_id, is_favorite, current_user.id)
            return self.success_response(data={"id": generation_id, "is_favorite": value})
        except GenerationNotFoundException:
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )
        except Exception as e:
            logging.error(f"Failed to set favorite for {generation_id}: {str(e)}")
            return self.error_response(error="favorite_update_failed", message=str(e))

    async def get_history_facets(self, current_user) -> APIResponse:
        """Get distinct modes, presets and models for history filter controls."""
        try:
            result = self.history_query.get_facets(current_user.id)
            return self.success_response(data=result)
        except Exception as e:
            logging.error(f"Failed to get history facets: {str(e)}")
            return self.error_response(error="facets_fetch_failed", message=str(e))

    async def delete_generation_history(
        self,
        generation_id: str,
        current_user
    ) -> APIResponse:
        """Delete generation from history and its files"""
        try:
            result = self.history_manager.delete(
                generation_id=generation_id,
                user_id=current_user.id
            )

            message = f"Generation deleted successfully. "
            message += f"Removed {result['files_deleted_fs']} files from filesystem, "
            message += f"{result['files_deleted_db']} file records from database."
            if result.get('files_failed_fs', 0) > 0:
                message += f" Warning: Failed to delete {result['files_failed_fs']} files from filesystem."

            return self.success_response(message=message)

        except GenerationNotFoundException:
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )
        except GenerationDeleteFailedException as e:
            return self.error_response(
                error="delete_failed",
                message=str(e)
            )
        except Exception as e:
            logging.error(f"Failed to delete generation {generation_id}: {str(e)}")
            return self.error_response(
                error="delete_failed",
                message=f"Failed to delete generation: {str(e)}"
            )

    async def bulk_delete_generations(
        self,
        generation_ids: List[str],
        current_user
    ) -> APIResponse:
        """Delete multiple generations and their files"""
        if not generation_ids:
            return self.error_response(
                error="invalid_request",
                message="No generation IDs provided",
                status_code=400
            )

        try:
            result = self.history_manager.bulk_delete(
                generation_ids=generation_ids,
                user_id=current_user.id
            )

            # Build response message
            message = f"Successfully deleted {result['deleted_count']} generation(s). "
            message += f"Removed {result['total_files_deleted']} files from filesystem, "
            message += f"{result.get('total_files_deleted_db', 0)} file records from database."

            if result.get('total_files_failed_fs', 0) > 0:
                message += f" Warning: Failed to delete {result['total_files_failed_fs']} files from filesystem."

            if result['failed_count'] > 0:
                message += f" Failed to delete {result['failed_count']} generation(s)."

            return self.success_response(
                message=message,
                data={
                    "deleted_count": result['deleted_count'],
                    "failed_count": result['failed_count'],
                    "failed_ids": result['failed_ids'],
                    "total_files_deleted": result['total_files_deleted']
                }
            )

        except GenerationDeleteFailedException as e:
            return self.error_response(
                error="bulk_delete_blocked",
                message=str(e)
            )
        except Exception as e:
            logging.error(f"Failed to bulk delete generations: {str(e)}")
            return self.error_response(
                error="bulk_delete_failed",
                message=f"Failed to bulk delete generations: {str(e)}"
            )

    async def export_generations(
        self,
        generation_ids: List[str],
        strip_metadata: bool,
        current_user
    ):
        """Export the final files of multiple generations as a downloadable zip.

        Unlike the other endpoints this returns the binary zip directly (via a
        StreamingResponse) instead of an APIResponse JSON envelope.
        """
        if not generation_ids:
            return self.error_response(
                error="invalid_request",
                message="No generation IDs provided",
                status_code=400
            )

        try:
            zip_bytes, filename = self.history_manager.export_zip(
                generation_ids=generation_ids,
                user_id=current_user.id,
                strip_metadata=strip_metadata
            )

            return StreamingResponse(
                io.BytesIO(zip_bytes),
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(len(zip_bytes)),
                }
            )

        except GenerationNotFoundException as e:
            return self.error_response(
                error="generation_not_found",
                message=str(e),
                status_code=404
            )
        except Exception as e:
            logging.error(f"Failed to export generations: {str(e)}")
            logging.error(traceback.format_exc())
            return self.error_response(
                error="export_failed",
                message=f"Failed to export generations: {str(e)}"
            )

    async def export_generation_bundle(self, generation_id: str, current_user):
        """Portable bundle for one generation - envelope + final output files -
        another PotionUI instance can import to reproduce the same output.

        Unlike the other endpoints this returns the binary zip directly (via a
        StreamingResponse) instead of an APIResponse JSON envelope.
        """
        try:
            zip_bytes, filename = self.history_manager.export_bundle(
                generation_id=generation_id,
                user_id=current_user.id
            )

            return StreamingResponse(
                io.BytesIO(zip_bytes),
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(len(zip_bytes)),
                }
            )

        except GenerationNotFoundException as e:
            return self.error_response(
                error="generation_not_found",
                message=str(e),
                status_code=404
            )
        except Exception as e:
            logging.error(f"Failed to export generation bundle: {str(e)}")
            logging.error(traceback.format_exc())
            return self.error_response(
                error="export_failed",
                message=f"Failed to export generation bundle: {str(e)}"
            )

    async def import_generation_bundle(self, file: UploadFile, current_user) -> APIResponse:
        """Parse an uploaded generation bundle (zip or bare generation.json)
        into a reuse payload. Never creates a generation record."""
        try:
            content = await file.read()
            result = self.history_manager.import_bundle(content)

            return self.success_response(
                message="Bundle parsed successfully",
                data=result
            )

        except GenerationBundleImportError as e:
            return self.error_response(
                error="invalid_bundle",
                message=str(e),
                status_code=400
            )
        except Exception as e:
            logging.error(f"Failed to import generation bundle: {str(e)}")
            logging.error(traceback.format_exc())
            return self.error_response(
                error="import_failed",
                message=f"Failed to import generation bundle: {str(e)}"
            )

    async def count_generations_by_tags(
        self,
        tag_ids: List[str],
        current_user
    ) -> APIResponse:
        """Count generations matching ALL specified tags (for preview)."""
        if not tag_ids:
            return self.error_response(
                error="invalid_request",
                message="No tag IDs provided",
                status_code=400
            )

        try:
            count = self.history_query.count_generations_by_tags(
                tag_ids=tag_ids,
                user_id=current_user.id
            )
            return self.success_response(
                message=f"Found {count} generation(s) matching all specified tags",
                data={"count": count}
            )
        except InvalidTagException as e:
            return self.error_response(
                error="invalid_tag",
                message=str(e),
                status_code=400
            )
        except Exception as e:
            logging.error(f"Failed to count generations by tags: {str(e)}")
            return self.error_response(
                error="count_failed",
                message=f"Failed to count generations by tags: {str(e)}"
            )

    async def bulk_delete_by_tags(
        self,
        tag_ids: List[str],
        current_user
    ) -> APIResponse:
        """Delete all generations matching ALL specified tags."""
        if not tag_ids:
            return self.error_response(
                error="invalid_request",
                message="No tag IDs provided",
                status_code=400
            )

        try:
            result = self.history_manager.bulk_delete_by_tags(
                tag_ids=tag_ids,
                user_id=current_user.id
            )

            message = f"Successfully deleted {result['deleted_count']} generation(s) matching all specified tags."
            if result['failed_count'] > 0:
                message += f" Failed to delete {result['failed_count']} generation(s)."

            return self.success_response(
                message=message,
                data={
                    "deleted_count": result['deleted_count'],
                    "failed_count": result['failed_count'],
                    "failed_ids": result.get('failed_ids', []),
                    "total_files_deleted": result.get('total_files_deleted', 0)
                }
            )
        except InvalidTagException as e:
            return self.error_response(
                error="invalid_tag",
                message=str(e),
                status_code=400
            )
        except GenerationDeleteFailedException as e:
            return self.error_response(
                error="bulk_delete_blocked",
                message=str(e)
            )
        except Exception as e:
            logging.error(f"Failed to bulk delete by tags: {str(e)}")
            return self.error_response(
                error="bulk_delete_failed",
                message=f"Failed to bulk delete generations by tags: {str(e)}"
            )

    async def upload_generations(
        self,
        files: List[UploadFile],
        tag_ids: List[str],
        current_user
    ) -> APIResponse:
        """Upload files as completed generations"""
        try:
            result = await self.history_manager.upload_generations(
                files=files,
                tag_ids=tag_ids,
                user_id=current_user.id
            )

            return self.success_response(
                message=f"Successfully uploaded {len(result['files'])} file(s)",
                data=result
            )

        except UploadFailedException as e:
            return self.error_response(
                error="upload_failed",
                message=str(e),
                status_code=400
            )
        except InvalidTagException as e:
            return self.error_response(
                error="invalid_tag",
                message=str(e),
                status_code=400
            )
        except Exception as e:
            logging.error(f"Failed to upload generations: {str(e)}")
            logging.error(traceback.format_exc())
            return self.error_response(
                error="upload_failed",
                message=f"Failed to upload files: {str(e)}"
            )

    async def get_generation_tags(
        self,
        generation_id: str,
        current_user
    ) -> APIResponse:
        """Get all tags for a generation"""
        try:
            tags = self.history_manager.get_tags(
                generation_id=generation_id,
                user_id=current_user.id
            )
            return self.success_response(data={"tags": tags})

        except GenerationNotFoundException:
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )
        except Exception as e:
            logging.error(f"Error getting tags for generation {generation_id}: {str(e)}")
            return self.error_response(
                error="fetch_tags_failed",
                message=f"Failed to get tags: {str(e)}"
            )

    async def update_generation_tags(
        self,
        generation_id: str,
        tag_ids: List[str],
        current_user
    ) -> APIResponse:
        """Replace all tags for a generation"""
        try:
            tags = self.history_manager.update_tags(
                generation_id=generation_id,
                tag_ids=tag_ids,
                user_id=current_user.id
            )
            return self.success_response(data={
                "message": "Generation tags updated successfully",
                "tags": tags
            })

        except GenerationNotFoundException:
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )
        except InvalidTagException as e:
            return self.error_response(
                error="invalid_tag",
                message=str(e),
                status_code=400
            )
        except Exception as e:
            logging.error(f"Error updating tags for generation {generation_id}: {str(e)}")
            return self.error_response(
                error="update_tags_failed",
                message=f"Failed to update tags: {str(e)}"
            )

    async def remove_generation_tag(
        self,
        generation_id: str,
        tag_id: str,
        current_user
    ) -> APIResponse:
        """Remove a single tag from a generation"""
        try:
            success = self.history_manager.remove_tag(
                generation_id=generation_id,
                tag_id=tag_id,
                user_id=current_user.id
            )

            if success:
                return self.success_response(data={"message": "Tag removed successfully"})
            else:
                return self.error_response(
                    error="remove_failed",
                    message="Failed to remove tag or tag not found"
                )

        except GenerationNotFoundException:
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )
        except Exception as e:
            logging.error(f"Error removing tag from generation {generation_id}: {str(e)}")
            return self.error_response(
                error="remove_tag_failed",
                message=f"Failed to remove tag: {str(e)}"
            )

    async def admin_list_generations(
        self,
        user_id: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: int = 0,
        status: Optional[str] = None,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
        completed_from: Optional[str] = None,
        completed_to: Optional[str] = None,
        media_type: Optional[str] = None,
        search: Optional[str] = None,
        mode: Optional[str] = None,
        preset_id: Optional[str] = None,
        model_name: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
    ) -> APIResponse:
        """Global generation history across every user (admin only).

        Reuses the same filter/query machinery as the per-user history
        endpoint - `user_id` is a filter here, not scoping, and defaults to
        None (every user).
        """
        try:
            result = self.history_manager.get_history(
                user_id=user_id,
                limit=limit,
                offset=offset,
                status=status,
                created_from=created_from,
                created_to=created_to,
                completed_from=completed_from,
                completed_to=completed_to,
                include_tags=False,
                media_type=media_type,
                search=search,
                mode=mode,
                preset_id=preset_id,
                model_name=model_name,
                sort_by=sort_by,
                sort_dir=sort_dir,
            )

            generation_ids = [g['id'] for g in result['generations']]
            report_ids = self.run_report_recorder.has_reports(generation_ids)
            for gen_dict in result['generations']:
                gen_dict['has_run_report'] = gen_dict['id'] in report_ids

            return self.success_response(data=result)

        except InvalidDateFilterException as e:
            return self.error_response(
                error="invalid_date_format",
                message=str(e),
                status_code=400
            )
        except Exception as e:
            logging.error(f"Failed to get admin generation history: {str(e)}")
            return self.error_response(
                error="history_fetch_failed",
                message=f"Failed to fetch generation history: {str(e)}"
            )

    async def admin_get_generation(self, generation_id: str) -> APIResponse:
        """Full generation record + its run report (admin only). 404, not 403 -
        an admin already has global visibility so there is no owner to hide."""
        generation = generation_repo.get_by_id(generation_id, include_files=True)
        if generation is None:
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )

        report = self.run_report_recorder.get_report(generation_id)
        if report is not None:
            prompt = None
            if isinstance(generation.form_data, dict):
                prompt = generation.form_data.get('prompt')
            report = {**report, 'prompt_template': prompt}

        return self.success_response(data={
            'generation': generation.to_dict(include_files=True, include_tags=True),
            'run_report': report,
        })

    async def handle_websocket(self, websocket, client_id: str, user=None):
        """Handle WebSocket connection for real-time updates"""
        await self.websocket_handler.handle_websocket(
            websocket, client_id, self.generation_orchestrator.status_tracker, user
        )


def _get_generation_controller(container: "AppContainer") -> GenerationController:
    """Build (or reuse) the single `GenerationController` for this container.

    `build_router` and `build_ws_router` must share one controller instance:
    the HTTP side broadcasts generation output through
    `controller.connection_manager`, and the WebSocket side subscribes
    clients to that same `ConnectionManager`. Constructing a fresh
    `GenerationController` per factory call would give each router its own
    `ConnectionManager` and silently break that broadcast path. The instance
    is cached on the container itself, so it's shared regardless of call
    order.
    """
    controller = getattr(container, "_generation_controller", None)
    if controller is None:
        controller = GenerationController(
            container.generation_orchestrator,
            container.generation_history_manager,
            container.file_service,
            container.run_report_recorder,
        )
        container._generation_controller = controller
    return controller


def build_router(container: "AppContainer") -> APIRouter:
    controller = _get_generation_controller(container)

    router = APIRouter(prefix="/api/generations", tags=["Generation"])

    @router.post("/start", response_model=APIResponse, summary="Start Generation")
    async def start_generation(request: GenerationRequest, current_user = Depends(get_current_active_user)):
        """Start a new image generation job with the specified parameters and preset."""
        return await controller.start_generation(request, current_user)

    @router.get("/{generation_id}/status", response_model=APIResponse, summary="Get Generation Status")
    async def get_generation_status(generation_id: str, current_user = Depends(get_current_active_user)):
        """Get the current status and progress of a specific generation job."""
        return await controller.get_generation_status(generation_id, current_user)

    @router.post("/{generation_id}/cancel", response_model=APIResponse, summary="Cancel Generation")
    async def cancel_generation(generation_id: str, current_user = Depends(get_current_active_user)):
        """Cancel a currently running generation job."""
        return await controller.cancel_generation(generation_id, current_user)

    @router.get("/{generation_id}/profile", summary="Get Generation Resource Profile")
    async def get_generation_profile(
        generation_id: str,
        file: Optional[str] = None,
        format: Optional[str] = None,
        current_user = Depends(get_current_admin_user),
    ):
        """Serve the per-generation resource profile. Admin-only: profiles bundle
        captured application logs. ``?format=report`` renders the text report,
        ``?file=log`` serves the ``generation.log`` cut, default is the raw
        ``profile.jsonl`` download."""
        return await controller.get_generation_profile(
            generation_id, current_user, file, format
        )

    @router.get("/queue", response_model=APIResponse, summary="Get Generation Queue")
    async def get_queue(tab_id: str = None, current_user = Depends(get_current_active_user)):
        """The caller's pending generations in FIFO order, plus their running ones."""
        return await controller.get_queue(current_user, tab_id)

    @router.post("/queue/clear", response_model=APIResponse, summary="Clear A Tab's Queue")
    async def clear_tab_queue(request: ClearTabQueueRequest, current_user = Depends(get_current_active_user)):
        """Cancel every generation this tab has queued but not yet started."""
        return await controller.clear_tab_queue(request.tab_id, current_user)

    @router.get("/list", response_model=APIResponse, summary="List Active Generations")
    async def list_generations(current_user = Depends(get_current_active_user)):
        """List all currently active generations with their current statuses."""
        return await controller.list_generations(current_user)

    @router.get("/history", response_model=APIResponse, summary="Get Generation History")
    async def get_generation_history(limit: int = 50, offset: int = 0, status: str = None,
                                   created_from: str = None, created_to: str = None,
                                   completed_from: str = None, completed_to: str = None,
                                   tag_ids: str = None, include_tags: bool = True, media_type: str = None,
                                   search: str = None, mode: str = None, preset_id: str = None,
                                   model_name: str = None, min_rating: int = None,
                                   favorites_only: bool = False, collection_id: str = None,
                                   used_phrasebook_value_id: str = None,
                                   sort_by: str = None, sort_dir: str = None,
                                   system_tag: str = None,
                                   semantic_query: str = None,
                                   current_user = Depends(get_current_active_user)):
        """Get paginated generation history with optional filtering, search and sorting."""
        return await controller.get_generation_history(
            current_user,
            limit=limit,
            offset=offset,
            status=status,
            created_from=created_from,
            created_to=created_to,
            completed_from=completed_from,
            completed_to=completed_to,
            tag_ids=tag_ids,
            include_tags=include_tags,
            media_type=media_type,
            search=search,
            mode=mode,
            preset_id=preset_id,
            model_name=model_name,
            min_rating=min_rating,
            favorites_only=favorites_only,
            collection_id=collection_id,
            used_phrasebook_value_id=used_phrasebook_value_id,
            sort_by=sort_by,
            sort_dir=sort_dir,
            system_tag=system_tag,
            semantic_query=semantic_query
        )

    @router.get("/history/facets", response_model=APIResponse, summary="Get History Filter Facets")
    async def get_history_facets(current_user = Depends(get_current_active_user)):
        """Get distinct modes, presets and models (with counts) for history filter controls."""
        return await controller.get_history_facets(current_user)

    @router.get("/history/{generation_id}", response_model=APIResponse, summary="Get Generation by ID")
    async def get_generation_by_id(generation_id: str, include_files: bool = True, current_user = Depends(get_current_active_user)):
        """Get detailed information about a specific generation from the database."""
        return await controller.get_generation_by_id(generation_id, current_user, include_files=include_files)

    @router.get("/history/{generation_id}/export-bundle", summary="Export Generation Bundle")
    async def export_generation_bundle(generation_id: str, current_user = Depends(get_current_active_user)):
        """Portable bundle (preset, resolved seed, models, output listing) for
        reproducing this generation on another PotionUI instance.

        Returns the binary zip directly (not an APIResponse envelope).
        """
        return await controller.export_generation_bundle(generation_id, current_user)

    @router.get("/{generation_id}/params/{index}", response_model=APIResponse, summary="Get Generation Parameters by Index")
    async def get_generation_params(generation_id: str, index: int, current_user = Depends(get_current_active_user)):
        """Get parameters for a specific generation and image index."""
        return await controller.get_generation_params(generation_id, index, current_user)

    @router.put("/{generation_id}/rating", response_model=APIResponse, summary="Set Generation Rating")
    async def set_generation_rating(generation_id: str, request: RatingRequest,
                                    current_user = Depends(get_current_active_user)):
        """Set the star rating (0-5, 0 = unrated) for a generation."""
        return await controller.set_generation_rating(generation_id, request.rating, current_user)

    @router.put("/{generation_id}/favorite", response_model=APIResponse, summary="Set Generation Favorite")
    async def set_generation_favorite(generation_id: str, request: FavoriteRequest,
                                      current_user = Depends(get_current_active_user)):
        """Set the favorite flag for a generation."""
        return await controller.set_generation_favorite(generation_id, request.is_favorite, current_user)

    @router.delete("/history/{generation_id}", response_model=APIResponse, summary="Delete Generation")
    async def delete_generation_history(generation_id: str, current_user = Depends(get_current_active_user)):
        """Delete a generation from history including all associated files."""
        return await controller.delete_generation_history(generation_id, current_user)

    @router.post("/history/bulk-delete", response_model=APIResponse, summary="Bulk Delete Generations")
    async def bulk_delete_generations(request: BulkDeleteRequest, current_user = Depends(get_current_active_user)):
        """Delete multiple generations from history including all associated files."""
        return await controller.bulk_delete_generations(request.generation_ids, current_user)

    @router.post("/history/count-by-tags", response_model=APIResponse, summary="Count Generations by Tags")
    async def count_generations_by_tags(request: BulkDeleteByTagsRequest, current_user = Depends(get_current_active_user)):
        """Count generations matching ALL specified tags (for confirmation preview)."""
        return await controller.count_generations_by_tags(request.tag_ids, current_user)

    @router.post("/history/bulk-delete-by-tags", response_model=APIResponse, summary="Bulk Delete by Tags")
    async def bulk_delete_by_tags(request: BulkDeleteByTagsRequest, current_user = Depends(get_current_active_user)):
        """Delete all generations that have ALL specified tags."""
        return await controller.bulk_delete_by_tags(request.tag_ids, current_user)

    @router.post("/export", summary="Export Generations as Zip")
    async def export_generations(request: ExportRequest, current_user = Depends(get_current_active_user)):
        """Export the final image/video files of multiple generations as a single zip.

        Returns the binary zip directly (not an APIResponse envelope). When
        strip_metadata is true, images are re-encoded without EXIF / workflow metadata.
        """
        return await controller.export_generations(
            request.generation_ids, request.strip_metadata, current_user
        )

    @router.post("/upload", response_model=APIResponse, summary="Upload Generations")
    async def upload_generations(
        files: List[UploadFile] = FastAPIFile(...),
        tag_ids: List[str] = Query(default=[]),
        current_user = Depends(get_current_active_user)
    ):
        """Upload image files as completed generations with optional tags."""
        return await controller.upload_generations(files, tag_ids, current_user)

    @router.post("/import-bundle", response_model=APIResponse, summary="Import Generation Bundle")
    async def import_generation_bundle(
        file: UploadFile = FastAPIFile(...),
        current_user = Depends(get_current_active_user)
    ):
        """Parse an exported generation bundle (zip or bare generation.json) into
        a reuse payload for a pre-filled generate tab. Never creates a generation."""
        return await controller.import_generation_bundle(file, current_user)

    @router.get("/{generation_id}/tags", response_model=APIResponse, summary="Get Generation Tags")
    async def get_generation_tags(
        generation_id: str,
        current_user = Depends(get_current_active_user)
    ):
        """Get all tags for a generation"""
        return await controller.get_generation_tags(generation_id, current_user)

    @router.put("/{generation_id}/tags", response_model=APIResponse, summary="Update Generation Tags")
    async def update_generation_tags(
        generation_id: str,
        request: UpdateTagsRequest,
        current_user = Depends(get_current_active_user)
    ):
        """Replace all tags for a generation"""
        return await controller.update_generation_tags(generation_id, request.tag_ids, current_user)

    @router.delete("/{generation_id}/tags/{tag_id}", response_model=APIResponse, summary="Remove Tag from Generation")
    async def remove_generation_tag(
        generation_id: str,
        tag_id: str,
        current_user = Depends(get_current_active_user)
    ):
        """Remove a single tag from a generation"""
        return await controller.remove_generation_tag(generation_id, tag_id, current_user)

    @router.get("/debug/{generation_id}", summary="Debug Generation Data")
    async def debug_generation(generation_id: str, current_user = Depends(get_current_admin_user)):
        """Debug endpoint to inspect raw generation data and files (development only)."""
        try:
            generation = generation_repo.get_by_id(generation_id, include_files=True)
            if not generation:
                return {"success": False, "error": "Generation not found"}

            # Get all files for this generation directly from database
            files = generation_repo.get_files(generation_id)

            return {
                "success": True,
                "data": {
                    "generation": generation.to_dict(include_files=True),
                    "files_direct": [file.to_dict() for file in files],
                    "files_count": len(files)
                }
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @router.get("/debug/files", summary="Debug All Files")
    async def debug_all_files(current_user = Depends(get_current_admin_user)):
        """Debug endpoint to inspect all files in the database (development only)."""
        try:
            files = file_repo.debug_recent_generation_files(limit=20)
            return {
                "success": True,
                "data": {
                    "files": files,
                    "count": len(files)
                }
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    return router


def build_ws_router(container: "AppContainer") -> APIRouter:
    controller = _get_generation_controller(container)

    ws_router = APIRouter(tags=["WebSocket"])

    # WebSocket endpoint for real-time generation updates
    @ws_router.websocket("/ws/generation")
    async def websocket_generation_endpoint(websocket: WebSocket, token: str = Query(None)):
        """WebSocket endpoint for real-time generation progress updates and status changes."""
        from src.platform.security.current_user import authenticate_websocket_token

        # Authenticate the user before accepting connection
        try:
            user, auth_error = authenticate_websocket_token(token)
        except Exception as e:
            logging.error(f"WebSocket auth exception: {e}")
            try:
                await websocket.accept()
                await websocket.close(code=4001, reason="Authentication error")
            except Exception as close_error:
                logging.error(f"Failed to close WebSocket after auth error: {close_error}")
            return

        if user is None:
            logging.warning(f"WebSocket auth failed: {auth_error}")
            try:
                await websocket.accept()
                await websocket.close(code=4001, reason=auth_error or "Authentication failed")
            except Exception as e:
                logging.error(f"Error closing WebSocket after auth failure: {e}")
            return

        # Authentication successful, now handle the connection
        import uuid
        client_id = str(uuid.uuid4())
        logging.info(f"WebSocket connected: user={user.username}, client_id={client_id}")

        try:
            await controller.handle_websocket(websocket, client_id, user)
        except Exception as e:
            logging.error(f"WebSocket handler error for client {client_id}: {e}")
            import traceback
            traceback.print_exc()

    return ws_router


def build_admin_router(container: "AppContainer") -> APIRouter:
    """Global, cross-user generation visibility for admins - run reports
    included. Separate from `/api/generations` (per-user, ownership-scoped)."""
    controller = _get_generation_controller(container)

    admin_router = APIRouter(prefix="/api/admin/generations", tags=["Generation"])

    @admin_router.get("", response_model=APIResponse, summary="List All Generations (Admin)")
    async def admin_list_generations(
        user_id: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: int = 0,
        status: Optional[str] = None,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
        completed_from: Optional[str] = None,
        completed_to: Optional[str] = None,
        media_type: Optional[str] = None,
        search: Optional[str] = None,
        mode: Optional[str] = None,
        preset_id: Optional[str] = None,
        model_name: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        current_user = Depends(get_current_admin_user),
    ):
        """Generation history across every user, optionally filtered to one."""
        return await controller.admin_list_generations(
            user_id=user_id,
            limit=limit,
            offset=offset,
            status=status,
            created_from=created_from,
            created_to=created_to,
            completed_from=completed_from,
            completed_to=completed_to,
            media_type=media_type,
            search=search,
            mode=mode,
            preset_id=preset_id,
            model_name=model_name,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

    @admin_router.get("/{generation_id}", response_model=APIResponse, summary="Get Generation + Run Report (Admin)")
    async def admin_get_generation(
        generation_id: str,
        current_user = Depends(get_current_admin_user),
    ):
        """The full generation record plus its persisted run report, if any."""
        return await controller.admin_get_generation(generation_id)

    return admin_router
