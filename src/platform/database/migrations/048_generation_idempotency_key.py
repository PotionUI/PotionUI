from src.platform.database.database import db

_NAME = "048_generation_idempotency_key"

_INDEX = "idx_generations_user_idempotency_key"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(generations)")
        existing = {row[1] for row in cursor.fetchall()}
        if "idempotency_key" not in existing:
            cursor.execute("ALTER TABLE generations ADD COLUMN idempotency_key TEXT")
        if "idempotency_fingerprint" not in existing:
            cursor.execute("ALTER TABLE generations ADD COLUMN idempotency_fingerprint TEXT")
        cursor.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} "
            "ON generations (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
    print(f"Migration {_NAME}: applied")


def down():
    with db.get_cursor() as cursor:
        cursor.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    print(f"Migration {_NAME}: index dropped (SQLite keeps the added column)")
