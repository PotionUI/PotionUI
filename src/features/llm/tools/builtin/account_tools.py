import asyncio
from typing import Any, Dict, Optional

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.domain_support import (
    DomainTool,
    acting_user,
    bounded_int,
    failure,
    payload,
    required_text,
    run_controller,
    without,
)
from src.features.sessions.mappers import session_to_response_dict

NOTIFICATIONS_DEFAULT = 20
NOTIFICATIONS_MAX = 100


async def owned_session(context: ToolContext, session_id: str) -> Optional[Any]:
    session = await asyncio.to_thread(context.domains.sessions.get_by_id, session_id)
    if session is None or session.user_id != context.user_id:
        return None
    return session


class ListSessionsTool(DomainTool):
    domain = "sessions"
    group = "Form & segments"
    context_group = "library"
    icon = "bookmark"
    name = "list_sessions"
    read_only = True
    user_description = "Lists your saved sessions (saved form states) for a preset."

    @property
    def description(self) -> str:
        return (
            "List the user's saved sessions for one preset: id, name, pinned, version and last change. A "
            "session is a saved Generate form; read one with get_session."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"preset_id": {"type": "string", "description": "A preset id from list_presets."}},
            "required": ["preset_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        preset_id = required_text(kwargs, "preset_id")
        if not preset_id:
            return failure("'preset_id' is required")
        sessions = await asyncio.to_thread(self.service(context).get_by_user_and_preset, context.user_id, preset_id)
        return payload({
            "sessions": [without(session_to_response_dict(session), "data") for session in sessions],
        })


class GetSessionTool(DomainTool):
    domain = "sessions"
    group = "Form & segments"
    context_group = "library"
    icon = "bookmark"
    name = "get_session"
    read_only = True
    user_description = "Reads one of your saved sessions."

    @property
    def description(self) -> str:
        return (
            "Read one of the user's saved sessions by id: its preset, the saved form values per mode and the "
            "modes it holds."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"session_id": {"type": "string", "description": "A session id from list_sessions."}},
            "required": ["session_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        session_id = required_text(kwargs, "session_id")
        if not session_id:
            return failure("'session_id' is required")
        session = await owned_session(context, session_id)
        if session is None:
            return failure("Session not found")
        body = session_to_response_dict(session)
        body["modes"] = [key for key, value in (body.get("data") or {}).items() if isinstance(value, dict)]
        return payload(body)


class ListNotificationsTool(DomainTool):
    domain = "notifications"
    group = "Notifications"
    context_group = "library"
    icon = "bell"
    name = "list_notifications"
    read_only = True
    user_description = "Reads your notifications."

    @property
    def description(self) -> str:
        return (
            "List the user's notifications, newest first, with the unread count. 'unread_only' narrows to "
            f"unread ones; 'limit' defaults to {NOTIFICATIONS_DEFAULT} (at most {NOTIFICATIONS_MAX}); 'before' is "
            "the id of the oldest one already seen, to page further back."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "unread_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": NOTIFICATIONS_MAX, "default": NOTIFICATIONS_DEFAULT},
                "before": {"type": "string"},
            },
            "required": [],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        limit = bounded_int(kwargs, "limit", NOTIFICATIONS_DEFAULT, 1, NOTIFICATIONS_MAX)
        return await run_controller(self.service(context).list_notifications(
            acting_user(context), limit=limit, before=kwargs.get("before") or None,
            unread_only=bool(kwargs.get("unread_only")),
        ))


class MarkNotificationsReadTool(DomainTool):
    domain = "notifications"
    group = "Notifications"
    context_group = "library"
    icon = "bell"
    name = "mark_notifications_read"
    user_description = "Marks one or all of your notifications as read."

    @property
    def description(self) -> str:
        return (
            "Mark one notification read ('notification_id') or every notification read ('all': true). "
            "Nothing is deleted."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"notification_id": {"type": "string"}, "all": {"type": "boolean", "default": False}},
            "required": [],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        controller, user = self.service(context), acting_user(context)
        if kwargs.get("all"):
            return await run_controller(controller.mark_all_read(user))
        notification_id = required_text(kwargs, "notification_id")
        if not notification_id:
            return failure("Give 'notification_id', or 'all': true.")
        return await run_controller(controller.mark_read(notification_id, user))


ACCOUNT_TOOLS = (ListSessionsTool, GetSessionTool, ListNotificationsTool, MarkNotificationsReadTool)
