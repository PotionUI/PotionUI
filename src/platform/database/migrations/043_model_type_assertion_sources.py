from src.platform.database.database import db

_NAME = "043_model_type_assertion_sources"

_RANK = {"download": 1, "recipe": 2, "admin": 3}

_CREATE_PER_SOURCE = """
    CREATE TABLE model_type_assertions_new (
        sha256 TEXT NOT NULL,
        model_type TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('admin', 'recipe', 'download')),
        set_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        set_at TIMESTAMP NOT NULL,
        PRIMARY KEY (sha256, source)
    )
"""

_CREATE_PER_SHA = """
    CREATE TABLE model_type_assertions_new (
        sha256 TEXT PRIMARY KEY,
        model_type TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('admin', 'recipe', 'download')),
        set_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        set_at TIMESTAMP NOT NULL
    )
"""

_COLUMNS = "sha256, model_type, source, set_by, set_at"


def up():
    with db.get_cursor() as cursor:
        if _primary_key(cursor) == ["sha256", "source"]:
            print(f"Migration {_NAME}: assertions are already stored per source")
            return
        cursor.execute("DROP TABLE IF EXISTS model_type_assertions_new")
        cursor.execute(_CREATE_PER_SOURCE)
        cursor.execute(f"INSERT INTO model_type_assertions_new ({_COLUMNS}) SELECT {_COLUMNS} FROM model_type_assertions")
        cursor.execute("DROP TABLE model_type_assertions")
        cursor.execute("ALTER TABLE model_type_assertions_new RENAME TO model_type_assertions")
    print(f"Migration {_NAME}: type assertions are now stored per source")


def down():
    with db.get_cursor() as cursor:
        if _primary_key(cursor) == ["sha256"]:
            return
        cursor.execute(f"SELECT {_COLUMNS} FROM model_type_assertions")
        best = {}
        for row in cursor.fetchall():
            current = best.get(row["sha256"])
            if current is None or _RANK.get(row["source"], 0) > _RANK.get(current["source"], 0):
                best[row["sha256"]] = dict(row)
        cursor.execute("DROP TABLE IF EXISTS model_type_assertions_new")
        cursor.execute(_CREATE_PER_SHA)
        cursor.executemany(
            f"INSERT INTO model_type_assertions_new ({_COLUMNS}) VALUES (?, ?, ?, ?, ?)",
            [(r["sha256"], r["model_type"], r["source"], r["set_by"], r["set_at"]) for r in best.values()],
        )
        cursor.execute("DROP TABLE model_type_assertions")
        cursor.execute("ALTER TABLE model_type_assertions_new RENAME TO model_type_assertions")
    print(f"Migration {_NAME}: type assertions are back to one per content hash")


def _primary_key(cursor):
    cursor.execute("PRAGMA table_info(model_type_assertions)")
    rows = sorted((row for row in cursor.fetchall() if row["pk"]), key=lambda row: row["pk"])
    return [row["name"] for row in rows]
