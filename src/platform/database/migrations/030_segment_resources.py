from src.platform.database.database import db

_TABLES = ("prompt_segments", "saved_segments", "segment_template_segments")


def up():
    added = []
    with db.get_cursor() as cursor:
        for table in _TABLES:
            cursor.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cursor.fetchall()}
            if "resources" in existing:
                continue
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN resources TEXT NOT NULL DEFAULT '{{}}'")
            added.append(f"{table}.resources")

    print(f"Migration 030_segment_resources: added {len(added)} column(s): {', '.join(added) or 'none'}")


def down():
    print("Migration 030_segment_resources: no-op (SQLite keeps the added columns)")
