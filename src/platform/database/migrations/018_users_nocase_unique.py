"""018 makes usernames and emails unique case-insensitively: `Anonymous`
and `anonymous` are the same account, at login and at registration alike.

IDEMPOTENT: `CREATE UNIQUE INDEX IF NOT EXISTS` on the NOCASE collation.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_nocase ON users (username COLLATE NOCASE)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_nocase ON users (email COLLATE NOCASE)"
        )
    print("Migration 018_users_nocase_unique: case-insensitive unique username/email")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_users_username_nocase")
        cursor.execute("DROP INDEX IF EXISTS idx_users_email_nocase")
