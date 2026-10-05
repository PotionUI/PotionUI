from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

_NAME = "053_plans"

_UNLIMITED_PLAN_ID = "unlimited"

_SETTINGS = (
    (
        "plans_exempt_admins",
        "true",
        "boolean",
        "Admin accounts are never refused by a plan limit. Their usage is still measured.",
    ),
    (
        "plans_day_timezone",
        "UTC",
        "string",
        "Timezone whose midnight resets daily plan limits; monthly limits reset on the 1st.",
    ),
    (
        "plans_contact_line",
        "Ask your admin for more.",
        "string",
        "Line added to every plan limit refusal.",
    ),
)

_CREATE_PLANS = (
    "CREATE TABLE IF NOT EXISTS plans ("
    "id TEXT PRIMARY KEY, "
    "name TEXT NOT NULL UNIQUE COLLATE NOCASE, "
    "description TEXT, "
    "limits_json TEXT NOT NULL DEFAULT '[]', "
    "is_system INTEGER NOT NULL DEFAULT 0, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)"
)

_CREATE_EVENTS = (
    "CREATE TABLE IF NOT EXISTS limit_events ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL, "
    "kind TEXT NOT NULL, "
    "units REAL NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL, "
    "ref_id TEXT, "
    "refunded_at TEXT, "
    "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE)"
)

_INDEXES = (
    ("idx_limit_events_user_kind_created", "limit_events (user_id, kind, created_at)"),
    ("idx_limit_events_ref", "limit_events (ref_id)"),
    ("idx_limit_events_created", "limit_events (created_at)"),
    ("idx_users_plan", "users (plan_id)"),
    ("idx_user_groups_plan", "user_groups (plan_id)"),
)


def _column_names(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def _add_plan_column(cursor, table):
    if "plan_id" not in _column_names(cursor, table):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN plan_id TEXT")


def _seed_settings(cursor, now):
    for key, value, value_type, description in _SETTINGS:
        cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
        if cursor.fetchone() is not None:
            continue
        cursor.execute(
            "INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'SYSTEM', ?, ?)",
            (generate_ulid(), key, value, value_type, description, now, now),
        )


def up():
    with db.get_cursor() as cursor:
        now = now_iso()
        cursor.execute(_CREATE_PLANS)
        cursor.execute(
            "INSERT OR IGNORE INTO plans (id, name, description, limits_json, is_system, created_at, updated_at) "
            "VALUES (?, 'Unlimited', 'No limits - everything unlimited', '[]', 1, ?, ?)",
            (_UNLIMITED_PLAN_ID, now, now),
        )
        cursor.execute(_CREATE_EVENTS)
        _add_plan_column(cursor, "user_groups")
        _add_plan_column(cursor, "users")
        for name, target in _INDEXES:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
        _seed_settings(cursor, now)
    print(f"Migration {_NAME}: plans, limit ledger and plan settings installed")


def down():
    with db.get_cursor() as cursor:
        for name, _target in _INDEXES:
            cursor.execute(f"DROP INDEX IF EXISTS {name}")
        for table in ("users", "user_groups"):
            if "plan_id" in _column_names(cursor, table):
                cursor.execute(f"ALTER TABLE {table} DROP COLUMN plan_id")
        cursor.execute("DROP TABLE IF EXISTS limit_events")
        cursor.execute("DROP TABLE IF EXISTS plans")
        for key, _, _, _ in _SETTINGS:
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
    print(f"Migration {_NAME}: plans removed")
