import asyncio
import io
import logging
import traceback
from dataclasses import replace
from typing import List, Optional, TYPE_CHECKING
from fastapi import APIRouter, WebSocket, Depends, Query, UploadFile, File as FastAPIFile, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, PlainTextResponse, Response

# Import services - needed for injector
from src.platform.filesystem import FileStore
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.preview_clip_message import ADMIN_DETAIL_KEY
from src.features.generation import profile_paths
from src.platform.observability.profiling import render_report_from_file

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.security.failure_text import basename_paths, failure_detail
from src.platform.security.api_scopes import ApiScope, key_access, key_scope
from src.platform.security.current_user import (
    get_current_active_user,
    get_current_admin_user,
    require_media_viewer,
)
from src.features.generation.dto import (
    ClearTabQueueRequest,
    GenerationRequest,
    GenerationStatus,
    UpdateTagsRequest,
    BulkDeleteRequest,
    BulkDeleteByCriteriaRequest,
    RatingRequest,
    FavoriteRequest,
    ExportRequest,
)
from src.features.generation.criteria_delete import (
    GenerationDeleteCriteria,
    delete_generations,
    preview_generations,
)
from src.platform.websocket import ConnectionHub
from src.platform.websocket.key_sockets import key_sockets
from src.features.generation.websocket_handler import WebSocketHandler
from src.features.forms.exceptions import FormNotFoundException
from src.features.forms.binding import FormBindingError
from src.features.models.exceptions import ModelNotFoundException, ModelAccessDeniedException
from src.features.presets.exceptions import PresetNotFoundException
from src.features.backends.backend_registry import NoBackendForEngineError
from src.features.generation.routing.contracts import NoEligibleBackendError
from src.features.content_safety.errors import ContentPolicyRefusal
from src.features.generation.exceptions import AdminAuthorityRequired, IdempotencyKeyConflict
from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.applied_prompt_repository import GenerationAppliedPromptRepository
from src.features.generation.fixed_prompt import applied_parameters, with_applied_prompts
from src.features.cloud.cost_repository import GenerationCostRepository
from src.features.generation.failure import (
    failure_report,
    scope_generation,
    scope_generation_state,
    scope_status,
    start_failure_reason,
)
from src.features.generation import (
    GenerationHistoryFacade,
    GenerationNotFoundException,
    GenerationDeleteFailedException,
    UploadFailedException,
    InvalidTagException,
    InvalidDateFilterException,
    InvalidGenerationSourceException,
    GenerationBundleImportError,
    GenerationPolicy,
)
from src.features.generation.history_executor import HistoryExecutorSaturated
from src.features.generation.history_filter import HistoryFilter, history_filter_query
from src.features.generation.repository import generation_repo
from src.features.generation.state_lookup import resolve_generation_state
from src.features.generation.status_tracker import TERMINAL_STATES

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

# Chunk size for streaming an already-built export zip out of its spooled
# temp file - keeps a single read() from pulling the whole archive back into
# memory at once even though the file itself is already bounded.
_EXPORT_STREAM_CHUNK_BYTES = 1024 * 1024


def _close_spooled_file(spooled_file) -> None:
    """Idempotent close, safe to call from more than one exit path."""
    if not spooled_file.closed:
        spooled_file.close()


class _SpooledZipResponse(StreamingResponse):
    """A `StreamingResponse` that is the sole, explicit owner of closing its
    backing spooled temp file - on normal completion, a client disconnect, or
    an exception raised from the ASGI `send()` itself (e.g. a broken pipe).

    Starlette's `StreamingResponse.__call__` only awaits `background` after
    its internal task group exits *without* an exception - a disconnect is a
    self-cancellation the task group swallows, so `background` still runs,
    but a real exception from `send()` propagates straight out of `__call__`
    past that line, and `background` never runs at all. A sync generator body
    left mid-iteration when that happens is never closed either (nothing
    calls `aclose()` on it), so its own `finally` can't be relied on. Wrapping
    `__call__` itself in `finally` closes the file on every exit path, with
    exactly one place responsible for it.
    """

    def __init__(self, zip_file, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._zip_file = zip_file

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            _close_spooled_file(self._zip_file)


_NOT_FOUND = {GenerationNotFoundException: Fail("generation_not_found", status=404)}
_BUSY = {HistoryExecutorSaturated: Fail("history_busy", "Generation history is busy. Please retry.", status=503)}
_INVALID_TAG = {InvalidTagException: Fail("invalid_tag", status=400)}
_INVALID_DATE = {InvalidDateFilterException: Fail("invalid_date_format", status=400)}


def _block_details(exc: Exception) -> dict:
    return getattr(exc, "block_details", None) or {}


class GenerationController(BaseController):
    def __init__(
        self,
        generation_orchestrator: GenerationOrchestrator,
        generation_history_facade: GenerationHistoryFacade,
        file_service: FileStore,
        run_report_recorder: RunReportRecorder,
        generation_cost_repository: Optional[GenerationCostRepository] = None,
        output_broadcaster: Optional[GenerationOutputBroadcaster] = None,
        applied_prompt_repository: Optional[GenerationAppliedPromptRepository] = None,
    ):
        super().__init__()  # Initialize BaseController
        self.applied_prompt_repository = applied_prompt_repository
        self.generation_orchestrator = generation_orchestrator
        self.history_facade = generation_history_facade
        self.history_query = generation_history_facade.query
        self.file_service = file_service
        self.run_report_recorder = run_report_recorder
        self.generation_cost_repository = generation_cost_repository
        self.output_broadcaster = output_broadcaster or GenerationOutputBroadcaster(
            ConnectionHub(), generation_orchestrator.status_tracker, run_report_recorder
        )
        self.connection_hub = self.output_broadcaster.connection_hub
        self.websocket_handler = WebSocketHandler(self.connection_hub)
        # Queued generations have no outputs to broadcast yet, so position
        # changes reach the client through this side channel instead.
        self.generation_orchestrator.set_queue_listener(self._broadcast_queue_update)

    async def _broadcast_queue_update(self, generation_id: str, message: dict) -> None:
        """Push a `queue_update` to the clients subscribed to this generation."""
        if ADMIN_DETAIL_KEY not in message:
            await self.connection_hub.broadcast_to_generation(generation_id, message)
            return
        viewer_message = {key: value for key, value in message.items() if key != ADMIN_DETAIL_KEY}
        await self.connection_hub.broadcast_to_generation(generation_id, viewer_message, message)

    async def start_generation(self, request: GenerationRequest, current_user, grid_cell=None) -> APIResponse:
        """Start a new generation using the generation orchestrator"""
        try:
            # Delegate to generation orchestrator
            extra = {'grid_cell': grid_cell} if grid_cell is not None else {}
            if GenerationPolicy.is_admin(current_user):
                extra['acts_as_admin'] = True
            result = await self.generation_orchestrator.start_generation(
                request,
                current_user.id,
                output_callback=self.output_broadcaster.handle_output,
                **extra
            )
            if isinstance(result, dict) and isinstance(result.get('status'), dict):
                result = {**result, 'status': scope_status(result['status'], GenerationPolicy.is_admin(current_user))}

            return self.success_response(data=result)

        except AdminAuthorityRequired:
            raise HTTPException(
                status_code=403,
                detail={
                    "success": False,
                    "error": "insufficient_scope",
                    "message": "This preset is restricted to administrators; the API key needs the 'admin' scope.",
                },
                headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="admin"'},
            )
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
        except PresetNotFoundException:
            return self.error_response(
                error="preset_not_found",
                message="The selected preset is not available",
                status_code=404
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
        except ContentPolicyRefusal as e:
            return self.error_response(error=e.code, message=str(e), status_code=e.status_code)
        except IdempotencyKeyConflict:
            return self.error_response(
                error="idempotency_key_conflict",
                message="This idempotency key was already used for a different request",
                status_code=409
            )
        except ValueError as e:
            return self.error_response(
                error="validation_error",
                message=str(e) if GenerationPolicy.is_admin(current_user) else basename_paths(str(e)),
                status_code=400
            )
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(error_details)
            return self.error_response(
                error="generation_start_failed",
                message=f"Failed to start generation: {start_failure_reason(e, GenerationPolicy.is_admin(current_user))}"
            )

    async def preview_memory(self, request: GenerationRequest, current_user) -> APIResponse:
        """Estimate the GPU memory a request would need, without starting it."""
        try:
            authority = {'acts_as_admin': True} if GenerationPolicy.is_admin(current_user) else {}
            result = await self.generation_orchestrator.preview_memory(request, current_user.id, **authority)
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
        except PresetNotFoundException:
            return self.error_response(
                error="preset_not_found",
                message="The selected preset is not available",
                status_code=404
            )
        except ValueError as e:
            return self.error_response(
                error="validation_error",
                message=str(e) if GenerationPolicy.is_admin(current_user) else basename_paths(str(e)),
                status_code=400
            )
        except (NoBackendForEngineError, NoEligibleBackendError) as e:
            # A routing outcome, not an estimate failure: honestly distinct
            # from `memory_preview_failed` below so a caller (and the
            # frontend's advisory line) can tell "no backend can run this
            # right now" apart from "the estimator itself broke".
            return self.error_response(
                error="no_eligible_backend",
                message=str(e),
                status_code=503
            )
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(error_details)
            return self.error_response(
                error="memory_preview_failed",
                message=f"Failed to preview memory: {start_failure_reason(e, GenerationPolicy.is_admin(current_user))}"
            )

    async def _resolve_generation_state(self, generation_id: str):
        live = await self.generation_orchestrator.get_generation_status(generation_id)
        return resolve_generation_state(live, generation_repo, generation_id)

    async def get_generation_status(self, generation_id: str, current_user) -> APIResponse:
        """Get status of a specific generation"""
        state = await self._resolve_generation_state(generation_id)

        # Return 404 (not 403) for a non-owner: a 403 would confirm that the
        # generation id exists and belongs to someone else.
        if state is None or not GenerationPolicy.can_access(current_user, state.owner_id):
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )

        is_admin = GenerationPolicy.is_admin(current_user)
        payload = scope_generation_state(state.payload, is_admin)
        queue_info = self.generation_orchestrator.get_queue_info(generation_id)
        if isinstance(queue_info, dict) and payload.get('status') == 'pending':
            detail = queue_info.pop(ADMIN_DETAIL_KEY, None)
            payload = {**payload, **queue_info, **(detail if is_admin and isinstance(detail, dict) else {})}
        return self.success_response(data=payload)

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

    @guarded(
        fallback=Fail("clear_queue_failed", "Failed to clear tab queue", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def clear_tab_queue(self, tab_id: str, current_user) -> APIResponse:
        cancelled = await self.generation_orchestrator.clear_tab_queue(
            current_user.id, tab_id
        )

        for generation_id in cancelled:
            await self.output_broadcaster.broadcast_cancelled(generation_id)

        return self.success_response(
            data={'cancelled': cancelled, 'count': len(cancelled)},
            message=f"Cleared {len(cancelled)} queued generation(s)"
        )

    @guarded(
        fallback=Fail("queue_read_failed", "Failed to read generation queue", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def get_queue(self, current_user, tab_id: Optional[str] = None) -> APIResponse:
        return self.success_response(
            data=self.generation_orchestrator.get_queue_snapshot(current_user.id, tab_id)
        )

    @guarded(fallback=Fail("queue_read_failed", "Failed to read generation queue", detail=failure_detail, status=400))
    async def get_all_queue(self) -> APIResponse:
        return self.success_response(data=self.generation_orchestrator.get_all_queue_snapshot())

    @guarded(fallback=Fail("cancel_failed", "Failed to cancel generation", detail=failure_detail, status=400), admin="current_user")
    async def cancel_generation(self, generation_id: str, current_user) -> APIResponse:
        state = await self._resolve_generation_state(generation_id)
        if state is not None and not GenerationPolicy.can_access(current_user, state.owner_id):
            return self.error_response(
                error="generation_not_found",
                message=f"Generation '{generation_id}' not found",
                status_code=404
            )

        success = await self.generation_orchestrator.cancel_generation(generation_id)

        if not success:
            return self.error_response(
                error="cancel_failed",
                message="Failed to cancel generation",
                status_code=400
            )

        await self.output_broadcaster.broadcast_cancelled(generation_id)

        status = self.generation_orchestrator.status_tracker.get(generation_id)
        return self.success_response(
            data={"cancel_notice": status.cancel_notice if status else None},
            message="Generation cancelled successfully",
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

    @staticmethod
    def _stream_zip_response(zip_file, filename: str) -> StreamingResponse:
        """Stream an already-built export zip out of `zip_file` - a
        `SpooledTemporaryFile` seeked to 0 - without ever holding the archive
        fully in memory as bytes. The returned `_SpooledZipResponse` is the
        sole owner of closing `zip_file` from here on (which also removes its
        backing temp file, if the export rolled over to disk) - until it is
        actually returned, ownership hasn't transferred yet, so any failure
        while measuring or constructing the response here closes `zip_file`
        itself rather than leaving it ownerless.
        """
        try:
            zip_file.seek(0, io.SEEK_END)
            content_length = zip_file.tell()
            zip_file.seek(0)

            def iter_chunks():
                while True:
                    chunk = zip_file.read(_EXPORT_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    yield chunk

            return _SpooledZipResponse(
                zip_file,
                iter_chunks(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(content_length),
                },
            )
        except Exception:
            _close_spooled_file(zip_file)
            raise

    @guarded(
        {**_INVALID_DATE, **_BUSY},
        Fail("history_fetch_failed", "Failed to fetch generation history", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def get_generation_history(
        self,
        current_user,
        flt: HistoryFilter,
        limit: Optional[int] = 50,
        offset: int = 0,
        include_tags: bool = True,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
    ) -> APIResponse:
        result = await self.history_facade.get_history_async(
            replace(flt, user_id=current_user.id),
            limit=limit,
            offset=offset,
            include_tags=include_tags,
            sort_by=sort_by,
            sort_dir=sort_dir,
            group_grids=True,
        )
        is_admin = GenerationPolicy.is_admin(current_user)
        result = {**result, 'generations': [scope_generation(item, is_admin) for item in result.get('generations') or []]}

        return self.success_response(data=result)

    @guarded(
        {**_NOT_FOUND, **_BUSY},
        Fail("generation_fetch_failed", "Failed to fetch generation", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def get_generation_by_id(
        self,
        generation_id: str,
        current_user,
        include_files: bool = True,
        include_batch: bool = False,
    ) -> APIResponse:
        result = await self.history_facade.get_by_id_async(
            generation_id=generation_id,
            user_id=current_user.id,
            include_files=include_files,
            include_batch=include_batch,
        )
        is_admin = GenerationPolicy.is_admin(current_user)
        if is_admin:
            result = with_applied_prompts(result, self._applied_prompts(generation_id))
        return self.success_response(data=scope_generation(result, is_admin))

    @guarded(
        _NOT_FOUND,
        Fail("params_fetch_failed", "Error retrieving generation parameters", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def get_generation_params(
        self,
        generation_id: str,
        index: int,
        current_user
    ) -> APIResponse:
        result = self.history_query.get_params(
            generation_id=generation_id,
            index=index,
            user_id=current_user.id
        )
        if GenerationPolicy.is_admin(current_user):
            result = with_applied_prompts(result, self._applied_prompts(generation_id), index)
        return self.success_response(data=result)

    def _applied_prompts(self, generation_id: str):
        if self.applied_prompt_repository is None:
            return None
        return self.applied_prompt_repository.get(generation_id)

    @guarded(
        {**_NOT_FOUND, ValueError: Fail("invalid_rating", status=400)},
        Fail("rating_update_failed", "Failed to update rating", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def set_generation_rating(
        self,
        generation_id: str,
        rating: int,
        current_user
    ) -> APIResponse:
        value = self.history_facade.set_rating(generation_id, rating, current_user.id)
        return self.success_response(data={"id": generation_id, "rating": value})

    @guarded(
        _NOT_FOUND,
        Fail("favorite_update_failed", "Failed to update favorite", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def set_generation_favorite(
        self,
        generation_id: str,
        is_favorite: bool,
        current_user
    ) -> APIResponse:
        value = self.history_facade.set_favorite(generation_id, is_favorite, current_user.id)
        return self.success_response(data={"id": generation_id, "is_favorite": value})

    @guarded(fallback=Fail("facets_fetch_failed", "Failed to fetch history facets", detail=failure_detail, status=400), admin="current_user")
    async def get_history_facets(self, current_user) -> APIResponse:
        result = self.history_query.get_facets(current_user.id)
        return self.success_response(data=result)

    @guarded(
        _BUSY,
        Fail("history_version_failed", "Failed to get history version", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def get_history_version(self, current_user) -> APIResponse:
        version = await self.history_facade.get_history_version_async(current_user.id)
        return self.success_response(data={'version': version})

    @guarded(
        {**_NOT_FOUND, GenerationDeleteFailedException: Fail("delete_failed", status=400, extra=_block_details)},
        Fail("delete_failed", "Failed to delete generation", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def delete_generation_history(
        self,
        generation_id: str,
        current_user
    ) -> APIResponse:
        result = self.history_facade.delete(
            generation_id=generation_id,
            user_id=current_user.id
        )

        message = f"Generation deleted successfully. "
        message += f"Removed {result['files_deleted_fs']} files from filesystem, "
        message += f"{result['files_deleted_db']} file records from database."
        if result.get('files_failed_fs', 0) > 0:
            message += f" Warning: Failed to delete {result['files_failed_fs']} files from filesystem."

        return self.success_response(message=message)

    @guarded(
        {GenerationDeleteFailedException: Fail("bulk_delete_blocked", status=400, extra=_block_details)},
        Fail("bulk_delete_failed", "Failed to bulk delete generations", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def bulk_delete_generations(
        self,
        generation_ids: List[str],
        current_user,
        *,
        any_owner: bool = False,
    ) -> APIResponse:
        if not generation_ids:
            return self.error_response(
                error="invalid_request",
                message="No generation IDs provided",
                status_code=400
            )

        if any_owner:
            result = self.history_facade.admin_bulk_delete(
                generation_ids=generation_ids,
                admin_user_id=current_user.id
            )
            logging.info(
                f"Admin {current_user.id} bulk-deleted {result['deleted_count']} generation(s), "
                f"{result['failed_count']} failed"
            )
        else:
            result = self.history_facade.bulk_delete(
                generation_ids=generation_ids,
                user_id=current_user.id
            )

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

    @guarded(
        {**_NOT_FOUND, **_BUSY},
        Fail("export_failed", "Failed to export generations", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def export_generations(
        self,
        generation_ids: List[str],
        strip_metadata: bool,
        current_user
    ):
        if not generation_ids:
            return self.error_response(
                error="invalid_request",
                message="No generation IDs provided",
                status_code=400
            )

        zip_file, filename = await self.history_facade.export_zip_async(
            generation_ids=generation_ids,
            user_id=current_user.id,
            strip_metadata=strip_metadata
        )

        return self._stream_zip_response(zip_file, filename)

    @guarded(
        {**_NOT_FOUND, **_BUSY},
        Fail("export_failed", "Failed to export generation bundle", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def export_generation_bundle(self, generation_id: str, current_user):
        zip_file, filename = await self.history_facade.export_bundle_async(
            generation_id=generation_id,
            user_id=current_user.id
        )

        return self._stream_zip_response(zip_file, filename)

    @guarded(
        {GenerationBundleImportError: Fail("invalid_bundle", status=400)},
        Fail("import_failed", "Failed to import generation bundle", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def import_generation_bundle(self, file: UploadFile, current_user) -> APIResponse:
        content = await file.read()
        result = self.history_facade.import_bundle(content)

        return self.success_response(
            message="Bundle parsed successfully",
            data=result
        )

    def _owner_delete_criteria(self, request: BulkDeleteByCriteriaRequest, user_id: str) -> GenerationDeleteCriteria:
        """Build a `GenerationDeleteCriteria` scoped to `user_id`, validating
        tag ownership, date formats and status membership the same way the
        request's siblings (history listing, admin housekeeping) do."""
        if request.tag_ids:
            self.history_query._validate_tag_ids(request.tag_ids, user_id)
        self.history_query.validate_date_filters(request.created_from, request.created_to, None, None)
        if request.older_than_days is not None and request.older_than_days < 0:
            raise ValueError("older_than_days must not be negative")
        if request.statuses:
            invalid = [s for s in request.statuses if s not in TERMINAL_STATES]
            if invalid:
                raise ValueError(f"invalid status: {', '.join(invalid)}")

        return GenerationDeleteCriteria(
            user_id=user_id,
            tag_ids=request.tag_ids or None,
            older_than_days=request.older_than_days,
            created_from=request.created_from,
            created_to=request.created_to,
            without_media=request.without_media,
            statuses=request.statuses or None,
            keep_favorites=request.keep_favorites,
        )

    @guarded(
        {**_INVALID_TAG, **_INVALID_DATE, ValueError: Fail("validation_error", status=400)},
        Fail("count_failed", "Failed to count generations by criteria", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def count_generations_by_criteria(
        self,
        request: BulkDeleteByCriteriaRequest,
        current_user
    ) -> APIResponse:
        criteria = self._owner_delete_criteria(request, current_user.id)
        count = preview_generations(generation_repo, criteria)
        return self.success_response(
            message=f"Found {count} generation(s) matching the given criteria",
            data={"count": count}
        )

    @guarded(
        {
            **_INVALID_TAG,
            **_INVALID_DATE,
            ValueError: Fail("validation_error", status=400),
            GenerationDeleteFailedException: Fail("bulk_delete_blocked", status=400, extra=_block_details),
        },
        Fail("bulk_delete_failed", "Failed to bulk delete generations by tags", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def bulk_delete_by_criteria(
        self,
        request: BulkDeleteByCriteriaRequest,
        current_user
    ) -> APIResponse:
        criteria = self._owner_delete_criteria(request, current_user.id)

        if criteria.is_empty():
            return self.error_response(
                error="no_criteria",
                message="Choose at least one criterion before deleting generations",
                status_code=400
            )

        summary = delete_generations(generation_repo, self.history_facade, criteria)
        return self.success_response(
            message=f"Successfully deleted {summary['deleted_count']} generation(s).",
            data=summary
        )

    @guarded(
        {UploadFailedException: Fail("upload_failed", status=400), **_INVALID_TAG},
        Fail("upload_failed", "Failed to upload files", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def upload_generations(
        self,
        files: List[UploadFile],
        tag_ids: List[str],
        current_user
    ) -> APIResponse:
        result = await self.history_facade.upload_generations(
            files=files,
            tag_ids=tag_ids,
            user_id=current_user.id
        )

        return self.success_response(
            message=f"Successfully uploaded {len(result['files'])} file(s)",
            data=result
        )

    @guarded(
        _NOT_FOUND,
        Fail("fetch_tags_failed", "Failed to get tags", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def get_generation_tags(
        self,
        generation_id: str,
        current_user
    ) -> APIResponse:
        tags = self.history_facade.get_tags(
            generation_id=generation_id,
            user_id=current_user.id
        )
        return self.success_response(data={"tags": tags})

    @guarded(
        {**_NOT_FOUND, **_INVALID_TAG},
        Fail("update_tags_failed", "Failed to update tags", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def update_generation_tags(
        self,
        generation_id: str,
        tag_ids: List[str],
        current_user
    ) -> APIResponse:
        tags = self.history_facade.update_tags(
            generation_id=generation_id,
            tag_ids=tag_ids,
            user_id=current_user.id
        )
        return self.success_response(data={
            "message": "Generation tags updated successfully",
            "tags": tags
        })

    @guarded(
        _NOT_FOUND,
        Fail("remove_tag_failed", "Failed to remove tag", detail=failure_detail, status=400),
        admin="current_user",
    )
    async def remove_generation_tag(
        self,
        generation_id: str,
        tag_id: str,
        current_user
    ) -> APIResponse:
        success = self.history_facade.remove_tag(
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

    @guarded(
        {**_INVALID_DATE, **_BUSY},
        Fail("history_fetch_failed", "Failed to fetch generation history", detail=failure_detail, status=400),
    )
    async def admin_list_generations(
        self,
        user_id: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: int = 0,
        status: Optional[str] = None,
        error_category: Optional[str] = None,
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
        result = await self.history_facade.get_history_async(
            user_id=user_id,
            limit=limit,
            offset=offset,
            status=status,
            error_category=error_category,
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
        costs = (
            self.generation_cost_repository.summaries(generation_ids)
            if self.generation_cost_repository is not None else {}
        )
        for gen_dict in result['generations']:
            gen_dict['has_run_report'] = gen_dict['id'] in report_ids
            gen_dict['cost'] = costs.get(gen_dict['id'])

        return self.success_response(data=result)

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

        generation_data = {
            **generation.to_dict(include_files=True, include_tags=True),
            'routing': generation.routing_decision,
        }
        applied = applied_parameters(self._applied_prompts(generation_id))
        if applied is not None:
            generation_data = {**generation_data, 'parameters': applied, 'fixed_prompt_applied': True}
        return self.success_response(data={
            'generation': generation_data,
            'run_report': report,
            'cost': self._admin_cost(generation_id),
        })

    def _admin_cost(self, generation_id: str):
        if self.generation_cost_repository is None:
            return None
        entries = self.generation_cost_repository.for_generation(generation_id)
        if not entries:
            return None
        return {**self.generation_cost_repository.summaries([generation_id])[generation_id], 'items': entries}

    async def get_generation_failure(self, generation_id: str) -> APIResponse:
        generation = generation_repo.get_by_id(generation_id)
        if generation is None or generation.status != 'failed':
            return self.error_response(
                error="failure_not_found",
                message=f"No failure recorded for generation '{generation_id}'",
                status_code=404
            )
        return self.success_response(data=failure_report(generation))

    async def get_run_report_artifact(self, generation_id: str, name: str):
        """One binary payload a persisted run report references.

        Only names the report itself recorded resolve to anything, so the
        caller's string never reaches the filesystem as a path.
        """
        found = self.run_report_recorder.artifact_bytes(generation_id, name)
        if found is None:
            return self.error_response(
                error="not_found",
                message="Run report artifact not found",
                status_code=404,
            )
        content, media_type = found
        return Response(
            content=content,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    async def handle_websocket(self, websocket, client_id: str, user=None):
        """Handle WebSocket connection for real-time updates"""
        await self.websocket_handler.handle_websocket(
            websocket, client_id, self.generation_orchestrator.status_tracker, user
        )


def _get_generation_controller(container: "AppContainer") -> GenerationController:
    """Build (or reuse) the single `GenerationController` for this container.

    `build_router` and `build_ws_router` must share one controller instance:
    the HTTP side broadcasts generation output through
    `controller.connection_hub`, and the WebSocket side subscribes
    clients to that same `ConnectionHub`. Constructing a fresh
    `GenerationController` per factory call would give each router its own
    `ConnectionHub` and silently break that broadcast path. The instance
    is cached on the container itself, so it's shared regardless of call
    order.
    """
    controller = getattr(container, "_generation_controller", None)
    if controller is None:
        controller = GenerationController(
            container.generation_orchestrator,
            container.generation_history_facade,
            container.file_service,
            container.run_report_recorder,
            container.generation_cost_repository,
            container.output_broadcaster,
            container.generation_applied_prompt_repository,
        )
        container._generation_controller = controller
    return controller


def generation_controller_for(container: "AppContainer") -> "GenerationController":
    return _get_generation_controller(container)


def build_router(container: "AppContainer") -> APIRouter:
    controller = _get_generation_controller(container)

    router = APIRouter(prefix="/api/generations", tags=["Generation"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.GENERATE)])

    @router.post("/start", response_model=APIResponse, summary="Start Generation", description="Starts a generation from a preset and answers at once with its generation_id; the run continues in the background. Follow it with the status route or the /ws/generation socket. Repeating a request with the same idempotency_key returns the generation already started, and a different request under that key is refused with 409 idempotency_key_conflict.")
    async def start_generation(request: GenerationRequest, current_user = Depends(get_current_active_user)):
        """Start a new image generation job with the specified parameters and preset."""
        return await controller.start_generation(request, current_user)

    @router.post("/memory-preview", response_model=APIResponse, summary="Preview Generation Memory Advisory", dependencies=[key_scope(ApiScope.READ)])
    async def preview_generation_memory(request: GenerationRequest, current_user = Depends(get_current_active_user)):
        """Estimate the GPU memory a request would need, without starting it."""
        return await controller.preview_memory(request, current_user)

    @router.get("/{generation_id}/status", response_model=APIResponse, summary="Get Generation Status", description="The state of one of your generations: pending, running, completed, failed or cancelled, with its progress. Someone else's generation answers 404.")
    async def get_generation_status(generation_id: str, current_user = Depends(get_current_active_user)):
        """Get the current status and progress of a specific generation job."""
        return await controller.get_generation_status(generation_id, current_user)

    @router.get("/{generation_id}/failure", response_model=APIResponse, summary="Get Generation Failure Detail")
    async def get_generation_failure(generation_id: str, current_user = Depends(get_current_admin_user)):
        return await controller.get_generation_failure(generation_id)

    @router.get("/{generation_id}/run-report/artifacts/{name}", summary="Serve Run Report Artifact", dependencies=[key_scope({ApiScope.READ, ApiScope.MCP})])
    async def get_run_report_artifact(generation_id: str, name: str, viewer=Depends(require_media_viewer)):
        allowed = GenerationPolicy.is_admin(viewer) and await asyncio.to_thread(
            container.media_access.generation_allowed, viewer, generation_id
        )
        if not allowed:
            raise HTTPException(status_code=404, detail="Run report artifact not found")
        return await controller.get_run_report_artifact(generation_id, name)

    @router.post("/{generation_id}/cancel", response_model=APIResponse, summary="Cancel Generation", description="Cancels a pending or running generation. A generation that has already finished answers 400 cancel_failed.")
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

    @router.get("/queue", response_model=APIResponse, summary="Get Generation Queue", description="Your pending generations in the order they will start, each with its queue_position (0 starts next), and your running generations.")
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

    @router.get("/history", response_model=APIResponse, summary="Get Generation History", description="Your generations with their files, a page at a time (limit and offset). Narrow the list with status, mode, preset_id, tag_ids, collection_id, favorites_only, min_rating, created_from, created_to, media_type or search.")
    async def get_generation_history(limit: int = 50, offset: int = 0,
                                   include_tags: bool = True,
                                   sort_by: str = None, sort_dir: str = None,
                                   flt: HistoryFilter = Depends(history_filter_query),
                                   current_user = Depends(get_current_active_user)):
        """Get paginated generation history with optional filtering, search and sorting."""
        return await controller.get_generation_history(
            current_user,
            flt,
            limit=limit,
            offset=offset,
            include_tags=include_tags,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

    @router.get("/history/facets", response_model=APIResponse, summary="Get History Filter Facets")
    async def get_history_facets(current_user = Depends(get_current_active_user)):
        """Get distinct modes, presets and models (with counts) for history filter controls."""
        return await controller.get_history_facets(current_user)

    # Registered before "/history/{generation_id}" so "version" is not read as an id.
    @router.get("/history/version", response_model=APIResponse, summary="Get History Version Token")
    async def get_history_version(current_user = Depends(get_current_active_user)):
        """A change-detection token for the caller's history. Clients poll this
        and refetch the list only when the token differs from the last one seen."""
        return await controller.get_history_version(current_user)

    @router.get("/history/{generation_id}", response_model=APIResponse, summary="Get Generation by ID", description="One generation with its parameters, its error if it failed, and its files. Each file has an id that GET /api/media/files/{file_id} returns as bytes.")
    async def get_generation_by_id(generation_id: str, include_files: bool = True, include_batch: bool = False, current_user = Depends(get_current_active_user)):
        """Get detailed information about a specific generation from the database."""
        return await controller.get_generation_by_id(generation_id, current_user, include_files=include_files, include_batch=include_batch)

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

    @router.put("/{generation_id}/rating", response_model=APIResponse, summary="Set Generation Rating", dependencies=[key_scope(ApiScope.CONTENT)])
    async def set_generation_rating(generation_id: str, request: RatingRequest,
                                    current_user = Depends(get_current_active_user)):
        """Set the star rating (0-5, 0 = unrated) for a generation."""
        return await controller.set_generation_rating(generation_id, request.rating, current_user)

    @router.put("/{generation_id}/favorite", response_model=APIResponse, summary="Set Generation Favorite", dependencies=[key_scope(ApiScope.CONTENT)])
    async def set_generation_favorite(generation_id: str, request: FavoriteRequest,
                                      current_user = Depends(get_current_active_user)):
        """Set the favorite flag for a generation."""
        return await controller.set_generation_favorite(generation_id, request.is_favorite, current_user)

    @router.delete("/history/{generation_id}", response_model=APIResponse, summary="Delete Generation", dependencies=[key_scope(ApiScope.CONTENT)])
    async def delete_generation_history(generation_id: str, current_user = Depends(get_current_active_user)):
        """Delete a generation from history including all associated files."""
        return await controller.delete_generation_history(generation_id, current_user)

    @router.post("/history/bulk-delete", response_model=APIResponse, summary="Bulk Delete Generations", dependencies=[key_scope(ApiScope.CONTENT)])
    async def bulk_delete_generations(request: BulkDeleteRequest, current_user = Depends(get_current_active_user)):
        """Delete multiple generations from history including all associated files."""
        return await controller.bulk_delete_generations(request.generation_ids, current_user)

    @router.post("/history/count-by-criteria", response_model=APIResponse, summary="Count Generations by Criteria", dependencies=[key_scope(ApiScope.READ)])
    async def count_generations_by_criteria(request: BulkDeleteByCriteriaRequest, current_user = Depends(get_current_active_user)):
        """Count the current user's generations matching criteria, AND-ed (for confirmation preview)."""
        return await controller.count_generations_by_criteria(request, current_user)

    @router.post("/history/bulk-delete-by-criteria", response_model=APIResponse, summary="Bulk Delete by Criteria", dependencies=[key_scope(ApiScope.CONTENT)])
    async def bulk_delete_by_criteria(request: BulkDeleteByCriteriaRequest, current_user = Depends(get_current_active_user)):
        """Delete every generation of the current user matching criteria, AND-ed."""
        return await controller.bulk_delete_by_criteria(request, current_user)

    @router.post("/export", summary="Export Generations as Zip", dependencies=[key_scope(ApiScope.READ)])
    async def export_generations(request: ExportRequest, current_user = Depends(get_current_active_user)):
        """Export the final image/video files of multiple generations as a single zip.

        Returns the binary zip directly (not an APIResponse envelope). When
        strip_metadata is true, images are re-encoded without EXIF / workflow metadata.
        """
        return await controller.export_generations(
            request.generation_ids, request.strip_metadata, current_user
        )

    @router.post("/upload", response_model=APIResponse, summary="Upload Generations", dependencies=[key_scope(ApiScope.CONTENT)])
    async def upload_generations(
        files: List[UploadFile] = FastAPIFile(...),
        tag_ids: List[str] = Query(default=[]),
        current_user = Depends(get_current_active_user)
    ):
        """Upload image files as completed generations with optional tags."""
        return await controller.upload_generations(files, tag_ids, current_user)

    @router.post("/import-bundle", response_model=APIResponse, summary="Import Generation Bundle", dependencies=[key_scope(ApiScope.CONTENT)])
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

    @router.put("/{generation_id}/tags", response_model=APIResponse, summary="Update Generation Tags", dependencies=[key_scope(ApiScope.CONTENT)])
    async def update_generation_tags(
        generation_id: str,
        request: UpdateTagsRequest,
        current_user = Depends(get_current_active_user)
    ):
        """Replace all tags for a generation"""
        return await controller.update_generation_tags(generation_id, request.tag_ids, current_user)

    @router.delete("/{generation_id}/tags/{tag_id}", response_model=APIResponse, summary="Remove Tag from Generation", dependencies=[key_scope(ApiScope.CONTENT)])
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

    return router


def build_ws_router(container: "AppContainer") -> APIRouter:
    controller = _get_generation_controller(container)

    ws_router = APIRouter(tags=["WebSocket"])

    # WebSocket endpoint for real-time generation updates
    @ws_router.websocket("/ws/generation", dependencies=[key_scope(ApiScope.READ)])
    async def websocket_generation_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time generation progress updates and status changes."""
        from src.platform.security.current_user import authenticate_websocket, refuse_websocket

        user, auth_error = await authenticate_websocket(websocket)
        if user is None:
            await refuse_websocket(websocket, auth_error)
            return

        import uuid
        client_id = str(uuid.uuid4())
        logging.info(f"WebSocket connected: user={user.username}, client_id={client_id}")

        try:
            with key_sockets.track(websocket, user):
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

    admin_router = APIRouter(prefix="/api/admin/generations", tags=["Generation"], dependencies=[key_access(read=ApiScope.ADMIN, write=ApiScope.ADMIN)])

    @admin_router.post("/bulk-delete", response_model=APIResponse, summary="Bulk Delete Generations (Admin)")
    async def admin_bulk_delete_generations(request: BulkDeleteRequest, current_user = Depends(get_current_admin_user)):
        return await controller.bulk_delete_generations(request.generation_ids, current_user, any_owner=True)

    @admin_router.get("/queue", response_model=APIResponse, summary="Every User's Running And Queued Generations (Admin)")
    async def admin_get_queue(current_user = Depends(get_current_admin_user)):
        return await controller.get_all_queue()

    @admin_router.get("", response_model=APIResponse, summary="List All Generations (Admin)")
    async def admin_list_generations(
        user_id: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: int = 0,
        status: Optional[str] = None,
        error_category: Optional[str] = None,
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
            error_category=error_category,
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
