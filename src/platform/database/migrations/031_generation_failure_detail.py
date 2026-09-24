from src.platform.database.database import db

_COLUMNS = (
    "error_code",
    "error_user_message",
    "error_detail",
    "failed_pipe_id",
    "failed_pipe_name",
    "failed_at_step",
)


def up():
    added = []
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(generations)")
        existing = {row[1] for row in cursor.fetchall()}
        for column in _COLUMNS:
            if column in existing:
                continue
            cursor.execute(f"ALTER TABLE generations ADD COLUMN {column} TEXT")
            added.append(column)

    print(f"Migration 031_generation_failure_detail: added {len(added)} column(s): {', '.join(added) or 'none'}")


def down():
    print("Migration 031_generation_failure_detail: no-op (SQLite keeps the added columns)")
