"""Migration 024: `prefix`/`suffix` on a rich prompt segment.

Adds optional `prefix TEXT` and `suffix TEXT` columns to the three child
tables that back `RichSegment` - `prompt_segments`, `saved_segments`, and
`segment_template_segments` - so a segment can carry text that wraps its
content without being folded into `content` itself. Both are NULL by
default; the join happens on the frontend, not here.

IDEMPOTENT: each column is added only when missing (checked via
`PRAGMA table_info`).
"""

from src.platform.database.database import db

_TABLES = ("prompt_segments", "saved_segments", "segment_template_segments")
_COLUMNS = ("prefix", "suffix")


def up():
    added = []
    with db.get_cursor() as cursor:
        for table in _TABLES:
            cursor.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cursor.fetchall()}
            for column in _COLUMNS:
                if column in existing:
                    continue
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
                added.append(f"{table}.{column}")

    print(f"Migration 024_segment_prefix_suffix: added {len(added)} column(s): {', '.join(added) or 'none'}")


def down():
    print("Migration 024_segment_prefix_suffix: no-op (SQLite keeps the added columns)")
