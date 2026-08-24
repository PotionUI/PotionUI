"""
Chat controller - thin layer for HTTP handling.

This controller delegates all business logic to ChatManager and handles:
- HTTP request/response serialization
- Exception mapping to HTTP status codes
- Response formatting for the API

Business logic is in src/features/chat/manager.py
"""

import json
import logging
from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.chat.dto import (
    CreateSessionRequest,
    MemoryUpdateRequest,
    MemoryWriteRequest,
    PromptFeedbackRequest,
    SendMessageRequest,
    ToolApprovalRequest,
    UpdateSessionRequest,
)
from src.features.chat import (
    ChatManager,
    SessionNotFoundException,
    AccessDeniedException,
    SessionClosedException,
    InvalidLLMConfigException,
    MessageCreationFailedException,
    SessionCreationFailedException,
)
from src.features.chat.context_builder import MEMORY_MAX_CONTENT_LEN, MEMORY_MAX_NOTES_PER_GROUP
from src.features.chat.exceptions import UnknownChatModeException
from src.features.chat.turns import ChatTurn, ChatTurnRegistry, TurnAlreadyRunningError
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class ChatController(BaseController):
    """Thin controller for chat endpoints.

    Delegates all business logic to ChatManager and handles HTTP-specific concerns.
    """

    def __init__(self, chat_manager: ChatManager, turn_registry: ChatTurnRegistry):
        super().__init__()
        self.chat_manager = chat_manager
        self.turn_registry = turn_registry

    # --- Endpoints ---

    def create_session(self, request: CreateSessionRequest, user: User) -> APIResponse:
        """Create a new chat session."""
        try:
            session = self.chat_manager.create_session(
                user_id=user.id,
                original_text=request.original_text,
                llm_config_id=request.llm_config_id,
                mode=request.mode,
                name=request.name,
                system_message=request.system_message,
                enabled_tools=request.enabled_tools,
            )
            return self.success_response(
                data=session.model_dump(),
                message="Session created successfully"
            )
        except UnknownChatModeException as e:
            return self.error_api_response(
                error="unknown_mode",
                message=str(e)
            )
        except SessionCreationFailedException as e:
            return self.error_api_response(
                error="session_creation_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error creating session: {e}")
            return self.error_api_response(
                error="session_creation_failed",
                message=f"Failed to create session: {str(e)}"
            )

    def get_sessions(
        self,
        user: User,
        mode: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> APIResponse:
        """List sessions for the history view (filterable, paginated)."""
        try:
            sessions, total = self.chat_manager.list_sessions(
                user_id=user.id,
                mode=mode,
                search=search,
                limit=limit,
                offset=offset
            )
            return self.success_response(
                data={
                    'sessions': [s.model_dump() for s in sessions],
                    'total': total,
                    'limit': limit,
                    'offset': offset,
                }
            )
        except Exception as e:
            logger.exception(f"Error getting sessions: {e}")
            return self.error_api_response(
                error="get_sessions_failed",
                message=f"Failed to get sessions: {str(e)}"
            )

    def get_modes(self) -> APIResponse:
        """List all registered chat modes."""
        modes = []
        for mode in self.chat_manager.chat_mode_registry.get_all():
            modes.append({
                "id": mode.id,
                "name": mode.name,
                "description": mode.description,
                "icon": mode.icon,
                "default_route_prefixes": mode.default_route_prefixes,
                "tools": [t.name for t in self._tools_for_mode(mode)],
                "resource_namespaces": mode.resource_namespaces,
                "source": mode.source,
            })
        return self.success_response(data={"modes": modes})

    def _tools_for_mode(self, mode):
        """Resolve the tools visible in a mode (empty when no executor)."""
        if not self.chat_manager.tool_executor:
            return []
        return self.chat_manager.tool_executor.tool_registry.get_for_mode(mode)

    def get_session(self, session_id: str, user: User) -> APIResponse:
        """Get a session with all messages."""
        try:
            session = self.chat_manager.get_session(session_id, user.id)
            # Surface an in-flight turn so the client can reattach to its live
            # stream on reload instead of dead-ending on a lost response.
            active = self.turn_registry.active(session_id)
            return self.success_response(
                data={
                    **session.model_dump(),
                    "active_turn": active.status_snapshot() if active else None,
                }
            )
        except SessionNotFoundException:
            return self.error_api_response(
                error="session_not_found",
                message="Session not found"
            )
        except AccessDeniedException:
            return self.error_api_response(
                error="access_denied",
                message="You don't have access to this session"
            )
        except Exception as e:
            logger.exception(f"Error getting session: {e}")
            return self.error_api_response(
                error="get_session_failed",
                message=f"Failed to get session: {str(e)}"
            )

    async def send_message(
        self,
        session_id: str,
        request: SendMessageRequest,
        user: User
    ) -> APIResponse:
        """Send a message and get AI response."""
        try:
            result = await self.chat_manager.send_message(
                session_id=session_id,
                user_id=user.id,
                content=request.content,
                image_data=request.image_data,
                context_metadata=request.context_metadata,
                resources=[r.uri for r in request.resources] if request.resources else None,
            )
            return self.success_response(
                data=result.model_dump()
            )
        except SessionNotFoundException:
            return self.error_api_response(
                error="session_not_found",
                message="Session not found"
            )
        except AccessDeniedException:
            return self.error_api_response(
                error="access_denied",
                message="You don't have access to this session"
            )
        except SessionClosedException:
            return self.error_api_response(
                error="session_closed",
                message="Cannot send messages to a closed session"
            )
        except InvalidLLMConfigException:
            return self.error_api_response(
                error="no_llm_config",
                message="Session has no LLM configuration"
            )
        except UnknownChatModeException as e:
            return self.error_api_response(
                error="unknown_mode",
                message=str(e)
            )
        except MessageCreationFailedException as e:
            return self.error_api_response(
                error="message_creation_failed",
                message=str(e)
            )
        except ValueError as e:
            return self.error_api_response(
                error="llm_error",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error sending message: {e}")
            return self.error_api_response(
                error="send_message_failed",
                message=f"Failed to send message: {str(e)}"
            )

    def _turn_stream_factory(self, session_id: str, request: SendMessageRequest, user: User):
        """Wrap the conversation runner so validation/known errors become SSE
        error events, matching the pre-turn-registry streaming behavior.

        Returned as a zero-arg factory so the turn's drive task — not the request
        — owns the generator's lifetime.
        """
        def factory():
            async def gen():
                try:
                    async for event in self.chat_manager.send_message_stream(
                        session_id=session_id,
                        user_id=user.id,
                        content=request.content,
                        image_data=request.image_data,
                        context_metadata=request.context_metadata,
                        resources=[r.uri for r in request.resources] if request.resources else None,
                    ):
                        yield event
                except SessionNotFoundException:
                    yield {"event": "error", "data": {"error": "session_not_found", "message": "Session not found"}}
                except AccessDeniedException:
                    yield {"event": "error", "data": {"error": "access_denied", "message": "Access denied"}}
                except SessionClosedException:
                    yield {"event": "error", "data": {"error": "session_closed", "message": "Session is closed"}}
                except InvalidLLMConfigException:
                    yield {"event": "error", "data": {"error": "no_llm_config", "message": "No LLM configuration"}}
                except UnknownChatModeException as e:
                    yield {"event": "error", "data": {"error": "unknown_mode", "message": str(e)}}
                except Exception as e:
                    logger.exception(f"Error in streaming: {e}")
                    yield {"event": "error", "data": {"error": "stream_error", "message": str(e)}}
            return gen()
        return factory

    @staticmethod
    def _sse_response(event_source) -> StreamingResponse:
        """Serialize an async iterator of ``{event, data}`` dicts as an SSE stream."""
        async def formatted():
            async for event in event_source:
                event_type = event.get("event", "message")
                event_data = json.dumps(event.get("data", {}))
                yield f"event: {event_type}\ndata: {event_data}\n\n"

        return StreamingResponse(
            formatted(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            }
        )

    async def send_message_stream(
        self,
        session_id: str,
        request: SendMessageRequest,
        user: User
    ) -> StreamingResponse:
        """Start a backend-owned turn and subscribe this connection to its stream.

        The turn runs to completion (and persists) even if this SSE connection
        drops; the client can reattach via ``reattach_stream``.
        """
        try:
            turn = self.turn_registry.start(
                session_id=session_id,
                user_id=user.id,
                stream_factory=self._turn_stream_factory(session_id, request, user),
            )
        except TurnAlreadyRunningError:
            async def busy():
                yield {"event": "error", "data": {
                    "error": "turn_in_progress",
                    "message": "A response is already being generated for this conversation.",
                }}
            return self._sse_response(busy())

        return self._sse_response(turn.stream())

    async def reattach_stream(self, session_id: str, user: User) -> StreamingResponse:
        """Subscribe to the session's in-flight (or just-finished) turn.

        Used on reload to resume a live response. Auth mirrors the other chat
        routes: ``get_session`` raises if the user can't access the session.
        """
        try:
            self.chat_manager.get_session(session_id, user.id)
        except SessionNotFoundException:
            async def not_found():
                yield {"event": "error", "data": {"error": "session_not_found", "message": "Session not found"}}
            return self._sse_response(not_found())
        except AccessDeniedException:
            async def denied():
                yield {"event": "error", "data": {"error": "access_denied", "message": "Access denied"}}
            return self._sse_response(denied())

        turn: Optional[ChatTurn] = self.turn_registry.get(session_id)
        if turn is None:
            async def none_active():
                yield {"event": "no_active_turn", "data": {}}
            return self._sse_response(none_active())

        return self._sse_response(turn.stream())

    async def cancel_turn(self, session_id: str, user: User) -> APIResponse:
        """Explicitly stop the session's in-flight turn (the stop button)."""
        try:
            self.chat_manager.get_session(session_id, user.id)
        except SessionNotFoundException:
            return self.error_api_response(error="session_not_found", message="Session not found")
        except AccessDeniedException as e:
            return self.error_api_response(error="access_denied", message=str(e))

        turn = self.turn_registry.active(session_id)
        if turn is None:
            return self.success_response(data={"cancelled": False})
        turn.request_cancel()
        return self.success_response(data={"cancelled": True, "turn_id": turn.turn_id})

    def accept_session(self, session_id: str, user: User) -> APIResponse:
        """Accept the session's suggestion and close it."""
        try:
            self.chat_manager.accept_session(session_id, user.id)
            return self.success_response(
                message="Session accepted and closed"
            )
        except SessionNotFoundException:
            return self.error_api_response(
                error="session_not_found",
                message="Session not found"
            )
        except AccessDeniedException as e:
            return self.error_api_response(
                error="access_denied",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error accepting session: {e}")
            return self.error_api_response(
                error="accept_failed",
                message=f"Failed to accept session: {str(e)}"
            )

    def reject_session(self, session_id: str, user: User) -> APIResponse:
        """Reject the session's suggestion and close it."""
        try:
            self.chat_manager.reject_session(session_id, user.id)
            return self.success_response(
                message="Session rejected and closed"
            )
        except SessionNotFoundException:
            return self.error_api_response(
                error="session_not_found",
                message="Session not found"
            )
        except AccessDeniedException as e:
            return self.error_api_response(
                error="access_denied",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error rejecting session: {e}")
            return self.error_api_response(
                error="reject_failed",
                message=f"Failed to reject session: {str(e)}"
            )

    def update_session(
        self,
        session_id: str,
        request: UpdateSessionRequest,
        user: User
    ) -> APIResponse:
        """Update session properties (e.g., name)."""
        try:
            session = self.chat_manager.update_session(
                session_id=session_id,
                user_id=user.id,
                name=request.name,
                llm_config_id=request.llm_config_id,
            )
            return self.success_response(
                data=session.model_dump(),
                message="Session updated successfully"
            )
        except SessionNotFoundException:
            return self.error_api_response(
                error="session_not_found",
                message="Session not found"
            )
        except AccessDeniedException:
            return self.error_api_response(
                error="access_denied",
                message="You don't have access to this session"
            )
        except SessionCreationFailedException as e:
            return self.error_api_response(
                error="update_failed",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error updating session: {e}")
            return self.error_api_response(
                error="update_failed",
                message=f"Failed to update session: {str(e)}"
            )

    async def suggest_resources(
        self,
        query: str,
        mode: Optional[str],
        limit: int,
        user: User,
    ) -> APIResponse:
        """Suggest @resource completions for the chat input dropdown."""
        try:
            suggestions = await self.chat_manager.suggest_resources(
                query=query,
                mode_id=mode,
                user_id=user.id,
                limit=max(1, min(limit, 50)),
            )
            return self.success_response(data={
                "suggestions": [
                    {
                        "uri": s.uri,
                        "label": s.label,
                        "kind": s.kind,
                        "description": s.description,
                        "has_children": s.has_children,
                        "icon": s.icon,
                        "attachable": s.attachable,
                    }
                    for s in suggestions
                ]
            })
        except Exception as e:
            logger.exception(f"Error suggesting resources: {e}")
            return self.error_api_response(
                error="suggest_resources_failed",
                message=f"Failed to suggest resources: {str(e)}"
            )

    def list_tools(self, mode: Optional[str] = None) -> APIResponse:
        """List available LLM tools, optionally filtered to a chat mode."""
        if not self.chat_manager.tool_executor:
            return self.success_response(data={"tools": []})

        registry = self.chat_manager.tool_executor.tool_registry
        if mode:
            try:
                mode_obj = self.chat_manager.chat_mode_registry.require(mode)
            except UnknownChatModeException as e:
                return self.error_api_response(error="unknown_mode", message=str(e))
            tool_list = registry.get_for_mode(mode_obj)
        else:
            tool_list = registry.get_all()

        tools = []
        for tool in tool_list:
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "hint": tool.hint,
                "requires_approval": tool.requires_approval,
                "mode": tool.modes[0] if tool.modes else None,
                "modes": tool.modes,
                "icon": tool.icon,
                "label": tool.label,
                "group": tool.group,
                "user_description": tool.user_description,
            })
        return self.success_response(data={"tools": tools})

    def list_pre_chat_actions(self) -> APIResponse:
        """List all registered pre-chat actions."""
        if not self.chat_manager.pre_chat_action_manager:
            return self.success_response(data={"actions": []})

        actions = self.chat_manager.pre_chat_action_manager.get_all_actions()
        return self.success_response(data={
            "actions": [
                {
                    "id": a.id,
                    "name": a.name,
                    "description": a.description,
                    "plugin_id": a.plugin_id,
                    "default_enabled": a.default_enabled,
                    "blocking": a.blocking,
                    "category": a.category,
                }
                for a in actions
            ]
        })

    async def approve_tool(
        self,
        session_id: str,
        request: ToolApprovalRequest,
        user: User,
    ) -> APIResponse:
        """Approve or reject a pending tool execution."""
        try:
            result = await self.chat_manager.approve_tool_execution(
                session_id=session_id,
                user_id=user.id,
                message_id=request.message_id,
                tool_index=request.tool_index,
                approved=request.approved,
            )
            return self.success_response(data=result)
        except SessionNotFoundException as e:
            return self.error_api_response(
                error="session_not_found",
                message=str(e),
            )
        except AccessDeniedException as e:
            return self.error_api_response(
                error="access_denied",
                message=str(e),
            )
        except MessageCreationFailedException as e:
            return self.error_api_response(
                error="tool_approval_failed",
                message=str(e),
            )
        except Exception as e:
            logger.exception(f"Error approving tool execution: {e}")
            return self.error_api_response(
                error="tool_approval_failed",
                message=str(e),
            )

    async def prompt_feedback(
        self,
        session_id: str,
        message_id: str,
        request: PromptFeedbackRequest,
        user: User,
    ) -> APIResponse:
        """Record an approve/reject verdict on an enhancement-proposed prompt."""
        try:
            result = await self.chat_manager.record_prompt_feedback(
                session_id=session_id,
                user_id=user.id,
                message_id=message_id,
                action_index=request.action_index,
                verdict=request.verdict,
                reason=request.reason,
            )
            return self.success_response(data=result)
        except SessionNotFoundException as e:
            return self.error_api_response(
                error="session_not_found",
                message=str(e),
            )
        except AccessDeniedException as e:
            return self.error_api_response(
                error="access_denied",
                message=str(e),
            )
        except MessageCreationFailedException as e:
            return self.error_api_response(
                error="prompt_feedback_failed",
                message=str(e),
            )
        except Exception as e:
            logger.exception(f"Error recording prompt feedback: {e}")
            return self.error_api_response(
                error="prompt_feedback_failed",
                message=str(e),
            )

    def list_memory_notes(
        self,
        user: User,
        scope: Optional[str] = None,
        scope_ref: Optional[str] = None,
    ) -> APIResponse:
        """List the user's persistent LLM memory notes, optionally filtered."""
        if not self.chat_manager.llm_memory_manager:
            return self.error_api_response(error="memory_unavailable", message="Memory manager not available")
        notes = self.chat_manager.llm_memory_manager.read_notes(user.id, scope=scope, scope_ref=scope_ref)
        return self.success_response(data={
            "notes": [note.to_dict() for note in notes],
            "injection": {
                "cap_per_group": MEMORY_MAX_NOTES_PER_GROUP,
                "max_content_len": MEMORY_MAX_CONTENT_LEN,
            },
        })

    def write_memory_note(self, request: MemoryWriteRequest, user: User) -> APIResponse:
        """Create or update a persistent LLM memory note."""
        if not self.chat_manager.llm_memory_manager:
            return self.error_api_response(error="memory_unavailable", message="Memory manager not available")
        try:
            note = self.chat_manager.llm_memory_manager.write_note(
                user_id=user.id,
                key=request.key,
                content=request.content,
                scope=request.scope,
                scope_ref=request.scope_ref,
            )
            return self.success_response(data=note.to_dict())
        except ValueError as e:
            return self.error_api_response(error="invalid_memory_note", message=str(e))
        except Exception as e:
            logger.exception(f"Error writing memory note: {e}")
            return self.error_api_response(error="memory_write_failed", message=str(e))

    def update_memory_note(self, note_id: str, request: MemoryUpdateRequest, user: User) -> APIResponse:
        """Update an existing persistent LLM memory note's key/content."""
        if not self.chat_manager.llm_memory_manager:
            return self.error_api_response(error="memory_unavailable", message="Memory manager not available")
        try:
            note = self.chat_manager.llm_memory_manager.update_note(
                user_id=user.id, note_id=note_id, key=request.key, content=request.content,
            )
        except ValueError as e:
            return self.error_api_response(error="invalid_memory_note", message=str(e))
        except Exception as e:
            logger.exception(f"Error updating memory note: {e}")
            return self.error_api_response(error="memory_update_failed", message=str(e))
        if note is None:
            return self.error_api_response(error="note_not_found", message=f"Memory note '{note_id}' not found")
        return self.success_response(data=note.to_dict())

    def delete_memory_note(self, note_id: str, user: User) -> APIResponse:
        """Delete a persistent LLM memory note."""
        if not self.chat_manager.llm_memory_manager:
            return self.error_api_response(error="memory_unavailable", message="Memory manager not available")
        deleted = self.chat_manager.llm_memory_manager.delete_note(user_id=user.id, note_id=note_id)
        if not deleted:
            return self.error_api_response(error="note_not_found", message=f"Memory note '{note_id}' not found")
        return self.success_response(message="Memory note deleted successfully")

    def delete_session(self, session_id: str, user: User) -> APIResponse:
        """Delete a session and all its messages."""
        try:
            self.chat_manager.delete_session(session_id, user.id)
            return self.success_response(
                message="Session deleted successfully"
            )
        except SessionNotFoundException:
            return self.error_api_response(
                error="session_not_found",
                message="Session not found"
            )
        except AccessDeniedException as e:
            return self.error_api_response(
                error="access_denied",
                message=str(e)
            )
        except Exception as e:
            logger.exception(f"Error deleting session: {e}")
            return self.error_api_response(
                error="delete_failed",
                message=f"Failed to delete session: {str(e)}"
            )

    # --- Admin session-debug viewer ---

    def list_admin_sessions(
        self,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> APIResponse:
        """List chat sessions across all users, for the admin debug viewer."""
        try:
            result = self.chat_manager.list_admin_sessions(search=search, limit=limit, offset=offset)
            return self.success_response(data={**result, "limit": limit, "offset": offset})
        except Exception as e:
            logger.exception(f"Error listing admin sessions: {e}")
            return self.error_api_response(
                error="list_admin_sessions_failed",
                message=f"Failed to list sessions: {str(e)}"
            )

    def get_admin_session_detail(self, session_id: str) -> APIResponse:
        """A session's messages plus every LLM call trace, for the admin debug viewer."""
        try:
            result = self.chat_manager.get_admin_session_detail(session_id)
            return self.success_response(data=result)
        except SessionNotFoundException:
            return self.error_api_response(
                error="session_not_found",
                message="Session not found"
            )
        except Exception as e:
            logger.exception(f"Error getting admin session detail: {e}")
            return self.error_api_response(
                error="get_admin_session_detail_failed",
                message=f"Failed to get session detail: {str(e)}"
            )

    def clear_traces(self, session_id: Optional[str] = None) -> APIResponse:
        """Delete LLM call traces for one session, or every session when omitted."""
        try:
            deleted = self.chat_manager.clear_traces(session_id)
            return self.success_response(data={"deleted": deleted})
        except Exception as e:
            logger.exception(f"Error clearing call traces: {e}")
            return self.error_api_response(
                error="clear_traces_failed",
                message=f"Failed to clear traces: {str(e)}"
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.chat_controller
    router = APIRouter(prefix="/api/chat", tags=["Chat"])

    @router.post("/sessions", response_model=APIResponse, summary="Create a chat session")
    async def create_session(
        request: CreateSessionRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Create a new chat session."""
        return controller.create_session(request, current_user)

    @router.get("/sessions", response_model=APIResponse, summary="List chat sessions")
    async def get_sessions(
        mode: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        current_user: User = Depends(get_current_active_user)
    ):
        """List sessions for the history view (filterable, paginated)."""
        return controller.get_sessions(current_user, mode, search, limit, offset)

    @router.get("/modes", response_model=APIResponse, summary="List chat modes")
    async def get_modes(
        current_user: User = Depends(get_current_active_user)
    ):
        """List all registered chat modes."""
        return controller.get_modes()

    @router.get("/sessions/{session_id}", response_model=APIResponse, summary="Get a chat session")
    async def get_session(
        session_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Get a session with all messages."""
        return controller.get_session(session_id, current_user)

    @router.post("/sessions/{session_id}/messages", response_model=APIResponse, summary="Send a chat message")
    async def send_message(
        session_id: str,
        request: SendMessageRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Send a message and get AI response."""
        return await controller.send_message(session_id, request, current_user)

    @router.post("/sessions/{session_id}/messages/stream", summary="Stream a chat message response (SSE)")
    async def send_message_stream(
        session_id: str,
        request: SendMessageRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Send a message and stream the AI response via SSE."""
        return await controller.send_message_stream(session_id, request, current_user)

    @router.get("/sessions/{session_id}/messages/stream", summary="Reattach to an in-flight turn (SSE)")
    async def reattach_stream(
        session_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Resume the live stream of a turn already running for this session."""
        return await controller.reattach_stream(session_id, current_user)

    @router.post("/sessions/{session_id}/turn/cancel", response_model=APIResponse, summary="Cancel the in-flight turn")
    async def cancel_turn(
        session_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Explicitly stop the session's in-flight turn."""
        return await controller.cancel_turn(session_id, current_user)

    @router.post("/sessions/{session_id}/accept", response_model=APIResponse, summary="Accept a session's suggestion")
    async def accept_session(
        session_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Accept the session's suggestion and close it."""
        return controller.accept_session(session_id, current_user)

    @router.post("/sessions/{session_id}/reject", response_model=APIResponse, summary="Reject a session's suggestion")
    async def reject_session(
        session_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Reject the session's suggestion and close it."""
        return controller.reject_session(session_id, current_user)

    @router.put("/sessions/{session_id}", response_model=APIResponse, summary="Update a chat session")
    async def update_session(
        session_id: str,
        request: UpdateSessionRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Update session properties."""
        return controller.update_session(session_id, request, current_user)

    @router.delete("/sessions/{session_id}", response_model=APIResponse, summary="Delete a chat session")
    async def delete_session(
        session_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Delete a session and all its messages."""
        return controller.delete_session(session_id, current_user)

    @router.get("/tools", response_model=APIResponse, summary="List available LLM tools")
    async def list_tools(
        mode: Optional[str] = None,
        current_user: User = Depends(get_current_active_user)
    ):
        """List available LLM tools, optionally filtered to a chat mode."""
        return controller.list_tools(mode)

    @router.get("/resources/suggest", response_model=APIResponse, summary="Suggest @resource completions")
    async def suggest_resources(
        query: str = "",
        mode: Optional[str] = None,
        limit: int = 15,
        current_user: User = Depends(get_current_active_user)
    ):
        """Suggest @resource completions for the chat input dropdown."""
        return await controller.suggest_resources(query, mode, limit, current_user)

    @router.get("/pre-actions", response_model=APIResponse, summary="List registered pre-chat actions")
    async def list_pre_chat_actions(
        current_user: User = Depends(get_current_active_user)
    ):
        """List all registered pre-chat actions."""
        return controller.list_pre_chat_actions()

    @router.post(
        "/sessions/{session_id}/messages/{message_id}/prompt-feedback",
        response_model=APIResponse,
        summary="Record approve/reject feedback on an enhanced prompt",
    )
    async def prompt_feedback(
        session_id: str,
        message_id: str,
        request: PromptFeedbackRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Record an approve/reject verdict on an enhancement-proposed prompt."""
        return await controller.prompt_feedback(session_id, message_id, request, current_user)

    @router.post("/sessions/{session_id}/tools/approve", response_model=APIResponse, summary="Approve or reject a pending tool execution")
    async def approve_tool_execution(
        session_id: str,
        request: ToolApprovalRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Approve or reject a pending tool execution."""
        return await controller.approve_tool(session_id, request, current_user)

    @router.get("/memory", response_model=APIResponse, summary="List LLM memory notes")
    async def list_memory_notes(
        scope: Optional[str] = None,
        scope_ref: Optional[str] = None,
        current_user: User = Depends(get_current_active_user)
    ):
        """List the current user's persistent LLM memory notes."""
        return controller.list_memory_notes(current_user, scope, scope_ref)

    @router.post("/memory", response_model=APIResponse, summary="Create or update an LLM memory note")
    async def write_memory_note(
        request: MemoryWriteRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Create or update a persistent LLM memory note."""
        return controller.write_memory_note(request, current_user)

    @router.put("/memory/{note_id}", response_model=APIResponse, summary="Update an LLM memory note")
    async def update_memory_note(
        note_id: str,
        request: MemoryUpdateRequest,
        current_user: User = Depends(get_current_active_user)
    ):
        """Update an existing persistent LLM memory note's key/content."""
        return controller.update_memory_note(note_id, request, current_user)

    @router.delete("/memory/{note_id}", response_model=APIResponse, summary="Delete an LLM memory note")
    async def delete_memory_note(
        note_id: str,
        current_user: User = Depends(get_current_active_user)
    ):
        """Delete a persistent LLM memory note."""
        return controller.delete_memory_note(note_id, current_user)

    # --- Admin session-debug viewer ---

    @router.get("/admin/sessions", response_model=APIResponse, summary="[Admin] List chat sessions across all users")
    async def list_admin_sessions(
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        admin_user: User = Depends(get_current_admin_user)
    ):
        """List chat sessions across all users, for the admin debug viewer."""
        return controller.list_admin_sessions(search, limit, offset)

    @router.get(
        "/admin/sessions/{session_id}",
        response_model=APIResponse,
        summary="[Admin] Get a session's messages and LLM call traces",
    )
    async def get_admin_session_detail(
        session_id: str,
        admin_user: User = Depends(get_current_admin_user)
    ):
        """A session's messages plus every LLM call trace it produced."""
        return controller.get_admin_session_detail(session_id)

    @router.delete("/admin/traces", response_model=APIResponse, summary="[Admin] Clear LLM call traces")
    async def clear_traces(
        session_id: Optional[str] = None,
        admin_user: User = Depends(get_current_admin_user)
    ):
        """Delete LLM call traces for one session, or every session when omitted."""
        return controller.clear_traces(session_id)

    return router
