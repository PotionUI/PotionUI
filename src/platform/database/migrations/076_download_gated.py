from src.platform.database.database import db

_NAME = "076_download_gated"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(downloads)")
        columns = {row[1] for row in cursor.fetchall()}
        if "gated" not in columns:
            cursor.execute("ALTER TABLE downloads ADD COLUMN gated INTEGER NOT NULL DEFAULT 0")
        if "gated_repo" not in columns:
            cursor.execute("ALTER TABLE downloads ADD COLUMN gated_repo TEXT")
    print(f"Migration {_NAME}: added downloads.gated and downloads.gated_repo")
