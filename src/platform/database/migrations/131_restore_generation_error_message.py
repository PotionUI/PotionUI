"""
Migration 131: restore `generations.error_message`, dropped by migration 032
as an "unused column" and never brought back.

It is not unused any more - `Generation.error_message` (records.py),
`GenerationRepository.update_status()` and
`GenerationStatusTracker.transition()` (status_tracker.py) all read/write it
as the short failure summary set on a FAILED transition. Every one of those
writes has been silently going nowhere since 032: the column does not exist,
so `Generation.from_row()`'s `safe_get()` swallows the missing-column lookup
and always reads back None, regardless of what `transition()` actually
recorded. A generation that fails therefore reads back with no indication of
why - not because the failure was uninformative, but because there was never
anywhere for that information to land.
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "generations", "error_message"):
            cursor.execute("ALTER TABLE generations ADD COLUMN error_message TEXT")
        print("Migration 131: restored generations.error_message")


def down():
    with db.get_cursor() as cursor:
        if _has_column(cursor, "generations", "error_message"):
            cursor.execute("ALTER TABLE generations DROP COLUMN error_message")
        print("Migration 131: dropped generations.error_message")
