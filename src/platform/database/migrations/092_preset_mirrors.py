from src.platform.database.database import db

_NAME = "092_preset_mirrors"

_COLUMNS = (
    ("source_preset_id", "TEXT REFERENCES presets(preset_id) ON DELETE RESTRICT"),
    ("display_name", "TEXT"),
    ("display_description", "TEXT"),
    ("display_cover", "TEXT"),
    ("offered_modes", "TEXT"),
    ("first_mode", "TEXT"),
)

_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_presets_source ON presets (source_preset_id)",
    "CREATE TABLE IF NOT EXISTS preset_tombstones ("
    "preset_id TEXT PRIMARY KEY, "
    "name TEXT NOT NULL, "
    "source_preset_id TEXT, "
    "deleted_at TEXT NOT NULL)",
)


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(presets)")
        existing = {row[1] for row in cursor.fetchall()}
        added = [name for name, _ in _COLUMNS if name not in existing]
        for name, definition in _COLUMNS:
            if name not in existing:
                cursor.execute(f"ALTER TABLE presets ADD COLUMN {name} {definition}")
        for statement in _STATEMENTS:
            cursor.execute(statement)
    print(f"Migration {_NAME}: {len(added)} preset columns added, tombstone table ready")
