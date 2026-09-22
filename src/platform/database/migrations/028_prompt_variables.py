
from src.platform.database.database import db

_COLUMN = ("prompts", "variables", "TEXT")


def up():
    added = None
    with db.get_cursor() as cursor:
        table, column, ddl = _COLUMN
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cursor.fetchall()}
        if column not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added = f"{table}.{column}"

    print(f"Migration 028_prompt_variables: added {added or 'no'} column")


def down():
    print("Migration 028_prompt_variables: no-op (SQLite keeps the added column)")
