from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

from src.features.generation.dto import GenerationRequest
from src.features.generation.exceptions import GenerationNotFoundException, IdempotencyKeyConflict
from src.features.generation.file_repository import file_repo
from src.features.generation.policy import GenerationPolicy
from src.platform.plugins.runtime_registries import get_container

__all__ = [
    "GenerationNotFoundException",
    "GenerationRequest",
    "IdempotencyKeyConflict",
    "HistoryMediaRef",
    "LibraryMediaRef",
    "MediaRefError",
    "ResolvedMedia",
    "SessionNotFoundError",
    "SubmittedGeneration",
    "cancel_generation",
    "generation_state",
    "get_session_snapshot",
    "list_generation_files",
    "resolve_media_ref",
    "submit_generation",
]


class SubmittedGeneration(TypedDict):
    generation_id: str
    status: Optional[str]
    queue_position: Optional[int]


class HistoryMediaRef(TypedDict):
    kind: Literal["history"]
    generation_id: str
    file_id: str


class LibraryMediaRef(TypedDict):
    kind: Literal["library"]
    item_id: str


class ResolvedMedia(TypedDict):
    value: str
    media_type: str
    original_filename: Optional[str]
    origin: Optional[Dict[str, Any]]


class MediaRefError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SessionNotFoundError(LookupError):
    pass


def _not_found(generation_id: str) -> GenerationNotFoundException:
    return GenerationNotFoundException(f"Generation '{generation_id}' not found")


def _owner_of(container, generation_id: str) -> tuple:
    record = container.generation_orchestrator.status_tracker.get(generation_id)
    if record is not None:
        return True, getattr(record, "user_id", None)
    generation = container.generation_repository.get_by_id(generation_id)
    if generation is None:
        return False, None
    return True, getattr(generation, "user_id", None)


def _require_access(container, user, generation_id: str) -> None:
    exists, owner_id = _owner_of(container, generation_id)
    if not exists or not GenerationPolicy.can_access(user, owner_id):
        raise _not_found(generation_id)


async def submit_generation(
    user, request: Union[GenerationRequest, Dict[str, Any]]
) -> SubmittedGeneration:
    if user is None:
        raise PermissionError("A user is required to submit a generation")
    if not isinstance(request, GenerationRequest):
        request = GenerationRequest.model_validate(request)
    result = await get_container().generation_orchestrator.start_generation(request, user.id)
    status = result.get("status")
    return {
        "generation_id": result["generation_id"],
        "status": status.get("status") if isinstance(status, dict) else status,
        "queue_position": result.get("queue_position"),
    }


async def cancel_generation(user, generation_id: str) -> bool:
    container = get_container()
    _require_access(container, user, generation_id)
    cancelled = await container.generation_orchestrator.cancel_generation(generation_id)
    if cancelled:
        await container.output_broadcaster.broadcast_cancelled(generation_id)
    return cancelled


def generation_state(user, generation_id: str) -> Dict[str, Any]:
    container = get_container()
    _require_access(container, user, generation_id)
    record = container.generation_orchestrator.status_tracker.get(generation_id)
    if record is not None:
        return record.model_dump()
    return container.generation_repository.get_by_id(generation_id).to_dict()


def _visible_files(container, user, generation_id: str) -> List[Dict[str, Any]]:
    _require_access(container, user, generation_id)
    files = file_repo.get_generation_files(generation_id)
    indexes = {f.id: position for position, f in enumerate(files)}
    entries = [dict(f.to_dict(), index=indexes[f.id]) for f in files]
    visible = container.generation_history_facade.query.visible_file_dicts(
        entries, viewer_id=getattr(user, "id", None)
    )
    if entries and not visible:
        raise _not_found(generation_id)
    for entry in visible:
        entry["index"] = indexes[entry["id"]]
    return visible


def list_generation_files(user, generation_id: str, final_only: bool = True) -> List[Dict[str, Any]]:
    visible = _visible_files(get_container(), user, generation_id)
    if final_only:
        return [f for f in visible if f.get("is_final") and not f.get("is_derived")]
    return visible


def _resolve_history(container, user, ref: Dict[str, Any]) -> ResolvedMedia:
    generation_id, file_id = ref.get("generation_id"), ref.get("file_id")
    if not isinstance(generation_id, str) or not generation_id or not isinstance(file_id, str) or not file_id:
        raise MediaRefError("invalid_ref", "A history reference needs generation_id and file_id")
    try:
        files = _visible_files(container, user, generation_id)
    except GenerationNotFoundException:
        raise MediaRefError("not_found", "The referenced media is not available") from None
    entry = next((f for f in files if f["id"] == file_id and f.get("file_path")), None)
    if entry is None or not container.file_service.generation_exists(entry["file_path"]):
        raise MediaRefError("not_found", "The referenced media is not available")
    return {
        "value": entry["file_path"],
        "media_type": entry["file_type"],
        "original_filename": entry["file_path"].rsplit("/", 1)[-1],
        "origin": {"generation_id": generation_id, "file_index": entry["index"]},
    }


def _resolve_library(container, user, ref: Dict[str, Any]) -> ResolvedMedia:
    item_id = ref.get("item_id")
    if not isinstance(item_id, str) or not item_id:
        raise MediaRefError("invalid_ref", "A library reference needs item_id")
    store = container.media_store
    upload = store.upload_repo.get_by_id(item_id, getattr(user, "id", None))
    if upload is None or not store.storage_driver.exists(f"uploads/{upload.filename}"):
        raise MediaRefError("not_found", "The referenced media is not available")
    return {
        "value": f"uploads/{upload.filename}",
        "media_type": upload.media_type,
        "original_filename": upload.original_filename,
        "origin": None,
    }


def resolve_media_ref(user, ref: Union[HistoryMediaRef, LibraryMediaRef]) -> ResolvedMedia:
    if not isinstance(ref, dict):
        raise MediaRefError("invalid_ref", "A media reference must be an object")
    kind = ref.get("kind")
    allowed = {"history": {"kind", "generation_id", "file_id"}, "library": {"kind", "item_id"}}
    if kind not in allowed or set(ref) != allowed[kind]:
        raise MediaRefError("invalid_ref", "Unsupported media reference")
    container = get_container()
    if kind == "history":
        return _resolve_history(container, user, ref)
    return _resolve_library(container, user, ref)


def get_session_snapshot(user, session_id: str, version: Optional[int] = None) -> Dict[str, Any]:
    container = get_container()
    session = container.session_repository.get_by_id(session_id)
    if session is None or session.user_id != getattr(user, "id", None):
        raise SessionNotFoundError(f"Session '{session_id}' not found")
    data = session.data
    if version is not None:
        record = container.session_version_repository.get(session_id, version)
        if record is None:
            raise SessionNotFoundError(f"Session '{session_id}' has no version {version}")
        data = record.data
    return {
        "session_id": session.id,
        "preset_id": session.preset_id,
        "name": session.name,
        "version": version,
        "data": data,
    }
