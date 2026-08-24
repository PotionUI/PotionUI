"""Migration 135: Attributes v2 - DB-backed, UI-managed model attribute
definitions with per-user value overlays.

Supersedes the code-registry design added in migration 133
(`src.platform.plugins.model_metadata_fields.ModelMetadataFieldRegistry`):
attribute definitions (a LoRA's `strength`, trigger words, ...) now live in
`model_attribute_definitions`, editable through the admin UI instead of only
through core/plugin code. `user_model_attributes` holds the per-user overlay
for definitions marked `per_user`.

Trigger words migrate into this system: `models.triggers` (a JSON array) is
copied into `models.model_metadata` under the `'triggers'` key (merged with
whatever is already there), then the `triggers` column is dropped. SQLite
3.35+ supports `ALTER TABLE ... DROP COLUMN` directly - no table rebuild
needed.
"""

import json

from src.platform.database.database import db


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return column in [col[1] for col in cursor.fetchall()]


def _migrate_triggers_into_metadata(cursor) -> int:
    """Copy each model's `triggers` JSON array into `model_metadata['triggers']`.

    A model that already has a `model_metadata['triggers']` key (re-running the
    migration, or a value set some other way) is left untouched - this only
    backfills, it never overwrites.
    """
    cursor.execute(
        "SELECT id, triggers, model_metadata FROM models "
        "WHERE triggers IS NOT NULL AND triggers != '' AND triggers != '[]'"
    )
    rows = cursor.fetchall()

    migrated = 0
    for row in rows:
        triggers = json.loads(row["triggers"]) if row["triggers"] else []
        if not triggers:
            continue

        metadata = json.loads(row["model_metadata"]) if row["model_metadata"] else {}
        if "triggers" in metadata:
            continue

        metadata["triggers"] = triggers
        cursor.execute(
            "UPDATE models SET model_metadata = ? WHERE id = ?",
            (json.dumps(metadata), row["id"]),
        )
        migrated += 1

    return migrated


def up():
    with db.get_cursor() as cursor:
        if not _table_exists(cursor, "model_attribute_definitions"):
            cursor.execute("""
                CREATE TABLE model_attribute_definitions (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    field_type TEXT NOT NULL,
                    model_types TEXT NOT NULL DEFAULT '[]',
                    config TEXT NOT NULL DEFAULT '{}',
                    default_value TEXT,
                    description TEXT,
                    per_user INTEGER NOT NULL DEFAULT 0,
                    admin_only INTEGER NOT NULL DEFAULT 0,
                    system INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(
                "CREATE INDEX idx_model_attribute_definitions_source "
                "ON model_attribute_definitions (source)"
            )

        if not _table_exists(cursor, "user_model_attributes"):
            cursor.execute("""
                CREATE TABLE user_model_attributes (
                    user_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, model_id, key),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
                )
            """)
            cursor.execute(
                "CREATE INDEX idx_user_model_attributes_user_model "
                "ON user_model_attributes (user_id, model_id)"
            )

        if _column_exists(cursor, "models", "triggers"):
            migrated = _migrate_triggers_into_metadata(cursor)
            cursor.execute("ALTER TABLE models DROP COLUMN triggers")
            print(f"Migration 135: migrated triggers into model_metadata for {migrated} model(s); dropped models.triggers")

    print("Migration 135: added model_attribute_definitions and user_model_attributes")


def down():
    with db.get_cursor() as cursor:
        if not _column_exists(cursor, "models", "triggers"):
            cursor.execute("ALTER TABLE models ADD COLUMN triggers TEXT")

            cursor.execute("SELECT id, model_metadata FROM models WHERE model_metadata IS NOT NULL")
            for row in cursor.fetchall():
                metadata = json.loads(row["model_metadata"]) if row["model_metadata"] else {}
                triggers = metadata.pop("triggers", None)
                if triggers is None:
                    continue
                cursor.execute(
                    "UPDATE models SET triggers = ?, model_metadata = ? WHERE id = ?",
                    (json.dumps(triggers), json.dumps(metadata), row["id"]),
                )

        cursor.execute("DROP TABLE IF EXISTS user_model_attributes")
        cursor.execute("DROP TABLE IF EXISTS model_attribute_definitions")

    print("Migration 135: reverted - restored models.triggers, dropped attribute tables")
