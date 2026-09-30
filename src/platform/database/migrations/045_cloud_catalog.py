from src.platform.database.database import db

_NAME = "045_cloud_catalog"

_CREATE_CATALOG = (
    "CREATE TABLE IF NOT EXISTS cloud_catalog ("
    "backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE CASCADE, "
    "slug TEXT NOT NULL, "
    "provider_model_id TEXT NOT NULL, "
    "label TEXT NOT NULL, "
    "vendor TEXT, "
    "tasks TEXT NOT NULL DEFAULT '[]', "
    "outputs TEXT NOT NULL DEFAULT '[]', "
    "spec TEXT NOT NULL, "
    "spec_version INTEGER NOT NULL DEFAULT 1, "
    "enabled INTEGER NOT NULL DEFAULT 0, "
    "suggested INTEGER NOT NULL DEFAULT 0, "
    "deprecated_at TEXT, "
    "discovered_at TEXT NOT NULL, "
    "refreshed_at TEXT NOT NULL, "
    "missing_since TEXT, "
    "enabled_at TEXT, "
    "PRIMARY KEY (backend_id, slug))"
)

_CREATE_STATE = (
    "CREATE TABLE IF NOT EXISTS cloud_catalog_state ("
    "backend_id TEXT PRIMARY KEY REFERENCES backends(id) ON DELETE CASCADE, "
    "refreshed_at TEXT NOT NULL, "
    "listed INTEGER NOT NULL DEFAULT 0, "
    "skipped TEXT NOT NULL DEFAULT '[]')"
)


def up():
    with db.get_cursor() as cursor:
        cursor.execute(_CREATE_CATALOG)
        cursor.execute(_CREATE_STATE)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cloud_catalog_backend_enabled ON cloud_catalog (backend_id, enabled)"
        )
    print(f"Migration {_NAME}: cloud catalog tables created")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS cloud_catalog_state")
        cursor.execute("DROP INDEX IF EXISTS idx_cloud_catalog_backend_enabled")
        cursor.execute("DROP TABLE IF EXISTS cloud_catalog")
    print(f"Migration {_NAME}: cloud catalog tables dropped")
