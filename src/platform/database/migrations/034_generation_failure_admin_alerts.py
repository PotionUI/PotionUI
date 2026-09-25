from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

_NAME = "034_generation_failure_admin_alerts"

_SETTINGS = (
    (
        "notify_admins_on_generation_failure",
        "false",
        "boolean",
        "Notify every admin when a generation fails, with a link to the failure.",
    ),
    (
        "notify_admins_on_generation_failure_categories",
        "[]",
        "json",
        "Error categories that raise an admin notification. Empty notifies for every category.",
    ),
)


def up():
    seeded = []
    with db.get_cursor() as cursor:
        now = now_iso()
        for key, value, value_type, description in _SETTINGS:
            cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if cursor.fetchone() is not None:
                continue
            cursor.execute(
                """
                INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'SYSTEM', ?, ?)
                """,
                (generate_ulid(), key, value, value_type, description, now, now),
            )
            seeded.append(key)
    print(f"Migration {_NAME}: seeded {len(seeded)} setting(s)")


def down():
    with db.get_cursor() as cursor:
        for key, _, _, _ in _SETTINGS:
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
    print(f"Migration {_NAME}: removed the admin failure alert settings")
