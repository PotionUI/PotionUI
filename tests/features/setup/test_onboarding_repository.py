"""Per-user onboarding state against the real file-backed SQLite wrapper.

Mirrors `test_instance_claim`'s `file_db` pattern: point the global DB
singleton at a temp file, migrate it, and exercise the production connection
path.
"""

import pytest

from src.platform.database.database import db as global_db
from src.platform.database.migration_runner import MigrationRunner
from src.platform.security.user import AccountType
from src.features.setup.onboarding_repository import OnboardingRepository
from src.features.setup.records import OnboardingStatus
from src.features.users.repository import UserRepository


@pytest.fixture
def file_db(tmp_path):
    """Redirect the shared DB singleton at a fresh migrated temp file."""
    original_path = global_db.db_path
    global_db.db_path = tmp_path / "onboarding.db"
    try:
        MigrationRunner().run_migrations()
        yield global_db
    finally:
        global_db.db_path = original_path


def test_onboarding_state_upsert(file_db):
    users = UserRepository()
    user = users.create(
        username="owner", email="owner@example.com",
        password_hash="$2b$12$fakehashfakehashfakehashfake",
        account_type=AccountType.ADMIN,
    )
    repo = OnboardingRepository()

    assert repo.get_onboarding_state(user.id) is None
    state = repo.upsert_onboarding_state(user.id, status=OnboardingStatus.PENDING)
    assert state.status == OnboardingStatus.PENDING

    updated = repo.upsert_onboarding_state(
        user.id, status=OnboardingStatus.COMPLETED, first_generation_id="gen-1"
    )
    assert updated.status == OnboardingStatus.COMPLETED
    assert updated.first_generation_id == "gen-1"
    assert updated.completed_at is not None
