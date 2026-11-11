from typing import Any

from src.features.presets.exceptions import PresetNotFoundException
from src.platform.security.user import owner_is_admin


def is_admin_user(user: Any) -> bool:
    return owner_is_admin(user)


def preset_available_to_user(db_repo: Any, user_id: str, preset_id: str, is_admin: bool) -> bool:
    if is_admin:
        return True
    return preset_id in (db_repo.get_available_preset_ids_for_user(user_id) or [])


def require_preset_access(db_repo: Any, user_id: str, preset_id: str, is_admin: bool) -> None:
    if not preset_available_to_user(db_repo, user_id, preset_id, is_admin):
        raise PresetNotFoundException(preset_id)
