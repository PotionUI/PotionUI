from fastapi import HTTPException

from src.features.plugins.operations import role_gate_open
from src.features.plugins.repository import PluginRepository
from src.platform.security.user import User

PLUGIN_ID = "system-monitor"
ROLE_SETTING = "visible_to"
ADMIN_ROLE = "ADMIN"


def monitor_visible_to(repo: PluginRepository, user: User) -> bool:
    if user.account_type.value == ADMIN_ROLE:
        return True
    plugin = repo.get_plugin_by_id(PLUGIN_ID)
    if plugin is None or not plugin.enabled:
        return False
    return role_gate_open(repo, PLUGIN_ID, ADMIN_ROLE, ROLE_SETTING, user.account_type.value)


def require_monitor_access(repo: PluginRepository, user: User) -> User:
    if not monitor_visible_to(repo, user):
        raise HTTPException(status_code=403, detail="System monitor is restricted to administrators")
    return user
