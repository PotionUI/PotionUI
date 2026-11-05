import logging
from typing import Any, List, Optional

from src.features.models.exceptions import (
    InvalidModelTypeException,
    ModelNotFoundException,
    ModelTypeConflictException,
    ModelTypeNotAssignableException,
)
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.user_groups.operations.resources import assign_models
from src.platform.filesystem.model_types import assignable_model_types
from src.platform.plugins.hooks import execute_hook
from src.platform.security.user import AccountType, User

logger = logging.getLogger(__name__)

SYSTEM_USER_ID = "system"

ASSIGNED = "assigned"
PRESENT = "present"
VETOED = "vetoed"

TYPE_SET = "set"
TYPE_SAME = "same"
TYPE_PENDING = "pending"


def system_user() -> User:
    return User(
        username=SYSTEM_USER_ID, email="system@localhost", password_hash="", account_type=AccountType.ADMIN,
        id=SYSTEM_USER_ID,
    )


class InstanceModelOps:

    def __init__(self, groups: Any, plugins: Any, types: Any, items: Any):
        self.groups = groups
        self.plugins = plugins
        self.types = types
        self.items = items

    def group_name(self, group_id: str) -> Optional[str]:
        group = self.groups.get_group_by_id(group_id) if group_id else None
        return group.name if group else None

    def group_names(self, group_ids: List[str]) -> dict:
        names = {}
        for group_id in dict.fromkeys(group_ids):
            name = self.group_name(group_id)
            if name is not None:
                names[group_id] = name
        return names

    def member_count(self, group_id: str) -> int:
        return self.groups.get_group_member_count(group_id)

    def assign_to_group(self, group_id: str, model_id: str) -> str:
        if self.items.members_of("user_group", group_id, [model_id]):
            return PRESENT
        assigned = assign_models(self.groups, self.plugins, group_id, [model_id], system_user())
        return ASSIGNED if assigned else VETOED

    def unassign_from_group(self, group_id: str, model_id: str) -> bool:
        return bool(self.groups.unassign_model_from_group(group_id, model_id))

    def assignable_types(self) -> List[str]:
        return list(assignable_model_types())

    def set_type(self, model_id: str, model_type: str) -> str:
        if self.items.models_of_type([model_id], model_type):
            return TYPE_SAME
        pending = self.types.set_admin_type(model_id, model_type, None)
        return TYPE_PENDING if pending is not None else TYPE_SET

    def reset_type(self, model_id: str) -> None:
        self.types.reset_admin_type(model_id)

    def tags_changed(self, model_id: str, tag_ids: List[str]) -> None:
        try:
            execute_hook(self.plugins, MODEL_INDEX_HOOKS.after_update_tags, {"model_id": model_id, "tag_ids": tag_ids})
        except Exception:
            logger.warning("after_update_tags hook failed for model %s", model_id, exc_info=True)


TYPE_REFUSALS = (ModelTypeConflictException, ModelTypeNotAssignableException, InvalidModelTypeException, ModelNotFoundException)
