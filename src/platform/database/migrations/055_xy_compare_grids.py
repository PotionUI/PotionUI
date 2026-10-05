from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

_NAME = "055_xy_compare_grids"

_CONFIRM_ABOVE_KEY = "compare_confirm_above"

_CREATE_GRIDS = (
    "CREATE TABLE IF NOT EXISTS generation_grids ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL, "
    "preset_id TEXT, "
    "tab_id TEXT, "
    "x_axis TEXT NOT NULL, "
    "y_axis TEXT, "
    "lock_seed INTEGER NOT NULL DEFAULT 1, "
    "base_request TEXT NOT NULL, "
    "seeds TEXT NOT NULL DEFAULT '{}', "
    "created_at TEXT NOT NULL, "
    "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE)"
)

_GENERATION_COLUMNS = (
    ("grid_id", "TEXT"),
    ("grid_x", "INTEGER"),
    ("grid_y", "INTEGER"),
    ("axis_values", "TEXT"),
)

_INDEXES = (
    ("idx_generations_grid", "generations (grid_id, grid_y, grid_x)"),
    ("idx_generation_grids_user_created", "generation_grids (user_id, created_at)"),
)


def _column_names(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _seed_setting(cursor, now):
    cursor.execute("SELECT 1 FROM settings WHERE key = ?", (_CONFIRM_ABOVE_KEY,))
    if cursor.fetchone() is not None:
        return
    cursor.execute(
        "INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at) "
        "VALUES (?, ?, '24', 'integer', ?, 'SYSTEM', ?, ?)",
        (
            generate_ulid(),
            _CONFIRM_ABOVE_KEY,
            "Compare grids with more cells than this ask the user to confirm before running.",
            now,
            now,
        ),
    )


def up():
    with db.get_cursor() as cursor:
        now = now_iso()
        cursor.execute(_CREATE_GRIDS)
        existing = _column_names(cursor, "generations")
        for name, column_type in _GENERATION_COLUMNS:
            if name not in existing:
                cursor.execute(f"ALTER TABLE generations ADD COLUMN {name} {column_type}")
        for name, target in _INDEXES:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
        _seed_setting(cursor, now)
    print(f"Migration {_NAME}: compare grids installed")


def down():
    with db.get_cursor() as cursor:
        for name, _target in _INDEXES:
            cursor.execute(f"DROP INDEX IF EXISTS {name}")
        existing = _column_names(cursor, "generations")
        for name, _type in _GENERATION_COLUMNS:
            if name in existing:
                cursor.execute(f"ALTER TABLE generations DROP COLUMN {name}")
        cursor.execute("DROP TABLE IF EXISTS generation_grids")
        cursor.execute("DELETE FROM settings WHERE key = ?", (_CONFIRM_ABOVE_KEY,))
    print(f"Migration {_NAME}: compare grids removed")
