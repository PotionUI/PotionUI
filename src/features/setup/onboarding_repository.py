"""Persistence for per-user onboarding state (`user_onboarding_state`).

Split from the recipe-run tables it used to share a repository with: a recipe
run is instance-wide machinery (see `src.features.recipes.run_repository`),
while onboarding progress is a per-user fact the first-run wizard owns.
"""

from typing import Any, List, Optional

from src.platform.database.rows import now_iso
from src.features.setup.records import OnboardingStatus, UserOnboardingState


class OnboardingRepository:
    """Reads/writes ``user_onboarding_state``."""

    def get_onboarding_state(self, user_id: str) -> Optional[UserOnboardingState]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_onboarding_state WHERE user_id = ?", (user_id,)
            )
            row = cursor.fetchone()
            return UserOnboardingState.from_row(row) if row else None

    def upsert_onboarding_state(
        self,
        user_id: str,
        *,
        status: Optional[OnboardingStatus] = None,
        first_generation_id: Optional[str] = None,
        dismissed: bool = False,
    ) -> UserOnboardingState:
        existing = self.get_onboarding_state(user_id)
        now = now_iso()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if existing is None:
                cursor.execute(
                    """
                    INSERT INTO user_onboarding_state (
                        user_id, status, first_generation_id,
                        dismissed_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        (status or OnboardingStatus.PENDING).value,
                        first_generation_id,
                        now if dismissed else None,
                        now if status == OnboardingStatus.COMPLETED else None,
                    ),
                )
            else:
                sets = ["updated_at = ?"]
                params: List[Any] = [now]
                if status is not None:
                    sets.append("status = ?")
                    params.append(status.value)
                    if status == OnboardingStatus.COMPLETED:
                        sets.append("completed_at = ?")
                        params.append(now)
                if first_generation_id is not None:
                    sets.append("first_generation_id = ?")
                    params.append(first_generation_id)
                if dismissed:
                    sets.append("dismissed_at = ?")
                    params.append(now)
                params.append(user_id)
                cursor.execute(
                    f"UPDATE user_onboarding_state SET {', '.join(sets)} "
                    "WHERE user_id = ?",
                    params,
                )
        return self.get_onboarding_state(user_id)
