"""
Session domain manager.

Handles all business logic for user sessions (saved preset configurations).
Framework-agnostic - uses ValueError for errors (controller converts to HTTP responses).
"""
import logging
from typing import Dict, Any, List, Optional, Tuple


from src.platform.util.ids import generate_ulid
from src.features.sessions.dto import (
    Session,
    SaveSessionRequest,
    UpdateSessionRequest,
)
from src.features.sessions.repository import SessionRepository
from src.features.sessions.version_repository import SessionVersionRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.sessions.hooks import SESSION_HOOKS

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Coordinates session operations.

    Handles CRUD for sessions with plugin hook execution.
    Sessions store user-specific form data configurations for presets.
    """

    def __init__(
        self,
        session_repository: SessionRepository,
        plugin_registry: PluginRegistry,
        session_version_repository: Optional[SessionVersionRepository] = None,
        file_preset_repository: Optional[Any] = None,
    ):
        self.repository = session_repository
        self.plugins = plugin_registry
        # Optional so existing tests/callers that build a SessionManager with
        # just (session_repository, plugin_registry) keep working unchanged;
        # version history is simply a no-op when these aren't supplied.
        self.version_repository = session_version_repository
        self.file_preset_repository = file_preset_repository

    def _merge_mode_data(
        self,
        existing_data: Dict[str, Any],
        new_data: Dict[str, Any],
        mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Merge mode-specific data into existing session data.

        Args:
            existing_data: Current session data from database
            new_data: New data being sent from frontend
            mode: Specific mode being updated (e.g., 'txt2vid', 'img2vid')

        Returns:
            Merged session data
        """
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

    # ========== Session history (versions) ==========

    def _resolve_preset_summary(self, preset_id: str) -> Optional[str]:
        """Resolve a preset id to its display name for the version summary column.

        Best-effort: a preset can be renamed or deleted from disk after a
        version was written, and the file_preset_repository dependency itself
        is optional (see __init__), so any failure just falls back to the raw
        id rather than blocking the save.
        """
        if not self.file_preset_repository:
            return preset_id
        try:
            preset = self.file_preset_repository.find_preset_by_id(preset_id)
            if preset is not None and getattr(preset, "name", None):
                return preset.name
        except Exception:
            logger.exception("Failed to resolve preset name for session version summary")
        return preset_id

    def _record_version(self, session: Session) -> None:
        """Append an immutable version snapshot for `session`, if configured.

        Called after every successful create/update. Skips writing a new
        version when the payload is byte-identical to the latest stored
        version (trivial dedup — no consecutive-duplicate versions on
        rename-only or no-op saves). No-op entirely when
        `session_version_repository` wasn't supplied (keeps existing callers
        that build a bare SessionManager working unchanged).
        """
        if not self.version_repository:
            return

        try:
            latest = self.version_repository.get_latest(session.id)
            if latest is not None and latest.data == session.data:
                return

            summary = self._resolve_preset_summary(session.preset_id)
            self.version_repository.create(session.id, session.data, summary)
        except Exception:
            # Version history is a secondary record of the save, not the save
            # itself — a failure here must never fail (or roll back) the
            # actual session save.
            logger.exception(f"Failed to record session version for session {session.id}")

    def list_session_versions(self, user_id: str, session_id: str) -> List[Dict[str, Any]]:
        """
        List a session's version history, newest first (no payloads).

        Args:
            user_id: The user's ID (for ownership check)
            session_id: The session's ID

        Returns:
            List of {version_number, created_at, summary} dicts, newest first.

        Raises:
            ValueError: "Session not found" for both a missing session and one
                owned by another user — the house 404-not-403 idiom, so the
                response can't be used to probe which session ids exist.
        """
        session = self.repository.get_by_id(session_id)
        if not session or session.user_id != user_id:
            raise ValueError("Session not found")

        if not self.version_repository:
            return []

        versions = self.version_repository.list_for_session(session_id)
        return [
            {
                "version_number": v.version_number,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "summary": v.summary,
            }
            for v in versions
        ]

    def get_session_version(self, user_id: str, session_id: str, version_number: int) -> Dict[str, Any]:
        """
        Get a single version's full payload.

        Args:
            user_id: The user's ID (for ownership check)
            session_id: The session's ID
            version_number: The version to fetch

        Returns:
            {version_number, created_at, summary, data} dict.

        Raises:
            ValueError: "Session not found" (missing/not owned) or
                "Version not found" (owned session, unknown version_number) —
                both 404s at the controller.
        """
        session = self.repository.get_by_id(session_id)
        if not session or session.user_id != user_id:
            raise ValueError("Session not found")

        if not self.version_repository:
            raise ValueError("Version not found")

        version = self.version_repository.get(session_id, version_number)
        if not version:
            raise ValueError("Version not found")

        return {
            "version_number": version.version_number,
            "created_at": version.created_at.isoformat() if version.created_at else None,
            "summary": version.summary,
            "data": version.data,
        }

    def _session_to_response_dict(self, session: Session) -> Dict[str, Any]:
        """
        Convert session to response dictionary (excludes user_id for security).

        Args:
            session: Session DTO

        Returns:
            Dictionary with session data (without user_id)
        """
        return {
            'id': session.id,
            'preset_id': session.preset_id,
            'name': session.name,
            'data': session.data,
            'created_at': session.created_at.isoformat() if session.created_at else None,
            'updated_at': session.updated_at.isoformat() if session.updated_at else None
        }

    # ========== Read Operations ==========

    def get_sessions_for_preset(self, user_id: str, preset_id: str) -> List[Dict[str, Any]]:
        """
        Get all sessions for a user and preset.

        Args:
            user_id: The user's ID
            preset_id: The preset's ID

        Returns:
            List of session dictionaries (without user_id)
        """
        sessions = self.repository.get_by_user_and_preset(user_id, preset_id)
        return [self._session_to_response_dict(session) for session in sessions]

    def get_session_by_id(self, user_id: str, session_id: str) -> Dict[str, Any]:
        """
        Get a specific session by ID with ownership validation.

        Args:
            user_id: The user's ID (for ownership check)
            session_id: The session's ID

        Returns:
            Session dictionary (without user_id)

        Raises:
            ValueError: If session not found or access denied
        """
        session = self.repository.get_by_id(session_id)

        if not session:
            raise ValueError("Session not found")

        # Check ownership
        if session.user_id != user_id:
            raise ValueError("Access denied to this session")

        return self._session_to_response_dict(session)

    # ========== Create/Update Operations ==========

    def save_session(
        self,
        user_id: str,
        request: SaveSessionRequest
    ) -> Tuple[Dict[str, Any], str]:
        """
        Save a new session or update existing one with same name.

        Executes hooks:
        - session.before_create: Can modify/validate data or block
        - session.after_create: Notification of successful creation

        Args:
            user_id: The user's ID
            request: Save session request

        Returns:
            Tuple of (session_dict, message)

        Raises:
            ValueError: If creation blocked or failed
        """
        # Check if session with this name already exists for this user/preset
        existing_session = self.repository.get_by_user_preset_and_name(
            user_id, request.preset_id, request.name
        )

        if existing_session:
            # Update existing session with merged mode data
            return self._update_existing_session(
                existing_session,
                request.data,
                request.mode,
                user_id
            )
        else:
            # Create new session
            return self._create_new_session(user_id, request)

    def _create_new_session(
        self,
        user_id: str,
        request: SaveSessionRequest
    ) -> Tuple[Dict[str, Any], str]:
        """
        Create a new session.

        Args:
            user_id: The user's ID
            request: Save session request

        Returns:
            Tuple of (session_dict, message)

        Raises:
            ValueError: If creation blocked or failed
        """
        # Execute before_create hook
        hook_data, blocked = execute_hook(self.plugins,
            SESSION_HOOKS.before_create,
            {
                "preset_id": request.preset_id,
                "name": request.name,
                "data": request.data,
                "mode": request.mode,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Session creation blocked")
            logger.warning(f"Session creation blocked by plugin: {reason}")
            raise ValueError(reason)

        # Allow hooks to modify data
        session_data = hook_data.get("data", request.data)

        session = Session(
            id=generate_ulid(),
            user_id=user_id,
            preset_id=request.preset_id,
            name=request.name,
            data=session_data
        )

        created_session = self.repository.create(session)

        # Execute after_create hook
        execute_hook(self.plugins,
            SESSION_HOOKS.after_create,
            {
                "session_id": created_session.id,
                "preset_id": created_session.preset_id,
                "name": created_session.name,
                "user_id": user_id
            }
        )

        # First save of a session is always its version 1.
        self._record_version(created_session)

        logger.info(f"Session created: {created_session.name} (id: {created_session.id})")
        return self._session_to_response_dict(created_session), f"Session '{request.name}' saved successfully"

    def _update_existing_session(
        self,
        existing_session: Session,
        new_data: Dict[str, Any],
        mode: Optional[str],
        user_id: str
    ) -> Tuple[Dict[str, Any], str]:
        """
        Update an existing session (called from save_session when session with name exists).

        Args:
            existing_session: The existing session
            new_data: New data to merge
            mode: Optional mode for merging
            user_id: The user's ID

        Returns:
            Tuple of (session_dict, message)

        Raises:
            ValueError: If update blocked or failed
        """
        # Execute before_update hook
        hook_data, blocked = execute_hook(self.plugins,
            SESSION_HOOKS.before_update,
            {
                "session_id": existing_session.id,
                "preset_id": existing_session.preset_id,
                "name": existing_session.name,
                "old_data": existing_session.data,
                "new_data": new_data,
                "mode": mode,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Session update blocked")
            logger.warning(f"Session update blocked by plugin: {reason}")
            raise ValueError(reason)

        # Allow hooks to modify data
        new_data = hook_data.get("new_data", new_data)

        # Merge mode data
        merged_data = self._merge_mode_data(existing_session.data, new_data, mode)

        # Create updated session object
        updated_session = Session(
            id=existing_session.id,
            user_id=existing_session.user_id,
            preset_id=existing_session.preset_id,
            name=existing_session.name,
            data=merged_data,
            created_at=existing_session.created_at
        )

        result = self.repository.update(updated_session)

        # Execute after_update hook
        execute_hook(self.plugins,
            SESSION_HOOKS.after_update,
            {
                "session_id": result.id,
                "preset_id": result.preset_id,
                "name": result.name,
                "user_id": user_id
            }
        )

        self._record_version(result)

        logger.info(f"Session updated: {result.name} (id: {result.id})")
        return self._session_to_response_dict(result), f"Session '{result.name}' updated successfully"

    def update_session(
        self,
        user_id: str,
        session_id: str,
        request: UpdateSessionRequest
    ) -> Dict[str, Any]:
        """
        Update an existing session by ID.

        Executes hooks:
        - session.before_update: Can modify/validate data or block
        - session.after_update: Notification of successful update

        Args:
            user_id: The user's ID
            session_id: The session's ID
            request: Update session request

        Returns:
            Updated session dictionary

        Raises:
            ValueError: If session not found, access denied, name conflict, or update blocked
        """
        session = self.repository.get_by_id(session_id)

        if not session:
            raise ValueError("Session not found")

        # Check ownership
        if session.user_id != user_id:
            raise ValueError("Access denied to this session")

        # Check if trying to rename to an existing session name
        if request.name != session.name:
            existing_with_name = self.repository.get_by_user_preset_and_name(
                user_id, session.preset_id, request.name
            )
            if existing_with_name:
                raise ValueError(f"Session with name '{request.name}' already exists for this preset")

        # Execute before_update hook
        hook_data, blocked = execute_hook(self.plugins,
            SESSION_HOOKS.before_update,
            {
                "session_id": session_id,
                "preset_id": session.preset_id,
                "old_name": session.name,
                "new_name": request.name,
                "old_data": session.data,
                "new_data": request.data,
                "mode": request.mode,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Session update blocked")
            logger.warning(f"Session update blocked by plugin: {reason}")
            raise ValueError(reason)

        # Allow hooks to modify data
        new_data = hook_data.get("new_data", request.data)

        # Merge mode data
        merged_data = self._merge_mode_data(session.data, new_data, request.mode)

        # Create updated session object
        updated_session = Session(
            id=session_id,
            user_id=session.user_id,
            preset_id=session.preset_id,
            name=request.name,
            data=merged_data,
            created_at=session.created_at
        )

        result = self.repository.update(updated_session)

        # Execute after_update hook
        execute_hook(self.plugins,
            SESSION_HOOKS.after_update,
            {
                "session_id": result.id,
                "preset_id": result.preset_id,
                "name": result.name,
                "user_id": user_id
            }
        )

        self._record_version(result)

        logger.info(f"Session updated: {result.name} (id: {result.id})")
        return self._session_to_response_dict(result)

    # ========== Delete Operations ==========

    def delete_session(self, user_id: str, session_id: str) -> str:
        """
        Delete a session.

        Executes hooks:
        - session.before_delete: Can block deletion
        - session.after_delete: Notification of successful deletion

        Args:
            user_id: The user's ID
            session_id: The session's ID

        Returns:
            Success message

        Raises:
            ValueError: If session not found, access denied, or deletion blocked
        """
        session = self.repository.get_by_id(session_id)

        if not session:
            raise ValueError("Session not found")

        # Check ownership
        if session.user_id != user_id:
            raise ValueError("Access denied to this session")

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            SESSION_HOOKS.before_delete,
            {
                "session_id": session_id,
                "preset_id": session.preset_id,
                "name": session.name,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Session deletion blocked")
            logger.warning(f"Session deletion blocked by plugin: {reason}")
            raise ValueError(reason)

        session_name = session.name
        success = self.repository.delete(session_id)

        if not success:
            raise ValueError("Failed to delete session")

        # Execute after_delete hook
        execute_hook(self.plugins,
            SESSION_HOOKS.after_delete,
            {
                "session_id": session_id,
                "preset_id": session.preset_id,
                "name": session_name,
                "user_id": user_id
            }
        )

        logger.info(f"Session deleted: {session_name} (id: {session_id})")
        return f"Session '{session_name}' deleted successfully"
