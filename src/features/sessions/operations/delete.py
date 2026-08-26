"""
Delete a session.

Module-level function, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for "not
found"/"blocked" (the controller converts that to an HTTP response).
"""
import logging

from src.features.sessions.repository import SessionRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.sessions.hooks import SESSION_HOOKS

logger = logging.getLogger(__name__)


def delete_session(session_repository: SessionRepository, plugin_registry: PluginRegistry, user_id: str, session_id: str) -> str:
    """Delete a session.

    Executes hooks:
    - session.before_delete: Can block deletion
    - session.after_delete: Notification of successful deletion

    Raises:
        ValueError: If session not found, access denied, or deletion blocked
    """
    session = session_repository.get_by_id(session_id)

    if not session:
        raise ValueError("Session not found")

    if session.user_id != user_id:
        raise ValueError("Access denied to this session")

    hook_data, blocked = execute_hook(
        plugin_registry,
        SESSION_HOOKS.before_delete,
        {
            "session_id": session_id,
            "preset_id": session.preset_id,
            "name": session.name,
            "user_id": user_id,
        },
    )

    if blocked:
        reason = hook_data.get("block_reason", "Session deletion blocked")
        logger.warning(f"Session deletion blocked by plugin: {reason}")
        raise ValueError(reason)

    session_name = session.name
    success = session_repository.delete(session_id)

    if not success:
        raise ValueError("Failed to delete session")

    execute_hook(
        plugin_registry,
        SESSION_HOOKS.after_delete,
        {
            "session_id": session_id,
            "preset_id": session.preset_id,
            "name": session_name,
            "user_id": user_id,
        },
    )

    logger.info(f"Session deleted: {session_name} (id: {session_id})")
    return f"Session '{session_name}' deleted successfully"
