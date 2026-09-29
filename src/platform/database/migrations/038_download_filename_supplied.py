from src.platform.database.database import db

_NAME = "038_download_filename_supplied"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(downloads)")
        columns = {row[1] for row in cursor.fetchall()}
        if "filename_supplied" not in columns:
            cursor.execute("ALTER TABLE downloads ADD COLUMN filename_supplied INTEGER NOT NULL DEFAULT 1")
    print(f"Migration {_NAME}: added downloads.filename_supplied")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(downloads)")
        columns = {row[1] for row in cursor.fetchall()}
        if "filename_supplied" in columns:
            cursor.execute("ALTER TABLE downloads DROP COLUMN filename_supplied")
    print(f"Migration {_NAME}: dropped downloads.filename_supplied")
