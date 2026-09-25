import json
import logging
from typing import Any, Callable, List

from src.platform.plugins.hooks import HookContext
from src.platform.security.user import AccountType

logger = logging.getLogger(__name__)

ENABLED_SETTING = "notify_admins_on_generation_failure"
CATEGORIES_SETTING = "notify_admins_on_generation_failure_categories"
NOTIFICATION_TYPE = "generation.failed_admin_alert"
HANDLER_ID = "core.admin_failure_alerts"


def failure_link(generation_id: str) -> str:
    return f"/admin?tab=generations&id={generation_id}"


def _category_filter(raw: Any) -> List[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError:
            raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


class GenerationFailureAdminAlerts:

    def __init__(self, settings, users, notify: Callable[..., Any]):
        self._settings = settings
        self._users = users
        self._notify = notify

    def __call__(self, context: HookContext) -> HookContext:
        try:
            self.handle(context.data)
        except Exception:
            logger.error("Failed to raise admin generation-failure alerts", exc_info=True)
        return context

    def handle(self, payload: dict) -> int:
        if not self._settings.get_setting(ENABLED_SETTING, False):
            return 0
        category = payload.get("category") or payload.get("error_code") or ""
        categories = _category_filter(self._settings.get_setting(CATEGORIES_SETTING, []))
        if categories and category not in categories:
            return 0
        generation_id = payload.get("generation_id") or ""
        message = f"Generation failed: {payload.get('message') or 'unknown error'}"
        metadata = {
            "generation_id": generation_id,
            "error_id": generation_id,
            "error_code": payload.get("error_code"),
            "preset_id": payload.get("preset_id"),
            "user_id": payload.get("user_id"),
            "failed_pipe": payload.get("failed_pipe"),
            "link": failure_link(generation_id),
            "link_label": "View failure",
        }
        sent = 0
        for user in self._users.get_all():
            if user.account_type != AccountType.ADMIN:
                continue
            self._notify(
                level="error",
                title="Generation failed",
                message=message,
                category="generation",
                type=NOTIFICATION_TYPE,
                user_id=user.id,
                metadata=metadata,
                show_toast=True,
            )
            sent += 1
        return sent
