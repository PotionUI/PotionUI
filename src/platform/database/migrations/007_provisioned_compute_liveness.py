"""Migration 007: `provisioned_compute` learns to say why it is in the state
it is in and when it last checked.

- `status_detail` - the provider-facing reason behind `status` ("pod EXITED",
  "handshake failed after 3 attempts") or, while provisioning, the latest
  progress message.
- `status_checked_at` - when `status` was last reconciled against the provider
  (the heartbeat monitor writes it every tick).
- `progress` - the bring-up timeline as a JSON list of
  `{stage, message, percent, at}` entries, capped by the repository.

Also seeds the `provisioning.status_interval_seconds` setting the heartbeat
monitor reads at startup.

IDEMPOTENT: each column is added only when missing; the setting only when
absent.
"""

from datetime import datetime

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid

_COLUMNS = (
    ("status_detail", "TEXT"),
    ("status_checked_at", "TIMESTAMP"),
    ("progress", "TEXT NOT NULL DEFAULT '[]'"),
)

_SETTING_KEY = "provisioning.status_interval_seconds"


def up():
    added = []
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(provisioned_compute)")
        existing = {row[1] for row in cursor.fetchall()}
        for name, ddl in _COLUMNS:
            if name in existing:
                continue
            cursor.execute(f"ALTER TABLE provisioned_compute ADD COLUMN {name} {ddl}")
            added.append(name)

        cursor.execute("SELECT 1 FROM settings WHERE key = ?", (_SETTING_KEY,))
        seeded = cursor.fetchone() is None
        if seeded:
            now = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generate_ulid(), _SETTING_KEY, "15", "integer",
                    "How often (seconds, minimum 5) provisioned compute is reconciled against its provider.",
                    "SYSTEM", now, now,
                ),
            )
    print(
        f"Migration 007_provisioned_compute_liveness: added {added or 'no'} column(s), "
        f"setting {'seeded' if seeded else 'already present'}"
    )


def down():
    print("Migration 007_provisioned_compute_liveness: no-op (SQLite keeps the added columns)")
