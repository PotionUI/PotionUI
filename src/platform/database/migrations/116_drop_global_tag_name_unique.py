"""Drop the table-level `UNIQUE` on `tags.name` that migration 029 could not.

Migration 020 created `tags` with `name TEXT NOT NULL UNIQUE` back when a tag
was a global label on a model. Migration 029 turned tags into a per-type,
per-user vocabulary and added

    CREATE UNIQUE INDEX idx_tags_name_type_user ON tags(name, type, COALESCE(user_id, ''))

noting that "SQLite doesn't support DROP CONSTRAINT, so we work with indexes".
It doesn't - the column-level UNIQUE from 020 is still enforced by its own
auto-index, and it is the stricter of the two. The compound index has therefore
never been the operative rule: whoever first creates a tag named "portrait"
claims that name across every type and every user, and the second user's
INSERT fails with "UNIQUE constraint failed: tags.name".

Dropping a column constraint means rebuilding the table, which is what this
does - same columns, same data, same indexes (read back from `sqlite_master`
and replayed, per migration 114), only the UNIQUE removed. Afterwards
`idx_tags_name_type_user` is the one uniqueness rule, which is what 029
intended and what every user-scoped tag type needs.
"""

import re

from src.platform.database.database import db

# Only the `name` column's own UNIQUE, in the declaration 020 wrote. A
# declaration that doesn't match is left alone rather than rewritten blind.
_NAME_UNIQUE = re.compile(r"(\bname\s+TEXT\s+NOT\s+NULL\s+)UNIQUE", re.IGNORECASE)


def _tags_table_sql(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tags'"
    ).fetchone()
    return row[0] if row else None


def _indexes_for(conn, table: str):
    """The CREATE INDEX statements currently on `table`.

    Read back, never hardcoded: which of 020's and 029's indexes a given
    database has depends on the path it took. Auto-indexes (`sql IS NULL`) come
    from the declaration itself - including the one this migration exists to
    remove, which is why it must not be replayed.
    """
    return [
        row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        )
    ]


def up():
    """Rebuild `tags` without the global UNIQUE on `name`."""
    with db.get_connection() as conn:
        table_sql = _tags_table_sql(conn)
        if not table_sql or not _NAME_UNIQUE.search(table_sql):
            return

        new_table_sql = _NAME_UNIQUE.sub(r"\1", table_sql)
        columns = ", ".join(row[1] for row in conn.execute("PRAGMA table_info(tags)"))
        indexes = _indexes_for(conn, "tags")

        # `legacy_alter_table` keeps the rename from rewriting the REFERENCES
        # clauses of model_tags/generation_tags/upload_tags to point at the
        # scratch name - the exact damage migration 114 had to repair.
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.execute("ALTER TABLE tags RENAME TO tags_name_unique_old")
            conn.execute(new_table_sql)
            conn.execute(
                f"INSERT INTO tags ({columns}) SELECT {columns} FROM tags_name_unique_old"
            )
            conn.execute("DROP TABLE tags_name_unique_old")
            for statement in indexes:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.commit()
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

        print("Migration 116: dropped the global UNIQUE on tags.name")


def down():
    """Nothing to undo.

    Restoring the constraint would fail on any database that has since made
    legitimate use of the per-type, per-user vocabulary it forbids.
    """
    return
