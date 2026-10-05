from datetime import datetime, timedelta, timezone

import pytest

from src.features.plans.components import build_plans
from src.features.plans.records import PlanLimit
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.plugins.limit_kinds import LimitKindRegistry
from src.platform.security.user import AccountType, User
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid

GB = 1024 ** 3


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now = self.now + timedelta(**delta)


class Seeder:
    def __init__(self, database):
        self.db = database

    def _exec(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)

    def rows(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def user(self, user_id, admin=False):
        self._exec(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', ?)",
            (user_id, user_id, f"{user_id}@example.test", "ADMIN" if admin else "USER"),
        )
        self.join(ALL_USERS_GROUP_ID, user_id)
        return User(username=user_id, email=f"{user_id}@example.test", password_hash="x",
                    account_type=AccountType.ADMIN if admin else AccountType.USER, id=user_id)

    def group(self, name):
        group_id = generate_ulid()
        self._exec("INSERT INTO user_groups (id, name, is_system) VALUES (?, ?, 0)", (group_id, name))
        return group_id

    def join(self, group_id, user_id):
        self._exec(
            "INSERT OR IGNORE INTO user_group_members (id, group_id, user_id) VALUES (?, ?, ?)",
            (generate_ulid(), group_id, user_id),
        )

    def generation(self, user_id, sizes=(), file_type="IMAGE"):
        generation_id = generate_ulid()
        self._exec(
            "INSERT INTO generations (id, preset_id, form_data, user_id, status, mode) VALUES (?, 'p', '{}', ?, 'completed', 'txt2img')",
            (generation_id, user_id),
        )
        for size in sizes:
            file_id = generate_ulid()
            self._exec(
                "INSERT INTO files (id, file_path, file_type, user_id, is_final, file_size, thumbnail_small) "
                "VALUES (?, ?, ?, ?, 1, ?, 'thumb.webp')",
                (file_id, f"{file_id}.png", file_type, user_id, size),
            )
            self._exec(
                "INSERT INTO generation_files (id, generation_id, file_id) VALUES (?, ?, ?)",
                (generate_ulid(), generation_id, file_id),
            )
        return generation_id

    def delete_generation(self, generation_id):
        files = self.rows("SELECT file_id FROM generation_files WHERE generation_id = ?", (generation_id,))
        self._exec("DELETE FROM generation_files WHERE generation_id = ?", (generation_id,))
        for row in files:
            self._exec("DELETE FROM files WHERE id = ?", (row["file_id"],))
        self._exec("DELETE FROM generations WHERE id = ?", (generation_id,))

    def upload(self, user_id, size, purpose="user_upload"):
        upload_id = generate_ulid()
        self._exec(
            "INSERT INTO uploads (id, user_id, filename, original_filename, media_type, purpose, file_size) "
            "VALUES (?, ?, ?, 'a.png', 'image', ?, ?)",
            (upload_id, user_id, f"{upload_id}.png", purpose, size),
        )
        return upload_id

    def cost(self, user_id, amount, at, generation_id=None):
        self._exec(
            "INSERT INTO generation_costs (id, generation_id, backend_id, model_id, user_id, amount_usd, source, detail, created_at) "
            "VALUES (?, ?, 'cloud', 'm', ?, ?, 'provider', '{}', ?)",
            (generate_ulid(), generation_id or generate_ulid(), user_id, str(amount), at.isoformat()),
        )


@pytest.fixture
def seed(mock_db):
    return Seeder(mock_db)


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def kinds():
    return LimitKindRegistry()


@pytest.fixture
def settings(mock_db):
    return Settings(SettingRepository())


@pytest.fixture
def hooks():
    return []


@pytest.fixture
def plans(mock_db, kinds, settings, clock, hooks):
    components = build_plans(kinds, settings, clock=clock)
    runner = lambda name, data: hooks.append((name, data))
    components.guard.hook_runner = runner
    components.manager.hook_runner = runner
    return components


@pytest.fixture
def guard(plans):
    return plans.guard


@pytest.fixture
def manager(plans):
    return plans.manager


def make_plan(plans, name, **limits):
    return plans.guard.plans.create(name, None, tuple(PlanLimit(kind, value) for kind, value in limits.items()))
