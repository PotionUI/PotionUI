"""
Split model *identity* from model *location*.

A `models` row used to be defined by a local file: `file_path NOT NULL UNIQUE`, created
only after the indexer opened the file and hashed its bytes. But the `comfyui` engine
never receives a path — it is handed a bare name that the remote server resolves against
its own `folder_paths`. So a model living only on a remote ComfyUI could not be
represented at all, and selecting one required downloading the weights to this host,
where nothing would ever read them.

After this migration:

* `models` is the logical model, identified by `(model_type, filename)`. `file_path` is
  nullable (a remote-only model has no local path) and no longer UNIQUE.
* `model_availability` records that a given backend can load a given model, together with
  `ref` — the **engine-native string** that backend needs: `models/loras/x.safetensors`
  for native, `style/x.safetensors` for comfyui.

`sha256` stays on the model row when native indexing computed it. ComfyUI cannot report
hashes, so it is never required for matching. See docs/models.md.

The `models` rebuild runs with `PRAGMA foreign_keys = OFF`. This is not optional: nine
tables carry `REFERENCES models(id)`, and with foreign keys enabled `ALTER TABLE ... RENAME`
rewrites those child references to the renamed table, after which `DROP TABLE` cascades and
silently deletes every dependent row — including all of `generation_models`.
"""

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid


MODELS_NEW = """
CREATE TABLE models_new (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_path TEXT,
    file_size INTEGER,
    sha256 TEXT UNIQUE,
    model_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_notes TEXT DEFAULT NULL,
    description TEXT,
    triggers TEXT,
    UNIQUE(model_type, filename)
)
"""

MODELS_OLD = """
CREATE TABLE models_new (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    file_size INTEGER,
    sha256 TEXT UNIQUE,
    model_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_notes TEXT DEFAULT NULL,
    description TEXT,
    triggers TEXT
)
"""

COPY_COLUMNS = (
    "id, filename, file_path, file_size, sha256, model_type, "
    "created_at, updated_at, indexed_at, user_notes, description, triggers"
)

MODEL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_models_model_type ON models (model_type)",
    "CREATE INDEX IF NOT EXISTS idx_models_sha256 ON models (sha256)",
    "CREATE INDEX IF NOT EXISTS idx_models_filename ON models (filename)",
    "CREATE INDEX IF NOT EXISTS idx_models_indexed_at ON models (indexed_at)",
]

AVAILABILITY = """
CREATE TABLE IF NOT EXISTS model_availability (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    backend_id TEXT NOT NULL,
    ref TEXT NOT NULL,
    size INTEGER,
    confidence TEXT NOT NULL DEFAULT 'reported',
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,
    FOREIGN KEY (backend_id) REFERENCES backends(id) ON DELETE CASCADE,
    UNIQUE(model_id, backend_id)
)
"""

AVAILABILITY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_availability_backend ON model_availability (backend_id)",
    "CREATE INDEX IF NOT EXISTS idx_availability_model ON model_availability (model_id)",
]


def _rebuild_models(conn, ddl: str) -> None:
    """Swap the `models` table for one built by `ddl`, preserving every dependent row."""
    conn.execute("DROP TABLE IF EXISTS models_new")
    conn.execute(ddl)
    conn.execute(f"INSERT INTO models_new ({COPY_COLUMNS}) SELECT {COPY_COLUMNS} FROM models")
    conn.execute("DROP TABLE models")
    conn.execute("ALTER TABLE models_new RENAME TO models")
    for stmt in MODEL_INDEXES:
        conn.execute(stmt)


def _guard_identity_collisions(conn) -> None:
    rows = conn.execute(
        "SELECT model_type, filename, COUNT(*) c FROM models "
        "GROUP BY model_type, filename HAVING c > 1"
    ).fetchall()
    if rows:
        offenders = ", ".join(f"{r[0]}/{r[1]} x{r[2]}" for r in rows[:5])
        raise RuntimeError(
            "Migration 074: cannot apply UNIQUE(model_type, filename) — duplicate "
            f"identities exist: {offenders}. Resolve them before migrating."
        )


def _backfill_native_availability(conn) -> int:
    """Every existing model row is, by construction, a file on the native backend."""
    native = conn.execute(
        "SELECT id FROM backends WHERE engine = 'native' ORDER BY id LIMIT 1"
    ).fetchone()
    if not native:
        print("Migration 074: no native backend row; skipping availability backfill")
        return 0

    native_id = native[0]
    rows = conn.execute(
        "SELECT id, file_path, file_size, sha256 FROM models WHERE file_path IS NOT NULL"
    ).fetchall()

    inserted = 0
    for model_id, file_path, file_size, sha256 in rows:
        conn.execute(
            "INSERT OR IGNORE INTO model_availability "
            "(id, model_id, backend_id, ref, size, confidence) VALUES (?, ?, ?, ?, ?, ?)",
            (
                generate_ulid(),
                model_id,
                native_id,
                file_path,
                file_size,
                "verified" if sha256 else "reported",
            ),
        )
        inserted += 1
    return inserted


def _transaction(conn):
    """Manual transaction control so PRAGMA foreign_keys can be toggled outside it."""
    conn.isolation_level = None


def up():
    with db.get_connection() as conn:
        _transaction(conn)
        conn.execute("PRAGMA foreign_keys = OFF")

        try:
            conn.execute("BEGIN")
            _guard_identity_collisions(conn)
            _rebuild_models(conn, MODELS_NEW)
            conn.execute(AVAILABILITY)
            for stmt in AVAILABILITY_INDEXES:
                conn.execute(stmt)
            n = _backfill_native_availability(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            conn.execute("PRAGMA foreign_keys = ON")
            raise

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.execute("PRAGMA foreign_keys = ON")
        if violations:
            raise RuntimeError(f"Migration 074: foreign key violations after rebuild: {violations[:5]}")

        print(f"Migration 074: models identity relaxed; {n} native availability rows backfilled")


def down():
    with db.get_connection() as conn:
        _transaction(conn)
        conn.execute("PRAGMA foreign_keys = OFF")

        try:
            conn.execute("BEGIN")
            orphans = conn.execute(
                "SELECT COUNT(*) FROM models WHERE file_path IS NULL"
            ).fetchone()[0]
            if orphans:
                raise RuntimeError(
                    f"Migration 074 down(): {orphans} model(s) have no local file_path and "
                    "cannot be represented by the old schema. Delete them first."
                )
            conn.execute("DROP TABLE IF EXISTS model_availability")
            _rebuild_models(conn, MODELS_OLD)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            conn.execute("PRAGMA foreign_keys = ON")
            raise

        conn.execute("PRAGMA foreign_keys = ON")
        print("Migration 074: reverted models to file_path NOT NULL UNIQUE")
