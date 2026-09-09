"""Onboarding row records.

The recipe-run lifecycle (statuses, legal transitions, run/attempt rows) lives
with the feature that owns it, `src.features.recipes.records`. What stays here
is the per-user onboarding progress the first-run wizard writes when a recipe
finishes.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from src.platform.database.rows import dt_column


class OnboardingStatus(str, Enum):
    """Per-user onboarding progress."""

    PENDING = "pending"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


@dataclass
class UserOnboardingState:
    """A row of ``user_onboarding_state``."""

    user_id: str
    version: int
    status: OnboardingStatus
    dismissed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    first_generation_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "UserOnboardingState":
        return cls(
            user_id=row["user_id"],
            version=row["version"],
            status=OnboardingStatus(row["status"]),
            dismissed_at=dt_column(row["dismissed_at"]),
            completed_at=dt_column(row["completed_at"]),
            first_generation_id=row["first_generation_id"],
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
        )
