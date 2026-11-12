from typing import Any, Dict

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.domain_support import (
    DomainTool,
    acting_user,
    bounded_int,
    failure,
    payload,
    required_text,
    run_controller,
)

NOTIFICATIONS_DEFAULT = 20
NOTIFICATIONS_MAX = 100


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


ACCOUNT_TOOLS = (ListNotificationsTool, MarkNotificationsReadTool)
