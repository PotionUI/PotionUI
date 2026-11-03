"""
Create/update a session, including mode-data merging and version history.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for "not
found"/"blocked" (the controller converts that to an HTTP response).
"""
import logging
from typing import Any, Dict, Optional, Tuple

from src.platform.util.ids import generate_ulid
from src.features.sessions.dto import Session, SaveSessionRequest, UpdateSessionRequest
from src.features.sessions.mappers import session_to_response_dict
from src.features.sessions.repository import SessionRepository
from src.features.sessions.version_repository import SessionVersionRepository
from src.platform.http.errors import AccessDeniedError, ConflictError, NotFoundError
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook, run_veto_hook
from src.features.sessions.hooks import SESSION_HOOKS
from src.features.sessions.live import WriteOrigin, stored_origin

logger = logging.getLogger(__name__)

MAX_WRITE_ATTEMPTS = 5


class SessionVersionConflict(ConflictError):
    def __init__(self, session: Session):
        super().__init__(
            "session_version_conflict",
            "The session changed since this copy was loaded.",
            session=session_to_response_dict(session),
            **stored_origin(session.last_origin),
        )


def _stored(origin: Optional[WriteOrigin]) -> Optional[Dict[str, Any]]:
    return origin.stored() if origin else None


def _check_base(session: Session, base_version: Optional[int]) -> None:
    if base_version is not None and base_version != session.version:
        raise SessionVersionConflict(session)


def _named_session(session_repository: SessionRepository, user_id: str, request: SaveSessionRequest) -> Session:
    session = session_repository.get_by_user_preset_and_name(user_id, request.preset_id, request.name)
    if session is None:
        raise NotFoundError("session_not_found", "Session not found")
    return session


def _owned_session(session_repository: SessionRepository, user_id: str, session_id: str) -> Session:
    session = session_repository.get_by_id(session_id)
    if not session:
        raise NotFoundError("session_not_found", "Session not found")
    if session.user_id != user_id:
        raise AccessDeniedError("session_access_denied", "Access denied to this session")
    return session


def _contended(session_id: str) -> ConflictError:
    return ConflictError("session_write_contended", f"Session {session_id} kept changing during the save; try again")


def _merge_mode_data(
    existing_data: Dict[str, Any],
    new_data: Dict[str, Any],
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge mode-specific data into existing session data."""
    existing_data = existing_data or {}

    if not mode and isinstance(new_data, dict):
        # Mode-keyed data (dict of dicts) — shallow-merge so saving in one
        # mode doesn't wipe data stored for other modes on the same session.
        if new_data and all(isinstance(v, dict) for v in new_data.values()):
            return {**existing_data, **new_data}

        # Flat data carrying selectedMode — promote it to the mode slot.
        if 'selectedMode' in new_data:
            mode = new_data.get('selectedMode')

    if mode:
        merged_data = dict(existing_data)
        merged_data[mode] = new_data
        return merged_data

    # Fallback to complete replacement when shape is not recognizable.
    return new_data


def _resolve_preset_summary(file_preset_repository: Optional[Any], preset_id: str) -> Optional[str]:
    """Resolve a preset id to its display name for the version summary column.

    Best-effort: a preset can be renamed or deleted from disk after a version
    was written, and `file_preset_repository` itself is optional, so any
    failure just falls back to the raw id rather than blocking the save.
    """
    if not file_preset_repository:
        return preset_id
    try:
        preset = file_preset_repository.find_preset_by_id(preset_id)
        if preset is not None and getattr(preset, "name", None):
            return preset.name
    except Exception:
        logger.exception("Failed to resolve preset name for session version summary")
    return preset_id


def _record_version(
    version_repository: Optional[SessionVersionRepository],
    file_preset_repository: Optional[Any],
    session: Session,
) -> None:
    """Append an immutable version snapshot for `session`, if configured.

    Called after every successful create/update. Skips writing a new version
    when the payload is byte-identical to the latest stored version (trivial
    dedup — no consecutive-duplicate versions on rename-only or no-op saves).
    No-op entirely when `version_repository` wasn't supplied.
    """
    if not version_repository:
        return

    try:
        summary = _resolve_preset_summary(file_preset_repository, session.preset_id)
        version_repository.create_if_changed(session.id, session.data, summary)
    except Exception:
        # Version history is a secondary record of the save, not the save
        # itself — a failure here must never fail (or roll back) the actual
        # session save.
        logger.exception(f"Failed to record session version for session {session.id}")


def _create_new_session(
    session_repository: SessionRepository,
    plugin_registry: PluginRegistry,
    version_repository: Optional[SessionVersionRepository],
    file_preset_repository: Optional[Any],
    user_id: str,
    request: SaveSessionRequest,
    origin: Optional[WriteOrigin] = None,
) -> Tuple[Dict[str, Any], str]:
    """Create a new session.

    Raises:
        ValueError: If creation blocked or failed
    """
    hook_data = run_veto_hook(
        plugin_registry,
        SESSION_HOOKS.before_create,
        {
            "preset_id": request.preset_id,
            "name": request.name,
            "data": request.data,
            "mode": request.mode,
            "user_id": user_id,
        },
        "Session creation blocked",
    )

    # Allow hooks to modify data
    session_data = hook_data.get("data", request.data)

    session = Session(
        id=generate_ulid(),
        user_id=user_id,
        preset_id=request.preset_id,
        name=request.name,
        data=session_data,
    )

    created_session = session_repository.create(session, _stored(origin))

    execute_hook(
        plugin_registry,
        SESSION_HOOKS.after_create,
        {
            "session_id": created_session.id,
            "preset_id": created_session.preset_id,
            "name": created_session.name,
            "user_id": user_id,
        },
    )

    # First save of a session is always its version 1.
    _record_version(version_repository, file_preset_repository, created_session)

    logger.info(f"Session created: {created_session.name} (id: {created_session.id})")
    return session_to_response_dict(created_session), f"Session '{request.name}' saved successfully"


def _update_existing_session(
    session_repository: SessionRepository,
    plugin_registry: PluginRegistry,
    version_repository: Optional[SessionVersionRepository],
    file_preset_repository: Optional[Any],
    user_id: str,
    request: SaveSessionRequest,
    origin: Optional[WriteOrigin],
) -> Tuple[Dict[str, Any], str]:
    """Update an existing session (called from save_session when a session
    with the requested name already exists).

    Raises:
        ValueError: If update blocked or failed
    """
    existing_session = _named_session(session_repository, user_id, request)
    _check_base(existing_session, request.base_version)

    hook_data = run_veto_hook(
        plugin_registry,
        SESSION_HOOKS.before_update,
        {
            "session_id": existing_session.id,
            "preset_id": existing_session.preset_id,
            "name": existing_session.name,
            "old_data": existing_session.data,
            "new_data": request.data,
            "mode": request.mode,
            "user_id": user_id,
        },
        "Session update blocked",
    )
    new_data = hook_data.get("new_data", request.data)

    for attempt in range(MAX_WRITE_ATTEMPTS):
        if attempt:
            existing_session = _named_session(session_repository, user_id, request)
            _check_base(existing_session, request.base_version)

        merged_data = _merge_mode_data(existing_session.data, new_data, request.mode)

        updated_session = Session(
            id=existing_session.id,
            user_id=existing_session.user_id,
            preset_id=existing_session.preset_id,
            name=existing_session.name,
            data=merged_data,
            created_at=existing_session.created_at,
        )

        result = session_repository.update(updated_session, existing_session.version, _stored(origin))
        if result is None:
            continue

        execute_hook(
            plugin_registry,
            SESSION_HOOKS.after_update,
            {
                "session_id": result.id,
                "preset_id": result.preset_id,
                "name": result.name,
                "user_id": user_id,
            },
        )

        _record_version(version_repository, file_preset_repository, result)

        logger.info(f"Session updated: {result.name} (id: {result.id})")
        return session_to_response_dict(result), f"Session '{result.name}' updated successfully"
    raise _contended(request.name)


def save_session(
    session_repository: SessionRepository,
    plugin_registry: PluginRegistry,
    version_repository: Optional[SessionVersionRepository],
    file_preset_repository: Optional[Any],
    user_id: str,
    request: SaveSessionRequest,
    origin: Optional[WriteOrigin] = None,
) -> Tuple[Dict[str, Any], str]:
    """Save a new session or update an existing one with the same name.

    Executes hooks:
    - session.before_create / session.after_create (new session)
    - session.before_update / session.after_update (existing session)

    Raises:
        ValueError: If creation/update blocked or failed
    """
    existing_session = session_repository.get_by_user_preset_and_name(
        user_id, request.preset_id, request.name
    )

    if existing_session:
        return _update_existing_session(
            session_repository,
            plugin_registry,
            version_repository,
            file_preset_repository,
            user_id,
            request,
            origin,
        )
    return _create_new_session(
        session_repository, plugin_registry, version_repository, file_preset_repository, user_id, request, origin
    )


def update_session(
    session_repository: SessionRepository,
    plugin_registry: PluginRegistry,
    version_repository: Optional[SessionVersionRepository],
    file_preset_repository: Optional[Any],
    user_id: str,
    session_id: str,
    request: UpdateSessionRequest,
    origin: Optional[WriteOrigin] = None,
) -> Dict[str, Any]:
    """Update an existing session by ID.

    Executes hooks:
    - session.before_update: Can modify/validate data or block
    - session.after_update: Notification of successful update

    Raises:
        ValueError: If session not found, access denied, name conflict, or update blocked
    """
    session = _owned_session(session_repository, user_id, session_id)
    _check_base(session, request.base_version)

    if request.name != session.name:
        existing_with_name = session_repository.get_by_user_preset_and_name(
            user_id, session.preset_id, request.name
        )
        if existing_with_name:
            raise ConflictError("session_name_exists", f"Session with name '{request.name}' already exists for this preset")

    hook_data = run_veto_hook(
        plugin_registry,
        SESSION_HOOKS.before_update,
        {
            "session_id": session_id,
            "preset_id": session.preset_id,
            "old_name": session.name,
            "new_name": request.name,
            "old_data": session.data,
            "new_data": request.data,
            "mode": request.mode,
            "user_id": user_id,
        },
        "Session update blocked",
    )
    new_data = hook_data.get("new_data", request.data)

    for attempt in range(MAX_WRITE_ATTEMPTS):
        if attempt:
            session = _owned_session(session_repository, user_id, session_id)
            _check_base(session, request.base_version)

        merged_data = _merge_mode_data(session.data, new_data, request.mode)

        updated_session = Session(
            id=session_id,
            user_id=session.user_id,
            preset_id=session.preset_id,
            name=request.name,
            data=merged_data,
            created_at=session.created_at,
        )

        result = session_repository.update(updated_session, session.version, _stored(origin))
        if result is None:
            continue

        execute_hook(
            plugin_registry,
            SESSION_HOOKS.after_update,
            {
                "session_id": result.id,
                "preset_id": result.preset_id,
                "name": result.name,
                "user_id": user_id,
            },
        )

        _record_version(version_repository, file_preset_repository, result)

        logger.info(f"Session updated: {result.name} (id: {result.id})")
        return session_to_response_dict(result)
    raise _contended(session_id)
