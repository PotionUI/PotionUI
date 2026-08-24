"""Atomic instance-claim tests against the real (file-backed) SQLite wrapper.

The single-admin guarantee comes from a DB constraint written in the same
transaction as the first user, so these tests exercise the production
`Database` connection path (a fresh connection per cursor, WAL, busy_timeout) on
a real file - not the shared in-memory connection the generic `test_db` fixture
uses - and include a genuine threaded race.
"""

import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.platform.database.database import db as global_db
from src.platform.database.migration_runner import MigrationManager
from src.platform.security.user import AccountType
from src.features.users.repository import UserRepository
from src.features.setup.repository import InstanceClaimRepository


def _load_migration_089():
    path = (
        Path(__file__).resolve().parents[3]
        / "src/platform/database/migrations/089_add_instance_claim.py"
    )
    spec = importlib.util.spec_from_file_location("migration_089", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def file_db(tmp_path):
    """Point the global DB singleton at a fresh temp file and migrate it.

    Every repository shares this one `db` object, so redirecting its `db_path`
    redirects them all. The original path is restored on teardown.
    """
    original_path = global_db.db_path
    global_db.db_path = tmp_path / "instance_claim.db"
    try:
        MigrationManager().run_migrations()
        yield global_db
    finally:
        global_db.db_path = original_path


def _register(users, n):
    """Create one user via the atomic claim path with unique credentials."""
    return users.create_claiming_instance(
        username=f"user{n}",
        email=f"user{n}@example.com",
        password_hash="$2b$12$fakehashfakehashfakehashfake",
    )


def test_first_registration_becomes_owner_admin(file_db):
    users = UserRepository()
    claim = InstanceClaimRepository()

    assert claim.is_claimed() is False

    user, became_owner = _register(users, 1)

    assert became_owner is True
    assert user.account_type == AccountType.ADMIN
    assert claim.is_claimed() is True
    assert claim.owner_user_id() == user.id


def test_second_registration_is_regular_user(file_db):
    users = UserRepository()
    claim = InstanceClaimRepository()

    owner, _ = _register(users, 1)
    second, became_owner = _register(users, 2)

    assert became_owner is False
    assert second.account_type == AccountType.USER
    # The claim still points at the original owner, untouched.
    assert claim.owner_user_id() == owner.id


def test_concurrent_registration_yields_exactly_one_admin(file_db):
    """The core race: many simultaneous first-registrations, one admin only."""
    users = UserRepository()
    n_threads = 12
    start = threading.Barrier(n_threads)
    results = []
    lock = threading.Lock()

    def worker(n):
        # Release all threads at once so their claim inserts truly contend.
        start.wait()
        user, became_owner = _register(users, n)
        with lock:
            results.append((user.account_type, became_owner))

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        list(pool.map(worker, range(n_threads)))

    owners = [r for r in results if r[1] is True]
    admins = [r for r in results if r[0] == AccountType.ADMIN]

    assert len(results) == n_threads          # every registration completed
    assert len(owners) == 1                   # exactly one won the claim
    assert len(admins) == 1                   # exactly one admin account
    assert owners[0][0] == AccountType.ADMIN  # the winner is that admin

    # And the database agrees: one sentinel row, one admin user.
    with global_db.get_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM instance_claim")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT COUNT(*) FROM users WHERE account_type = 'ADMIN'")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT COUNT(*) FROM users")
        assert cursor.fetchone()[0] == n_threads


def test_owner_joins_all_users_and_all_admins_groups(file_db):
    """The claiming owner is joined to both built-in groups on
    the same transaction as the claim - see `UserRepository.
    create_claiming_instance`'s `_join_builtin_groups` call and migration
    095_seed_default_user_groups.py, which seeds both groups' rows before any
    registration can happen."""
    from src.features.user_groups.constants import ALL_ADMINS_GROUP_ID, ALL_USERS_GROUP_ID
    from src.features.user_groups.repository import UserGroupRepository

    users = UserRepository()
    groups = UserGroupRepository()

    owner, became_owner = _register(users, 1)
    assert became_owner is True

    owner_group_ids = {g.id for g in groups.get_user_groups(owner.id)}
    assert owner_group_ids == {ALL_USERS_GROUP_ID, ALL_ADMINS_GROUP_ID}


def test_subsequent_registration_joins_all_users_only(file_db):
    """Every registration path joins ALL_USERS - see `UserRepository.
    _insert_user`'s `_join_builtin_groups` call, which runs unconditionally
    for every user row, not just the claiming owner. Only the owner also
    joins ALL_ADMINS."""
    from src.features.user_groups.constants import ALL_USERS_GROUP_ID
    from src.features.user_groups.repository import UserGroupRepository

    users = UserRepository()
    groups = UserGroupRepository()

    _register(users, 1)
    second, became_owner = _register(users, 2)
    assert became_owner is False

    second_group_ids = {g.id for g in groups.get_user_groups(second.id)}
    assert second_group_ids == {ALL_USERS_GROUP_ID}


def test_check_connection_succeeds_against_a_reachable_db(file_db):
    """`check_connection` is `ReadinessManager`'s service-facet probe; it must
    not raise against a real, migrated database."""
    InstanceClaimRepository().check_connection()


def test_regular_create_does_not_claim(file_db):
    """The plain create() path (admin-provisioned users) never claims."""
    users = UserRepository()
    claim = InstanceClaimRepository()

    users.create(
        username="plain", email="plain@example.com",
        password_hash="$2b$12$fakehashfakehashfakehashfake",
        account_type=AccountType.USER,
    )
    assert claim.is_claimed() is False


def test_migration_backfills_existing_admin_as_owner(file_db):
    """An install that already has users is treated as claimed on upgrade.

    Simulates the pre-089 world: existing users, empty claim sentinel. Re-running
    the migration must backfill the earliest admin as the owner so registration
    does not silently reopen.
    """
    users = UserRepository()
    claim = InstanceClaimRepository()

    # Existing users, none of which claimed (created via the plain path).
    admin = users.create(
        username="oldadmin", email="oldadmin@example.com",
        password_hash="$2b$12$fakehashfakehashfakehashfake",
        account_type=AccountType.ADMIN,
    )
    users.create(
        username="olduser", email="olduser@example.com",
        password_hash="$2b$12$fakehashfakehashfakehashfake",
        account_type=AccountType.USER,
    )
    assert claim.is_claimed() is False

    _load_migration_089().up()  # idempotent re-run performs the backfill

    assert claim.is_claimed() is True
    assert claim.owner_user_id() == admin.id
