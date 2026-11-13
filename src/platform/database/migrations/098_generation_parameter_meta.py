from src.platform.database.database import db

_NAME = "098_generation_parameter_meta"

_COLUMNS = (
    ("meta", "TEXT"),
    ("position", "INTEGER"),
)


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(generation_parameters)")
        existing = {row[1] for row in cursor.fetchall()}
        for column, column_type in _COLUMNS:
            if column not in existing:
                cursor.execute(f"ALTER TABLE generation_parameters ADD COLUMN {column} {column_type}")
    print(f"Migration {_NAME}: generation_parameters.meta and generation_parameters.position ready")
