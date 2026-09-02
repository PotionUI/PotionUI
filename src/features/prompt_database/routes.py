"""HTTP boundary for the normalized prompt aggregate API."""

import logging
from typing import Any, Dict, List, TYPE_CHECKING, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Form, Query, UploadFile
from fastapi import File as FastAPIFile
from fastapi.responses import PlainTextResponse

from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.prompt_database import operations
from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from src.features.prompt_database.dto import (
    PromptBulkDeleteRequest,
    PromptRequest,
)
from src.features.prompt_database.embedding import LocalEmbeddingProvider
from src.features.prompt_database.operations.mutations import UnknownModelError
from src.features.generation.repository import generation_repo
from src.features.presets.name_resolver import PresetNameResolver
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)

MAX_IMPORT_FILE_BYTES = 20 * 1024 * 1024


def _user_id(user: User) -> str:
    """Authenticated users are persisted records and therefore always have an id."""
    if not user.id:
        raise RuntimeError("Authenticated user is missing an id")
    return user.id


class PromptDatabaseController(BaseController):
    def __init__(self, collaborators: PromptDatabaseCollaborators):
        super().__init__()
        self.collaborators = collaborators

    async def create(self, request: PromptRequest, user: User) -> APIResponse:
        try:
            prompt = await operations.create_prompt(self.collaborators, _user_id(user), request)
            return self.success_response(prompt.to_dict())
        except UnknownModelError as exc:
            return self.error_response("invalid_model", str(exc), 400)
        except ValueError as exc:
            return self.error_response("validation_error", str(exc), 422)
        except Exception as exc:
            return self.handle_exception(exc, "create_prompt_error")

    async def replace(self, prompt_id: str, request: PromptRequest, user: User) -> APIResponse:
        try:
            prompt = await operations.replace_prompt(self.collaborators, _user_id(user), prompt_id, request)
            if prompt is None:
                return self.error_response("not_found", "Prompt not found", 404)
            return self.success_response(prompt.to_dict())
        except UnknownModelError as exc:
            return self.error_response("invalid_model", str(exc), 400)
        except ValueError as exc:
            return self.error_response("validation_error", str(exc), 422)
        except Exception as exc:
            return self.handle_exception(exc, "update_prompt_error")


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.prompt_database_controller
    settings = container.settings
    download_queue = container.download_queue
    prompt_importer_registry = container.prompt_importer_registry
    router = APIRouter(prefix="/api/prompts", tags=["Prompts"])

    @router.get(
        "/embedding-status",
        response_model=APIResponse,
        summary="Local prompt-embedding model status",
    )
    async def embedding_status(
        model_name: Optional[str] = Query(
            None, description="Override the saved model id (e.g. an unsaved admin edit)"
        ),
        current_user: User = Depends(get_current_admin_user),
    ):
        """Presence/path/size of the local text-embedder weights on disk, plus
        whether they're currently resident in memory and any in-flight fetch
        job for them. Admin only.

        `present` is disk-only and never implies `loaded` - the active
        provider instance can be evicted (or simply never yet loaded) while
        the weights remain on disk. The in-flight job is included so a
        reloading or reconnecting admin client can reconstruct "a fetch is
        already running" from this call alone - it never has to keep its own
        record of which download id maps to which asset.
        """
        name = model_name or settings.get_setting(
            "prompt_embedding_model", LocalEmbeddingProvider.DEFAULT_MODEL
        )
        data = LocalEmbeddingProvider.resolve_status(name, settings.get_models_dir())
        active = download_queue.find_active_download_for_repo(name)
        data["active_download"] = active.to_dict() if active else None
        # Only the active provider instance can report residency, and only
        # for the model it was actually constructed with - an admin querying
        # an unsaved override model_name gets an honest `false`, not a
        # residency reading for a different model.
        provider = controller.collaborators.embedding_provider
        is_loaded = getattr(provider, "is_loaded", None)
        data["loaded"] = bool(
            is_loaded and getattr(provider, "model_name", None) == name and is_loaded()
        )
        return APIResponse(success=True, data=data)

    @router.post("/import", response_model=APIResponse, summary="Import prompts from one or more files")
    async def import_prompts_route(
        files: List[UploadFile] = FastAPIFile(...),
        format: Optional[str] = Form(None),
        model_id: Optional[str] = Form(None),
        base_model: Optional[str] = Form(None),
        current_user: User = Depends(get_current_active_user),
    ):
        """Auto-detects styles.csv, Fooocus-style JSON, dynamicprompts wildcard
        YAML, one-prompt-per-line text, or generation metadata embedded in an
        image, unless `format` overrides detection. Pasted text arrives as an
        ordinary file part (conventionally named `pasted.txt`) - nothing
        special-cases it here."""
        loaded: List[Tuple[str, bytes]] = []
        for upload in files:
            content = await upload.read()
            if len(content) > MAX_IMPORT_FILE_BYTES:
                return controller.error_response(
                    "file_too_large",
                    f"{upload.filename or 'file'} exceeds the {MAX_IMPORT_FILE_BYTES // (1024 * 1024)}MB import limit",
                    413,
                )
            loaded.append((upload.filename or "upload", content))

        try:
            outcome = await operations.import_prompts(
                controller.collaborators, _user_id(current_user), loaded,
                format=format, model_id=model_id or None, base_model=base_model,
            )
        except UnknownModelError as exc:
            return controller.error_response("invalid_model", str(exc), 400)
        data = outcome.to_dict()
        if data["imported"] == 0 and data["files"] and all(f.get("reason") for f in data["files"]):
            return APIResponse(success=False, error="nothing_imported", data=data)
        return APIResponse(success=True, data=data)

    @router.get("/export", summary="Export saved prompts")
    async def export_prompts_route(
        format: str = Query("styles-csv"),
        collection_id: Optional[str] = Query(None, description="Only prompts in this 'prompts'-scope collection"),
        current_user: User = Depends(get_current_active_user),
    ):
        if format != "styles-csv":
            return controller.error_response("unsupported_format", f"Unknown export format: {format}", 400)
        csv_text = operations.export_styles_csv(
            controller.collaborators, _user_id(current_user), collection_id=collection_id,
        )
        return PlainTextResponse(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="styles.csv"'},
        )

    @router.get("/importers", response_model=APIResponse, summary="List available prompt import sources")
    async def list_importers(current_user: User = Depends(get_current_active_user)):
        return APIResponse(success=True, data=prompt_importer_registry.frontend_manifest())

    @router.post("/import/{importer_id}", response_model=APIResponse, summary="Run a prompt import source")
    async def run_import(
        importer_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        current_user: User = Depends(get_current_active_user),
    ):
        definition = prompt_importer_registry.get(importer_id)
        if definition is None:
            return controller.error_response("not_found", "Unknown prompt importer", 404)
        try:
            outcome = await definition.backend.run(payload, _user_id(current_user))
        except UnknownModelError as exc:
            return controller.error_response("invalid_model", str(exc), 400)
        return APIResponse(success=outcome.error is None, data={
            "imported": outcome.imported, "skipped": outcome.skipped, "total": outcome.total,
            "items": outcome.items, "error": outcome.error,
        })

    @router.get("/search", response_model=APIResponse, summary="Semantic-search saved prompts")
    async def search_prompts(
        q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100),
        base_model: Optional[str] = None, model_id: Optional[str] = None,
        source_provider: Optional[str] = None,
        current_user: User = Depends(get_current_active_user),
    ):
        prompts = await operations.search(
            controller.collaborators, _user_id(current_user), q, limit, base_model, model_id, source_provider,
        )
        return APIResponse(success=True, data=[prompt.to_dict() for prompt in prompts])

    @router.post("/find-duplicates", response_model=APIResponse, summary="Find near-duplicate prompts")
    async def find_duplicates(
        threshold: float = Query(0.1, ge=0.01, le=1.0),
        model_id: Optional[str] = None,
        current_user: User = Depends(get_current_active_user),
    ):
        groups = await operations.find_duplicates(
            controller.collaborators, _user_id(current_user), threshold, model_id,
        )
        return APIResponse(
            success=True,
            data={
                "groups": groups,
                "total_duplicates": sum(len(group["prompts"]) - 1 for group in groups),
            },
        )

    @router.post("/bulk-delete", response_model=APIResponse, summary="Delete multiple prompts")
    async def bulk_delete(
        request: PromptBulkDeleteRequest,
        current_user: User = Depends(get_current_active_user),
    ):
        count = operations.bulk_delete_prompts(
            controller.collaborators, _user_id(current_user), request.prompt_ids,
        )
        return APIResponse(success=True, data={"deleted": count})

    @router.delete("/purge-model/{model_id}", response_model=APIResponse, summary="Delete all prompts for a model")
    async def purge_model(model_id: str, current_user: User = Depends(get_current_active_user)):
        count = operations.purge_model_prompts(
            controller.collaborators, _user_id(current_user), model_id
        )
        return APIResponse(success=True, data={"deleted": count})

    @router.post("/embed-pending", response_model=APIResponse, summary="Embed prompts awaiting vectorization")
    async def embed_pending(current_user: User = Depends(get_current_active_user)):
        count = await operations.embed_pending(
            controller.collaborators, _user_id(current_user)
        )
        return APIResponse(success=True, data={"embedded": count})

    @router.post("", response_model=APIResponse, summary="Create a prompt")
    @router.post("/", response_model=APIResponse, include_in_schema=False)
    async def create_prompt(
        request: PromptRequest,
        current_user: User = Depends(get_current_active_user),
    ):
        return await controller.create(request, current_user)

    @router.get("", response_model=APIResponse, summary="List saved prompts")
    @router.get("/", response_model=APIResponse, include_in_schema=False)
    async def list_prompts(
        limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
        source_provider: Optional[str] = None, base_model: Optional[str] = None,
        model_id: Optional[str] = None, usage_hint: Optional[str] = None,
        collection_id: Optional[str] = Query(None, description="Only prompts in this 'prompts'-scope collection"),
        sort_by: str = "created_at", sort_order: str = "desc",
        current_user: User = Depends(get_current_active_user),
    ):
        user_id = _user_id(current_user)
        repository = controller.collaborators.repository
        items = repository.get_all(
            user_id=user_id, limit=limit, offset=offset,
            source_provider=source_provider, base_model=base_model, model_id=model_id,
            usage_hint=usage_hint, collection_id=collection_id,
            sort_by=sort_by, sort_order=sort_order,
        )
        total = repository.count(
            user_id, source_provider, model_id, base_model, usage_hint, collection_id,
        )
        data = {
            "items": [item.to_dict() for item in items], "total": total,
            "limit": limit, "offset": offset,
        }
        # Single grouped query for the whole page rather than one lookup per
        # prompt - see GenerationRepository.usage_stats_by_source_prompt.
        prompt_ids = [item["id"] for item in data["items"]]
        usage = generation_repo.usage_stats_by_source_prompt(prompt_ids, user_id)
        for item in data["items"]:
            stats = usage.get(item["id"])
            item["usage_count"] = stats["usage_count"] if stats else 0
            item["last_used_at"] = stats["last_used_at"] if stats else None
        return APIResponse(success=True, data=data)

    @router.get("/{prompt_id}", response_model=APIResponse, summary="Get a prompt")
    async def get_prompt(prompt_id: str, current_user: User = Depends(get_current_active_user)):
        prompt = controller.collaborators.repository.get_by_id(prompt_id, _user_id(current_user))
        if prompt is None:
            return controller.error_response("not_found", "Prompt not found", 404)
        return APIResponse(success=True, data=prompt.to_dict())

    @router.get(
        "/{prompt_id}/generations", response_model=APIResponse,
        summary="Generations that used this prompt",
    )
    async def get_prompt_generations(
        prompt_id: str,
        limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
        current_user: User = Depends(get_current_active_user),
    ):
        """Completed generations submitted from this library prompt, newest first.

        Scoped to the caller the same way generation history is - a second
        user's generations never surface here even if they happen to carry
        this prompt's id.
        """
        user_id = _user_id(current_user)
        generations = generation_repo.get_by_source_prompt(prompt_id, user_id, limit=limit, offset=offset)
        total = generation_repo.count_by_source_prompt(prompt_id, user_id)
        names = PresetNameResolver(container.preset_template_loader).name_map()
        items = [
            {
                "id": generation.id,
                "preset_id": generation.preset_id,
                "preset_name": names.get(generation.preset_id, generation.preset_id) if generation.preset_id else None,
                "created_at": generation.created_at.isoformat() if generation.created_at else None,
                "files": [file.to_dict() for file in generation.files],
            }
            for generation in generations
        ]
        return APIResponse(success=True, data={"items": items, "total": total, "limit": limit, "offset": offset})

    @router.put("/{prompt_id}", response_model=APIResponse, summary="Replace a prompt")
    async def replace_prompt(
        prompt_id: str, request: PromptRequest,
        current_user: User = Depends(get_current_active_user),
    ):
        return await controller.replace(prompt_id, request, current_user)

    @router.delete("/{prompt_id}", response_model=APIResponse, summary="Delete a prompt")
    async def delete_prompt(prompt_id: str, current_user: User = Depends(get_current_active_user)):
        if not operations.delete_prompt(controller.collaborators, _user_id(current_user), prompt_id):
            return controller.error_response("not_found", "Prompt not found", 404)
        return APIResponse(success=True, message="Prompt deleted")

    return router


# Alias kept for plugin manifests that reference this module by this name.
PromptController = PromptDatabaseController
