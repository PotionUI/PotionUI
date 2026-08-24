"""
Give `generations` a trustworthy duration, and repair `completed_at`.

Until now there was no stored duration and no `started_at`, so the only way to time a
generation was `completed_at - created_at`. That subtraction was wrong, because the two
columns were written by different clocks:

- `created_at` / `updated_at` come from SQLite `CURRENT_TIMESTAMP` -> UTC, "YYYY-MM-DD HH:MM:SS"
- `completed_at` came from Python `datetime.now().isoformat()` -> naive *local* time,
  "YYYY-MM-DDTHH:MM:SS.ffffff" (generation_repository.update_status)

On a UTC+1/+2 host the naive difference therefore had a ~3600s floor that jumped to ~7200s
after the DST change. Measured on a real database: minimum "duration" 3600.4s in January,
7200.8s in April. Actual durations are a median of ~17s.

`updated_at` is the honest completion timestamp: the `update_generations_updated_at` trigger
sets it to `CURRENT_TIMESTAMP` on the same UPDATE that sets `completed_at`, so it records the
same instant in UTC. This migration uses it to backfill `duration_ms` and to rewrite
`completed_at` in UTC.

Two things make that safe rather than a guess:

1. The trigger is dropped for the duration of the backfill and recreated afterwards.
   Otherwise the very UPDATE that reads `updated_at` would fire the trigger and overwrite
   `updated_at` with the migration's own wall-clock time.

2. Every row is drift-checked. `updated_at` is bumped by *any* later UPDATE (rating a
   generation, favouriting it), which would make it a poor proxy for completion. A row is
   only trusted when `completed_at - updated_at` is a whole number of hours (within 2s) --
   i.e. it still differs from `updated_at` by nothing but a timezone offset. Rows that fail
   the check keep `duration_ms = NULL` (an honest unknown) and keep their original
   `completed_at`, so a database where ratings have been used degrades instead of corrupting.

`started_at` is added for the forward path: `update_status` sets it on the `running` transition
so that a duration can one day exclude queue wait. Note that **nothing currently transitions a
generation to `running`** -- `status_tracker.transition` is only ever called with COMPLETED,
FAILED or CANCELLED -- so in practice `started_at` stays NULL and `duration_ms` measures
completed-minus-created, queue wait included. The column and the COALESCE in `update_status` are
in place for when a running transition is emitted. It is left NULL for history: that information
was never recorded and cannot be reconstructed.

down() drops both columns. It cannot restore the original local-time `completed_at` values;
that is a deliberate one-way repair.
"""

from src.platform.database.database import db


# Recreated verbatim from 002_create_generations.py / 032_make_preset_id_nullable.py.
TRIGGER_SQL = """
    CREATE TRIGGER update_generations_updated_at
    AFTER UPDATE ON generations
    FOR EACH ROW
    BEGIN
        UPDATE generations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
    END
"""

# A row is trustworthy when its completed_at differs from updated_at only by a whole-hour
# timezone offset. ROUND(delta/3600)*3600 is the nearest whole hour; anything more than 2s
# away from it means updated_at was bumped after completion.
_TRUSTED_ROW = """
    status = 'completed'
    AND created_at IS NOT NULL
    AND updated_at IS NOT NULL
    AND completed_at IS NOT NULL
    AND julianday(updated_at) >= julianday(created_at)
    AND ABS(
            (julianday(completed_at) - julianday(updated_at)) * 86400.0
            - ROUND(((julianday(completed_at) - julianday(updated_at)) * 86400.0) / 3600.0) * 3600.0
        ) <= 2.0
"""


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if _has_column(cursor, "generations", "duration_ms"):
            print("Migration 075: generations.duration_ms already present, skipping")
            return

        cursor.execute("ALTER TABLE generations ADD COLUMN duration_ms INTEGER")
        cursor.execute("ALTER TABLE generations ADD COLUMN started_at TIMESTAMP")

        cursor.execute("SELECT COUNT(*) FROM generations WHERE status = 'completed'")
        completed = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM generations WHERE {_TRUSTED_ROW}")
        trusted = cursor.fetchone()[0]

        # The trigger would overwrite updated_at -- the column both statements read from.
        cursor.execute("DROP TRIGGER IF EXISTS update_generations_updated_at")
        try:
            cursor.execute(f"""
                UPDATE generations
                SET duration_ms = CAST(
                    ROUND((julianday(updated_at) - julianday(created_at)) * 86400000.0) AS INTEGER
                )
                WHERE {_TRUSTED_ROW}
            """)
            backfilled = cursor.rowcount

            cursor.execute(f"""
                UPDATE generations
                SET completed_at = updated_at
                WHERE {_TRUSTED_ROW}
            """)
            repaired = cursor.rowcount
        finally:
            cursor.execute(TRIGGER_SQL)

        skipped = completed - trusted
        print(
            f"Migration 075: added generations.duration_ms + started_at; "
            f"backfilled {backfilled} durations, repaired {repaired} completed_at values"
            + (f", skipped {skipped} untrusted completed rows" if skipped else "")
        )


def down():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "generations", "duration_ms"):
            print("Migration 075: generations.duration_ms absent, nothing to drop")
            return

        # SQLite gained DROP COLUMN in 3.35 (2021); the runtime is well past that.
        cursor.execute("ALTER TABLE generations DROP COLUMN duration_ms")
        cursor.execute("ALTER TABLE generations DROP COLUMN started_at")
        print("Migration 075: dropped generations.duration_ms + started_at")
