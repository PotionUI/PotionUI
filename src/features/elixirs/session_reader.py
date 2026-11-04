from typing import Any, Dict, Optional

from src.features.elixirs.errors import SessionNotFoundError


class SessionReader:
    def __init__(self, sessions: Any, versions: Any) -> None:
        self.sessions = sessions
        self.versions = versions

    def _owned(self, user, session_id: str):
        session = self.sessions.get_by_id(session_id)
        if session is None or session.user_id != getattr(user, "id", None):
            raise SessionNotFoundError(f"Session '{session_id}' not found")
        return session

    def get_session(self, user, session_id: str) -> Dict[str, Any]:
        session = self._owned(user, session_id)
        return {
            "session_id": session.id,
            "preset_id": session.preset_id,
            "name": session.name,
            "version": session.version,
            "updated_at": (session.updated_at or session.created_at).isoformat(),
            "mode": None,
            "data": session.data or {},
        }

    def get_snapshot(self, user, session_id: str, version: Optional[int] = None) -> Dict[str, Any]:
        session = self._owned(user, session_id)
        data = session.data
        if version is not None:
            record = self.versions.get(session_id, version)
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
