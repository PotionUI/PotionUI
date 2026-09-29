import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.features.content_safety.constants import (
    POLICIES,
    POLICY_ALLOWED,
    POLICY_BLOCKED,
    RESTRICTED_GROUP_ID,
    SETTING_POLICY,
    STRICTNESS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EffectivePolicy:
    mode: str
    restricted: bool = False

    @property
    def allowed(self) -> bool:
        return self.mode == POLICY_ALLOWED

    @property
    def blocked(self) -> bool:
        return self.mode == POLICY_BLOCKED


class ContentPolicyResolver:
    def __init__(self, settings: Any, groups: Any):
        self.settings = settings
        self.groups = groups

    def instance_policy(self) -> str:
        value = self.settings.get_setting(SETTING_POLICY, POLICY_ALLOWED)
        if value in POLICIES:
            return value
        logger.warning("Unknown %s value %r; treating as blocked", SETTING_POLICY, value)
        return POLICY_BLOCKED

    def resolve(self, user_id: Optional[str]) -> EffectivePolicy:
        if not user_id:
            return EffectivePolicy(self.instance_policy())
        groups = self.groups.get_user_groups(user_id)
        if any(group.id == RESTRICTED_GROUP_ID for group in groups):
            return EffectivePolicy(POLICY_BLOCKED, restricted=True)
        declared = [group.content_policy for group in groups if group.content_policy in POLICIES]
        if declared:
            return EffectivePolicy(max(declared, key=STRICTNESS.__getitem__))
        return EffectivePolicy(self.instance_policy())

    def allows_nsfw(self, user_id: Optional[str]) -> bool:
        return self.resolve(user_id).allowed
