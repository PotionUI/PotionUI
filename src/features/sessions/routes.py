"""
Session Controller

Handles CRUD operations for user sessions (saved preset configurations).
Delegates business logic to SessionManager.
"""
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.sessions.dto import SaveSessionRequest, UpdateSessionRequest
from src.features.sessions import SessionManager
from src.features.sessions.mappers import (
    session_to_response_dict,
    session_version_summary_to_dict,
    session_version_to_dict,
)
from src.features.sessions.repository import SessionRepository
from src.features.sessions.version_repository import SessionVersionRepository

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class SessionController(BaseController):
    """Controller for managing user sessions."""

    def __init__(
        self,
        session_manager: SessionManager,
        session_repository: SessionRepository,
        session_version_repository: Optional[SessionVersionRepository] = None,
    ):
        super().__init__()
        self.manager = session_manager
        self.repository = session_repository
        # Optional, matching SessionManager - version history is a no-op
        # (empty list / "not found") when this isn't wired.
        self.version_repository = session_version_repository

    async def get_sessions_for_preset(self, user_id: str, preset_id: str) -> APIResponse:
        """Get all sessions for a user and preset."""
        try:
            sessions = self.repository.get_by_user_and_preset(user_id, preset_id)
            return self.success_response(data=[session_to_response_dict(s) for s in sessions])
        except ValueError as e:
            return self.error_api_response(error="get_sessions_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(
                error="get_sessions_failed",
                message=f"Failed to get sessions: {str(e)}"
            )

    def _get_owned_session_record(self, user_id: str, session_id: str):
        """Look up a session by id and enforce ownership, raising distinct
        errors for "missing" vs "belongs to someone else". Pure DB read."""
        session = self.repository.get_by_id(session_id)

        if not session:
            raise ValueError("Session not found")

        if session.user_id != user_id:
            raise ValueError("Access denied to this session")

        return session

    def _get_session_or_404(self, user_id: str, session_id: str):
        """Look up a session by id and enforce ownership, collapsing "missing"
        and "belongs to someone else" into a single "Session not found" -
        the house 404-not-403 idiom, so the response can't be used to probe
        which session ids exist. Pure DB read."""
        session = self.repository.get_by_id(session_id)
        if not session or session.user_id != user_id:
            raise ValueError("Session not found")
        return session

    async def get_session_by_id(self, user_id: str, session_id: str) -> APIResponse:
        """Get a specific session by ID."""
        try:
            session = self._get_owned_session_record(user_id, session_id)
            return self.success_response(data=session_to_response_dict(session))
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return self.error_api_response(
                    error="session_not_found",
                    message=error_msg
                )
            elif "access denied" in error_msg.lower():
                return self.error_api_response(
                    error="session_access_denied",
                    message=error_msg
                )
            return self.error_api_response(error="get_session_failed", message=error_msg)
        except Exception as e:
            return self.error_api_response(
                error="get_session_failed",
                message=f"Failed to get session: {str(e)}"
            )

    async def save_session(self, user_id: str, request: SaveSessionRequest) -> APIResponse:
        """Save a new session or update existing one with same name."""
        try:
            session_data, message = self.manager.save_session(user_id, request)
            return self.success_response(data=session_data, message=message)
        except ValueError as e:
            return self.error_api_response(error="save_session_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(
                error="save_session_failed",
                message=f"Failed to save session: {str(e)}"
            )

    async def update_session(
        self,
        user_id: str,
        session_id: str,
        request: UpdateSessionRequest
    ) -> APIResponse:
        """Update an existing session."""
        try:
            session_data = self.manager.update_session(user_id, session_id, request)
            return self.success_response(data=session_data, message="Session updated successfully")
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return self.error_api_response(
                    error="session_not_found",
                    message=error_msg
                )
            elif "access denied" in error_msg.lower():
                return self.error_api_response(
                    error="session_access_denied",
                    message=error_msg
                )
            elif "already exists" in error_msg.lower():
                return self.error_api_response(
                    error="session_name_exists",
                    message=error_msg
                )
            return self.error_api_response(error="update_session_failed", message=error_msg)
        except Exception as e:
            return self.error_api_response(
                error="update_session_failed",
                message=f"Failed to update session: {str(e)}"
            )

    async def list_session_versions(self, user_id: str, session_id: str) -> APIResponse:
        """List a session's version history (newest first, no payloads)."""
        try:
            self._get_session_or_404(user_id, session_id)

            if not self.version_repository:
                versions: List[Dict[str, Any]] = []
            else:
                versions = [
                    session_version_summary_to_dict(v)
                    for v in self.version_repository.list_for_session(session_id)
                ]
            return self.success_response(data=versions)
        except ValueError as e:
            # House 404-not-403 idiom: missing session and "belongs to someone
            # else" both raise "Session not found", so this single 404 branch
            # can't be used to probe which session ids exist.
            return self.error_response(error="session_not_found", message=str(e), status_code=404)
        except Exception as e:
            return self.error_api_response(
                error="list_session_versions_failed",
                message=f"Failed to list session versions: {str(e)}"
            )

    async def get_session_version(self, user_id: str, session_id: str, version_number: int) -> APIResponse:
        """Get a single version's full payload."""
        try:
            self._get_session_or_404(user_id, session_id)

            if not self.version_repository:
                raise ValueError("Version not found")

            version = self.version_repository.get(session_id, version_number)
            if not version:
                raise ValueError("Version not found")

            return self.success_response(data=session_version_to_dict(version))
        except ValueError as e:
            error_msg = str(e)
            error_code = "session_not_found" if error_msg == "Session not found" else "session_version_not_found"
            return self.error_response(error=error_code, message=error_msg, status_code=404)
        except Exception as e:
            return self.error_api_response(
                error="get_session_version_failed",
                message=f"Failed to get session version: {str(e)}"
            )

    async def delete_session(self, user_id: str, session_id: str) -> APIResponse:
        """Delete a session."""
        try:
            message = self.manager.delete_session(user_id, session_id)
            return self.success_response(message=message)
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return self.error_api_response(
                    error="session_not_found",
                    message=error_msg
                )
            elif "access denied" in error_msg.lower():
                return self.error_api_response(
                    error="session_access_denied",
                    message=error_msg
                )
            return self.error_api_response(error="delete_session_failed", message=error_msg)
        except Exception as e:
            return self.error_api_response(
                error="delete_session_failed",
                message=f"Failed to delete session: {str(e)}"
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.session_controller
    router = APIRouter(prefix="/api/sessions", tags=["Sessions"])

    @router.get("/preset/{preset_id}", response_model=APIResponse, summary="Get Sessions for Preset")
    async def get_sessions_for_preset(preset_id: str, current_user=Depends(get_current_active_user)):
        """Get all sessions for the current user and specified preset."""
        return await controller.get_sessions_for_preset(current_user.id, preset_id)

    # Session history — registered ahead of GET /{session_id} so the
    # extra path segments always win the match (FastAPI routes on path shape,
    # not registration order, but keeping specific-before-generic here matches
    # the house idiom used elsewhere, e.g. src/features/setup/routes.py).
    @router.get(
        "/{session_id}/versions",
        response_model=APIResponse,
        summary="List Session Versions",
    )
    async def list_session_versions(session_id: str, current_user=Depends(get_current_active_user)):
        """List a session's version history, newest first (no payloads)."""
        return await controller.list_session_versions(current_user.id, session_id)

    @router.get(
        "/{session_id}/versions/{version_number}",
        response_model=APIResponse,
        summary="Get Session Version",
    )
    async def get_session_version(
        session_id: str,
        version_number: int,
        current_user=Depends(get_current_active_user)
    ):
        """Get a single version's full payload."""
        return await controller.get_session_version(current_user.id, session_id, version_number)

    @router.get("/{session_id}", response_model=APIResponse, summary="Get Session by ID")
    async def get_session_by_id(session_id: str, current_user=Depends(get_current_active_user)):
        """Get a specific session by ID."""
        return await controller.get_session_by_id(current_user.id, session_id)

    @router.post("/save", response_model=APIResponse, summary="Save Session")
    async def save_session(request: SaveSessionRequest, current_user=Depends(get_current_active_user)):
        """Save a new session or update existing one with same name."""
        return await controller.save_session(current_user.id, request)

    @router.put("/{session_id}", response_model=APIResponse, summary="Update Session")
    async def update_session(
        session_id: str,
        request: UpdateSessionRequest,
        current_user=Depends(get_current_active_user)
    ):
        """Update an existing session."""
        return await controller.update_session(current_user.id, session_id, request)

    @router.delete("/{session_id}", response_model=APIResponse, summary="Delete Session")
    async def delete_session(session_id: str, current_user=Depends(get_current_active_user)):
        """Delete a session."""
        return await controller.delete_session(current_user.id, session_id)

    return router
