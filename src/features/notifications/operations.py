"""
Notification domain operations.

Persistence, fan-out, and real-time push for user notifications. Framework-
agnostic - uses ValueError for errors (the controller converts that to HTTP
responses). WebSocket pushes are best-effort: failures are logged, never
raised, so a dropped connection can't break a `notify()` call or a generation
completion.

`notify` is the single entry point producers (generation lifecycle, plugins,
the REST API, automation) use to raise a notification - persisted per-user
(fanned out to every user for a broadcast) or transient (toast-only, never
persisted). Widely fanned out to unrelated features as a bound callable (see
`src.bootstrap.container` - `functools.partial(operations.notify,
notification_collaborators)`), so its signature is the stable contract, not
this module or `NotificationCollaborators`.
"""
import json
import logging
from typing import List, Optional, Dict, Any

from src.features.notifications.collaborators import NotificationCollaborators
from src.features.notifications.records import Notification, NotificationLevel
from src.platform.plugins.hooks import execute_hook
from src.features.notifications.hooks import NOTIFICATION_HOOKS
from src.features.notifications.types import notification_type_registry
from src.platform.security.user import AccountType

logger = logging.getLogger(__name__)

DEFAULT_PRUNE_KEEP = 200
PREFERENCES_SETTING_KEY = "notification_preferences"


def _safe_send(collaborators: NotificationCollaborators, user_id: Optional[str], message: dict) -> None:
    """Push a WS message, swallowing any failure so callers never break."""
    try:
        collaborators.connections.schedule_send(user_id, message)
    except Exception as e:
        logger.error(f"Failed to schedule notification WS send: {e}")


def _get_preferences_raw(collaborators: NotificationCollaborators, user_id: str) -> Dict[str, Any]:
    """Fetch the stored preferences dict for a user, tolerating a raw JSON string or missing setting."""
    raw = collaborators.settings.get_setting(PREFERENCES_SETTING_KEY, {}, user_id=user_id)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _is_admin(collaborators: NotificationCollaborators, user_id: str) -> bool:
    """Whether `user_id` currently holds the ADMIN account type."""
    user = collaborators.users.get_by_id(user_id)
    return user is not None and user.account_type == AccountType.ADMIN


def is_type_enabled(collaborators: NotificationCollaborators, user_id: str, type: str) -> bool:
    """
    Whether a notification `type` should be delivered to `user_id`.

    Empty or unregistered types are always enabled. An `admin_only` type
    is never enabled for a non-admin, regardless of preference overrides.
    Otherwise, an explicit per-user preference wins; absent that, the
    type's `default_enabled` applies.
    """
    if not type:
        return True

    spec = notification_type_registry.get(type)
    if spec is not None and spec.admin_only and not _is_admin(collaborators, user_id):
        return False

    raw = _get_preferences_raw(collaborators, user_id)
    types = raw.get("types") if isinstance(raw.get("types"), dict) else {}

    if type in types:
        return bool(types[type])

    return spec.default_enabled if spec is not None else True


def notify(
    collaborators: NotificationCollaborators,
    *,
    level: str,
    title: str,
    message: str = "",
    category: str = "system",
    user_id: Optional[str] = None,
    source: str = "core",
    transient: bool = False,
    show_toast: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
    type: str = ""
) -> List[Notification]:
    """
    Raise a notification.

    Args:
        collaborators: NotificationCollaborators
        level: 'success' | 'error' | 'info' | 'warning'
        title: Notification title
        message: Notification body
        category: 'generation' | 'system' | 'plugin' | free-form
        user_id: Target user, or None to broadcast to all users
        source: 'core' | 'frontend' | plugin id
        transient: If True, skip persistence entirely (toast-only push)
        show_toast: Whether clients should also surface this as a toast
        metadata: Optional JSON-serializable extra data
        type: Notification type key (see `src.features.notifications.types`);
            empty or unregistered types are always delivered. A concrete,
            registered type is checked against each target's preferences
            - if disabled for that user, the notification is skipped
            entirely for them (no row, no WS push, no after_create hook).
            A broadcast transient (user_id=None) bypasses this filter,
            except for an `admin_only` type, which is fanned out to
            admins only rather than broadcast to every connection.

    Raises:
        ValueError: If `level` is not a valid NotificationLevel

    Returns:
        List of persisted Notification rows (empty for
        transient/blocked/filtered-out)
    """
    # Validate level up front (raises ValueError for an invalid value)
    NotificationLevel(level)

    hook_data, blocked = execute_hook(collaborators.plugins,
        NOTIFICATION_HOOKS.before_create,
        {
            "level": level,
            "title": title,
            "message": message,
            "category": category,
            "user_id": user_id,
            "source": source,
        }
    )

    if blocked:
        reason = hook_data.get("block_reason", "Notification blocked")
        logger.info(f"Notification blocked by plugin: {reason}")
        return []

    level = hook_data.get("level", level)
    title = hook_data.get("title", title)
    message = hook_data.get("message", message)

    if transient:
        toast = {
            "type": "toast",
            "level": level,
            "title": title,
            "message": message,
            "category": category,
        }
        if user_id is not None:
            if not is_type_enabled(collaborators, user_id, type):
                return []
            _safe_send(collaborators, user_id, toast)
            return []

        spec = notification_type_registry.get(type)
        if spec is not None and spec.admin_only:
            # An admin_only type has no "everyone" audience to broadcast
            # to - fan out to admins individually instead of the normal
            # user_id=None all-connections broadcast.
            for admin in collaborators.users.get_all():
                if admin.account_type == AccountType.ADMIN and is_type_enabled(collaborators, admin.id, type):
                    _safe_send(collaborators, admin.id, toast)
        else:
            _safe_send(collaborators, user_id, toast)
        return []

    targets = [user_id] if user_id is not None else [u.id for u in collaborators.users.get_all()]

    created: List[Notification] = []
    for target in targets:
        if not is_type_enabled(collaborators, target, type):
            continue

        notification = collaborators.repository.create(
            user_id=target,
            category=category,
            level=level,
            title=title,
            message=message,
            metadata=metadata,
            source=source,
            type=type,
        )
        created.append(notification)

        try:
            collaborators.repository.prune(target, keep=DEFAULT_PRUNE_KEEP)
        except Exception as e:
            logger.error(f"Failed to prune notifications for user {target}: {e}")

        _safe_send(collaborators, target, {
            "type": "notification",
            "notification": notification.model_dump(mode="json"),
            "show_toast": show_toast,
        })

        execute_hook(collaborators.plugins,
            NOTIFICATION_HOOKS.after_create,
            {
                "notification_id": notification.id,
                "user_id": target,
                "category": category,
                "level": level,
            }
        )

    return created


# ========== Preferences ==========

def get_preferences(collaborators: NotificationCollaborators, user_id: str) -> Dict[str, Any]:
    """
    Return the user-effective preferences: every registered type
    visible to `user_id` with its resolved `enabled` state, plus the
    global sound toggle. `admin_only` types are omitted for non-admins.
    """
    raw = _get_preferences_raw(collaborators, user_id)
    user_types = raw.get("types") if isinstance(raw.get("types"), dict) else {}
    sound = bool(raw.get("sound", False))
    is_admin = _is_admin(collaborators, user_id)

    types = []
    for spec in sorted(notification_type_registry.all(), key=lambda s: (s.category, s.label)):
        if spec.admin_only and not is_admin:
            continue
        enabled = bool(user_types[spec.key]) if spec.key in user_types else spec.default_enabled
        types.append({
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "category": spec.category,
            "default_enabled": spec.default_enabled,
            "enabled": enabled,
        })

    return {"types": types, "sound": sound}


def update_preferences(
    collaborators: NotificationCollaborators,
    user_id: str,
    types: Optional[Dict[str, bool]] = None,
    sound: Optional[bool] = None
) -> Dict[str, Any]:
    """
    Partially merge `types`/`sound` into the user's stored preferences.

    Raises:
        ValueError: If `types` references a key not in the notification
            type registry.

    Returns:
        The fresh `get_preferences(collaborators, user_id)` shape.
    """
    raw = _get_preferences_raw(collaborators, user_id)
    stored_types = dict(raw.get("types")) if isinstance(raw.get("types"), dict) else {}
    stored_sound = bool(raw.get("sound", False))

    if types is not None:
        for key, value in types.items():
            if not notification_type_registry.has(key):
                raise ValueError(f"Unknown notification type: '{key}'")
            stored_types[key] = bool(value)

    if sound is not None:
        stored_sound = bool(sound)

    collaborators.settings.set_setting(
        PREFERENCES_SETTING_KEY,
        {"types": stored_types, "sound": stored_sound},
        user_id=user_id
    )

    return get_preferences(collaborators, user_id)


# ========== Mutations ==========

def mark_read(collaborators: NotificationCollaborators, notification_id: str, user_id: str) -> bool:
    """Mark a single notification read; emits a sync event to the user's tabs."""
    success = collaborators.repository.mark_read(notification_id, user_id)
    if success:
        _safe_send(collaborators, user_id, {"type": "notification_read", "id": notification_id})
    return success


def mark_all_read(collaborators: NotificationCollaborators, user_id: str) -> int:
    """Mark all of a user's notifications read; emits a sync event."""
    updated = collaborators.repository.mark_all_read(user_id)
    _safe_send(collaborators, user_id, {"type": "all_read"})
    return updated


def delete(collaborators: NotificationCollaborators, notification_id: str, user_id: str) -> bool:
    """Delete a single notification; emits a sync event."""
    success = collaborators.repository.delete(notification_id, user_id)
    if success:
        _safe_send(collaborators, user_id, {"type": "notification_deleted", "id": notification_id})
    return success


def clear(collaborators: NotificationCollaborators, user_id: str) -> int:
    """Delete all of a user's notifications; emits a sync event."""
    deleted = collaborators.repository.delete_all(user_id)
    _safe_send(collaborators, user_id, {"type": "notifications_cleared"})
    return deleted
