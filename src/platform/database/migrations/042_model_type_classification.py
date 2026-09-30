from src.platform.database.database import db
from src.platform.filesystem.model_types import binding_scans_headers_by_default

_NAME = "042_model_type_classification"

_CREATE_VERDICTS = """
    CREATE TABLE IF NOT EXISTS model_header_verdicts (
        sha256 TEXT PRIMARY KEY,
        format TEXT NOT NULL,
        status TEXT NOT NULL,
        model_type TEXT,
        family TEXT,
        variant TEXT,
        components TEXT NOT NULL DEFAULT '[]',
        transformer_extractable INTEGER NOT NULL DEFAULT 0,
        classifier TEXT,
        registry_fingerprint TEXT NOT NULL,
        reason TEXT,
        classified_at TIMESTAMP NOT NULL
    )
"""

_CREATE_ASSERTIONS = """
    CREATE TABLE IF NOT EXISTS model_type_assertions (
        sha256 TEXT PRIMARY KEY,
        model_type TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('admin', 'recipe', 'download')),
        set_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        set_at TIMESTAMP NOT NULL
    )
"""


def up():
    with db.get_cursor() as cursor:
        seeded = _add_scan_headers(cursor)
        if "type_source" not in _column_names(cursor, "models"):
            cursor.execute("ALTER TABLE models ADD COLUMN type_source TEXT NOT NULL DEFAULT 'folder'")
        cursor.execute(_CREATE_VERDICTS)
        cursor.execute(_CREATE_ASSERTIONS)
    print(f"Migration {_NAME}: added header classification tables, {seeded} binding(s) scan headers by default")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS model_type_assertions")
        cursor.execute("DROP TABLE IF EXISTS model_header_verdicts")
        if "type_source" in _column_names(cursor, "models"):
            cursor.execute("ALTER TABLE models DROP COLUMN type_source")
        if "scan_headers" in _column_names(cursor, "model_root_bindings"):
            cursor.execute("ALTER TABLE model_root_bindings DROP COLUMN scan_headers")
    print(f"Migration {_NAME}: removed header classification tables and columns")


def _column_names(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def _add_scan_headers(cursor):
    if "scan_headers" in _column_names(cursor, "model_root_bindings"):
        return 0
    cursor.execute("ALTER TABLE model_root_bindings ADD COLUMN scan_headers INTEGER NOT NULL DEFAULT 0")
    cursor.execute("SELECT root_id, model_type, subdir FROM model_root_bindings")
    enabled = [
        (row["root_id"], row["model_type"])
        for row in cursor.fetchall()
        if binding_scans_headers_by_default(row["model_type"], row["subdir"])
    ]
    cursor.executemany(
        "UPDATE model_root_bindings SET scan_headers = 1 WHERE root_id = ? AND model_type = ?",
        enabled,
    )
    return len(enabled)
