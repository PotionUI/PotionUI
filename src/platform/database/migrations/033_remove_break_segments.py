from src.platform.database.database import db

_DELETES = (
    ("prompt_segments", "type"),
    ("saved_segments", "type"),
    ("segment_template_segments", "type"),
    ("generation_segments", "segment_type"),
)


def up():
    removed = {}
    with db.get_cursor() as cursor:
        for table, column in _DELETES:
            cursor.execute(f"DELETE FROM {table} WHERE {column} = 'break'")
            removed[table] = cursor.rowcount
    summary = ", ".join(f"{table}={count}" for table, count in removed.items())
    print(f"Migration 033_remove_break_segments: removed break rows ({summary})")


def down():
    print("Migration 033_remove_break_segments: no-op (break rows are not recoverable)")
