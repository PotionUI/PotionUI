"""Allow 'MESH' as a files.file_type.

On every database built by the current migration chain this is a no-op:
migration 010 creates `files` with a plain `file_type TEXT NOT NULL` and no
CHECK constraint, so 'MESH' already inserts. It is here for the one shape that
does constrain the column - migration 035's *recovery* branch (files_old
present, files absent) rebuilds the table with
`CHECK(file_type IN ('IMAGE', 'VIDEO', 'AUDIO'))`, which rejects 'MESH'
outright. A database that went through that branch would fail every mesh save
with an IntegrityError, so the constraint is widened where it exists.

Unlike migration 035, the replacement table is derived from the existing
table's own SQL rather than re-declared column by column. Only the CHECK clause
is rewritten, so whatever columns a given database has reached this point with
survive verbatim - a hardcoded column list silently drops any column added
between the two migrations being written.
"""

import re

from src.platform.database.database import db

_CHECK_CLAUSE = re.compile(
    r"CHECK\s*\(\s*file_type\s+IN\s*\((?P<values>[^)]*)\)\s*\)",
    re.IGNORECASE,
)

def _files_indexes(conn):
    """The CREATE INDEX statements currently defined on `files`.

    Read back rather than hardcoded, for the same reason the table SQL is:
    a fixed list both drops indexes a database has and fails outright on one
    naming a column that database never got. Auto-indexes (`sql IS NULL`) are
    recreated by the table declaration itself.
    """
    return [
        row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name='files' AND sql IS NOT NULL"
        )
    ]


def _files_table_sql(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='files'"
    ).fetchone()
    return row[0] if row else None


def _widened(table_sql: str, value: str) -> str:
    """Rewrite the file_type CHECK clause to also accept `value`.

    Returns the SQL unchanged when there is no CHECK on file_type, or when it
    already lists `value`.
    """
    match = _CHECK_CLAUSE.search(table_sql)
    if not match:
        return table_sql
    if f"'{value}'" in match.group("values").upper():
        return table_sql

    replacement = f"CHECK(file_type IN ({match.group('values').strip()}, '{value}'))"
    return table_sql[:match.start()] + replacement + table_sql[match.end():]


def _rebuild(conn, new_table_sql: str) -> None:
    """Swap `files` for a table declared by `new_table_sql`, keeping every row.

    `legacy_alter_table` is what stops the rename from following through into
    other tables: since SQLite 3.25 an `ALTER TABLE ... RENAME` edits the
    REFERENCES clauses of every table pointing at the renamed one, which would
    leave generation_files referencing files_old after the swap. Turning
    foreign keys off is not enough on its own - both PRAGMAs are no-ops inside
    an open transaction, so the pending one is committed first.
    """
    columns = [row[1] for row in conn.execute("PRAGMA table_info(files)")]
    column_list = ", ".join(columns)
    indexes = _files_indexes(conn)

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute("ALTER TABLE files RENAME TO files_old")
        conn.execute(new_table_sql)
        conn.execute(f"INSERT INTO files ({column_list}) SELECT {column_list} FROM files_old")
        conn.execute("DROP TABLE files_old")
        for statement in indexes:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.commit()
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")


def up():
    """Add MESH to the file_type CHECK constraint, where one exists."""
    with db.get_connection() as conn:
        table_sql = _files_table_sql(conn)
        if not table_sql:
            return

        new_table_sql = _widened(table_sql, "MESH")
        if new_table_sql == table_sql:
            return

        _rebuild(conn, new_table_sql)


def down():
    """Drop MESH from the constraint again, and the rows that relied on it."""
    with db.get_connection() as conn:
        table_sql = _files_table_sql(conn)
        if not table_sql:
            return

        match = _CHECK_CLAUSE.search(table_sql)
        if not match or "'MESH'" not in match.group("values").upper():
            return

        remaining = [
            value.strip()
            for value in match.group("values").split(",")
            if value.strip().upper() != "'MESH'"
        ]
        replacement = f"CHECK(file_type IN ({', '.join(remaining)}))"
        new_table_sql = table_sql[:match.start()] + replacement + table_sql[match.end():]

        conn.execute("DELETE FROM files WHERE file_type = 'MESH'")
        _rebuild(conn, new_table_sql)
