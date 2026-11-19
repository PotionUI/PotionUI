from datetime import datetime

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid

_NAME = "107_expand_formats"
_KEY = "editor_expand_formats"
_DESCRIPTION = (
    "House formats the Image editor's Expand task offers next to the built-in ones: a list of "
    "{id, label, ratio as W:H, group_id}. A format with a group_id is offered to that group's members only."
)


def up():
    with db.get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM settings WHERE key = ?", (_KEY,))
        if cursor.fetchone() is not None:
            print(f"Migration {_NAME}: the setting already exists")
            return
        now = datetime.now().isoformat()
        cursor.execute(
            """
            INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
            VALUES (?, ?, ?, 'json', ?, 'SYSTEM', ?, ?)
            """,
            (generate_ulid(), _KEY, "[]", _DESCRIPTION, now, now),
        )
    print(f"Migration {_NAME}: seeded {_KEY}")
