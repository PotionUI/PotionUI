from datetime import datetime, timezone

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid

_SETTINGS = (
    (
        "external_login_auto_create",
        "false",
        "boolean",
        "Whether an external login with no mapped account creates a local user.",
    ),
    (
        "external_login_default_group",
        "",
        "string",
        "Group id a user created by an external login is assigned to.",
    ),
    (
        "external_login_link_by_email",
        "false",
        "boolean",
        "Whether an external identity attaches to an existing local user with a matching verified email.",
    ),
)


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS external_identities ("
            "id TEXT PRIMARY KEY, "
            "issuer TEXT NOT NULL, "
            "subject TEXT NOT NULL, "
            "user_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "last_login_at TEXT, "
            "UNIQUE (issuer, subject), "
            "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_external_identities_user "
            "ON external_identities (user_id)"
        )

        now = datetime.now(timezone.utc).isoformat()
        seeded = []
        for key, value, value_type, description in _SETTINGS:
            cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if cursor.fetchone() is not None:
                continue
            cursor.execute(
                "INSERT INTO settings "
                "(id, key, value, value_type, description, type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'SYSTEM', ?, ?)",
                (generate_ulid(), key, value, value_type, description, now, now),
            )
            seeded.append(key)

    print(
        f"Migration 025_external_identities: external_identities table ready, "
        f"seeded {len(seeded)} setting(s)"
    )


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS external_identities")
        for key, _, _, _ in _SETTINGS:
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
    print("Migration 025_external_identities: removed external identities and settings")
