from src.platform.database.database import db

_NAME = "097_kits"

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS kits ("
    "id TEXT PRIMARY KEY, "
    "owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "name TEXT NOT NULL, "
    "handle TEXT NOT NULL, "
    "label TEXT, "
    "position INTEGER NOT NULL DEFAULT 0, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "UNIQUE (owner_id, handle))",
    "CREATE INDEX IF NOT EXISTS idx_kits_owner ON kits (owner_id, position)",
    "CREATE TABLE IF NOT EXISTS kit_items ("
    "id TEXT PRIMARY KEY, "
    "kit_id TEXT NOT NULL REFERENCES kits(id) ON DELETE CASCADE, "
    "kind TEXT NOT NULL CHECK (kind IN ('image', 'video', 'audio', 'text')), "
    "name TEXT NOT NULL, "
    "position INTEGER NOT NULL DEFAULT 0, "
    "media_ref TEXT REFERENCES uploads(id) ON DELETE SET NULL, "
    "text TEXT, "
    "created_at TEXT NOT NULL, "
    "UNIQUE (kit_id, name))",
    "CREATE INDEX IF NOT EXISTS idx_kit_items_kit ON kit_items (kit_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_kit_items_media ON kit_items (media_ref)",
    "CREATE TABLE IF NOT EXISTS kit_handle_aliases ("
    "owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "handle TEXT NOT NULL, "
    "kit_id TEXT NOT NULL REFERENCES kits(id) ON DELETE CASCADE, "
    "PRIMARY KEY (owner_id, handle))",
    "CREATE INDEX IF NOT EXISTS idx_kit_handle_aliases_kit ON kit_handle_aliases (kit_id)",
)


def up():
    with db.get_cursor() as cursor:
        for statement in _STATEMENTS:
            cursor.execute(statement)
    print(f"Migration {_NAME}: kits, kit items and kit path aliases ready")
