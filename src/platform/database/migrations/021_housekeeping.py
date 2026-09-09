"""Migration 021: retention windows for the three unbounded stores.

`storage/tmp`, `generation_run_reports` and `chat_llm_call_traces` all grow
without a bound - the scratch folder because nothing ever removed a file from
it, the two tables because their only deletion path is a cascade from the row
they hang off. These three SYSTEM settings are the policy the housekeeping
worker applies; 0 means "keep everything" for the store it names.

The trace retention default matches the constant the trace recorder pruned by
before this setting existed.

IDEMPOTENT: each setting is inserted only when absent.
"""

from datetime import datetime

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid

_SETTINGS = (
    (
        "tmp_retention_days",
        "7",
        "integer",
        "Files in the scratch folder older than this many days are deleted. 0 keeps everything.",
    ),
    (
        "run_report_retention_days",
        "30",
        "integer",
        "Generation run reports older than this many days are deleted. 0 keeps everything.",
    ),
    (
        "llm_trace_retention_days",
        "7",
        "integer",
        "Chat LLM call traces older than this many days are deleted. 0 keeps everything.",
    ),
)


def up():
    seeded = []
    with db.get_cursor() as cursor:
        now = datetime.now().isoformat()
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

    print(f"Migration 021_housekeeping: seeded {len(seeded)} setting(s)")


def down():
    with db.get_cursor() as cursor:
        for key, _, _, _ in _SETTINGS:
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
    print("Migration 021_housekeeping: removed the retention settings")
