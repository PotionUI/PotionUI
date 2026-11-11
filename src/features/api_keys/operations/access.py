from datetime import datetime, timezone

from src.features.api_keys.errors import ApiKeyError
from src.features.api_keys.operations.expiry import stamp
from src.features.api_keys.records import UserApiAccess
from src.features.api_keys.repository import UserApiAccessRepository


def get_user_access(access: UserApiAccessRepository, users, user_id: str) -> UserApiAccess:
    if users.get_by_id(user_id) is None:
        raise ApiKeyError("user_not_found", "User not found", 404)
    return access.get(user_id)


def set_user_access(
    access: UserApiAccessRepository, users, admin_id: str, user_id: str, keys_enabled: bool, mcp_enabled: bool
) -> UserApiAccess:
    if users.get_by_id(user_id) is None:
        raise ApiKeyError("user_not_found", "User not found", 404)
    updated = UserApiAccess(user_id=user_id, keys_enabled=keys_enabled, mcp_enabled=mcp_enabled)
    access.save(updated, stamp(datetime.now(timezone.utc)), admin_id)
    return updated
