from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(users)")
        existing = {row[1] for row in cursor.fetchall()}
        if "has_local_password" not in existing:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN has_local_password "
                "INTEGER NOT NULL DEFAULT 1"
            )

    print("Migration 026_users_has_local_password: has_local_password column ready")


def down():
    print("Migration 026_users_has_local_password: no-op (SQLite keeps the added column)")
