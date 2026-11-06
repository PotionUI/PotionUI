"""
Session Controller

Handles CRUD operations for user sessions (saved preset configurations).
Delegates mutations to `src.features.sessions.operations`.
"""
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.platform.http.base_controller import APIError, APIResponse, BaseController
from src.platform.http.errors import AccessDeniedError, NotFoundError
from src.platform.security.current_user import get_current_active_user
from src.features.sessions.dto import PinSessionRequest, SaveSessionRequest, UpdateSessionRequest
from src.features.sessions import operations
from src.features.sessions.live import CLIENT_HEADER, WriteOrigin, announce, browser_or_api_origin
from src.features.sessions.operations.save import SessionVersionConflict
from src.features.sessions.mappers import (
    session_to_response_dict,
    session_version_list_to_dicts,
    session_version_to_dict,
)
from src.features.sessions.repository import SessionRepository
from src.features.sessions.version_repository import SessionVersionRepository
from src.platform.plugins import PluginRegistry

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

MISSING_CODES = frozenset({"session_not_found", "session_version_not_found"})



class SessionController(BaseController):

    def __init__(
        self,
        session_repository: SessionRepository,
        plugin_registry: PluginRegistry,
        session_version_repository: Optional[SessionVersionRepository] = None,
        file_preset_repository: Optional[Any] = None,
        notifier: Optional[Any] = None,
    ):
        super().__init__()
        self.repository = session_repository
        self.plugins = plugin_registry
        self.version_repository = session_version_repository
        self.file_preset_repository = file_preset_repository
        self.notifier = notifier

    @staticmethod
    def _conflict_as_409(write: Callable[..., Any]) -> Callable[..., Any]:
        def guarded(*args: Any) -> Any:
            try:
                return write(*args)
            except SessionVersionConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.payload())
        return guarded

    def _get_owned_session_record(self, user_id: str, session_id: str):
        session = self.repository.get_by_id(session_id)
        if not session:
            raise NotFoundError("session_not_found", "Session not found")
        if session.user_id != user_id:
            raise AccessDeniedError("session_access_denied", "Access denied to this session")
        return session

    def _get_session_or_404(self, user_id: str, session_id: str):
        session = self.repository.get_by_id(session_id)
        if not session or session.user_id != user_id:
            raise NotFoundError("session_not_found", "Session not found")
        return session

    def _missing_as_404(self, response: APIResponse) -> APIResponse:
        if not response.success and response.error in MISSING_CODES:
            raise HTTPException(
                status_code=404,
                detail=APIError(error=response.error, message=response.message).model_dump(),
            )
        return response

    async def get_sessions_for_preset(self, user_id: str, preset_id: str) -> APIResponse:
        def load():
            return [session_to_response_dict(s) for s in self.repository.get_by_user_and_preset(user_id, preset_id)]
        return await self.run("get_sessions_failed", load, soft=True, message="Failed to get sessions")

    async def get_session_by_id(self, user_id: str, session_id: str) -> APIResponse:
        def load():
            return session_to_response_dict(self._get_owned_session_record(user_id, session_id))
        return await self.run("get_session_failed", load, soft=True, message="Failed to get session")

    async def save_session(
        self, user_id: str, request: SaveSessionRequest, origin: Optional[WriteOrigin] = None
    ) -> APIResponse:
        origin = origin or browser_or_api_origin(None)
        response = await self.run(
            "save_session_failed", self._conflict_as_409(operations.save_session),
            self.repository, self.plugins, self.version_repository, self.file_preset_repository, user_id, request,
            origin,
            soft=True, message="Failed to save session",
        )
        if response.success:
            response.data, response.message = response.data
            announce(self.notifier, user_id, response.data, origin)
        return response

    async def update_session(
        self,
        user_id: str,
        session_id: str,
        request: UpdateSessionRequest,
        origin: Optional[WriteOrigin] = None,
    ) -> APIResponse:
        origin = origin or browser_or_api_origin(None)
        response = await self.run(
            "update_session_failed", self._conflict_as_409(operations.update_session),
            self.repository, self.plugins, self.version_repository, self.file_preset_repository,
            user_id, session_id, request, origin,
            soft=True, message="Failed to update session",
        )
        if response.success:
            response.message = "Session updated successfully"
            announce(self.notifier, user_id, response.data, origin)
        return response

    async def set_session_pinned(self, user_id: str, session_id: str, request: PinSessionRequest) -> APIResponse:
        def pin():
            self._get_session_or_404(user_id, session_id)
            session = self.repository.set_pinned(session_id, request.pinned)
            if session is None:
                raise NotFoundError("session_not_found", "Session not found")
            return session_to_response_dict(session)
        return self._missing_as_404(
            await self.run("pin_session_failed", pin, soft=True, message="Failed to pin session")
        )

    async def list_session_versions(
        self,
        user_id: str,
        session_id: str,
        limit: Optional[int] = None,
        before: Optional[int] = None,
    ) -> APIResponse:
        def list_versions() -> List[Dict[str, Any]]:
            self._get_session_or_404(user_id, session_id)
            if not self.version_repository:
                return []
            page, predecessor = self.version_repository.list_page_with_payloads(
                session_id, limit=limit, before=before
            )
            return session_version_list_to_dicts(page, predecessor)
        return self._missing_as_404(
            await self.run("list_session_versions_failed", list_versions, soft=True,
                           message="Failed to list session versions")
        )

    async def get_session_version(self, user_id: str, session_id: str, version_number: int) -> APIResponse:
        def get_version():
            self._get_session_or_404(user_id, session_id)
            version = self.version_repository.get(session_id, version_number) if self.version_repository else None
            if not version:
                raise NotFoundError("session_version_not_found", "Version not found")
            return session_version_to_dict(version)
        return self._missing_as_404(
            await self.run("get_session_version_failed", get_version, soft=True,
                           message="Failed to get session version")
        )

    async def delete_session(self, user_id: str, session_id: str) -> APIResponse:
        response = await self.run(
            "delete_session_failed", operations.delete_session, self.repository, self.plugins, user_id, session_id,
            soft=True, message="Failed to delete session",
        )
        if response.success:
            response.message, response.data = response.data, None
        return response

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
    async def list_session_versions(
        session_id: str,
        limit: Optional[int] = Query(None, ge=1, le=200),
        before: Optional[int] = Query(None, ge=1),
        current_user=Depends(get_current_active_user),
    ):
        """List a session's version history, newest first (no payloads)."""
        return await controller.list_session_versions(current_user.id, session_id, limit, before)

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
    async def save_session(
        request: SaveSessionRequest, http: Request, current_user=Depends(get_current_active_user)
    ):
        """Save a new session or update existing one with same name."""
        return await controller.save_session(
            current_user.id, request, browser_or_api_origin(http.headers.get(CLIENT_HEADER))
        )

    @router.put("/{session_id}/pin", response_model=APIResponse, summary="Pin or Unpin Session")
    async def set_session_pinned(
        session_id: str,
        request: PinSessionRequest,
        current_user=Depends(get_current_active_user)
    ):
        return await controller.set_session_pinned(current_user.id, session_id, request)

    @router.put("/{session_id}", response_model=APIResponse, summary="Update Session")
    async def update_session(
        session_id: str,
        request: UpdateSessionRequest,
        http: Request,
        current_user=Depends(get_current_active_user)
    ):
        """Update an existing session."""
        return await controller.update_session(
            current_user.id, session_id, request, browser_or_api_origin(http.headers.get(CLIENT_HEADER))
        )

    @router.delete("/{session_id}", response_model=APIResponse, summary="Delete Session")
    async def delete_session(session_id: str, current_user=Depends(get_current_active_user)):
        """Delete a session."""
        return await controller.delete_session(current_user.id, session_id)

    return router
