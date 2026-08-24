"""Repoint foreign keys left pointing at `files_old` by migration 035.

Migration 035 rebuilds `files` with `ALTER TABLE files RENAME TO files_old`
under `PRAGMA foreign_keys = OFF` alone. Since SQLite 3.25 that is not enough:
a RENAME also rewrites the REFERENCES clauses of every table pointing at the
renamed one (only `PRAGMA legacy_alter_table = ON` suppresses that, which 035
does not set), so `generation_files` comes out of the rebuild declared as
`REFERENCES "files_old"(id)`. 035 then drops `files_old`, leaving the junction
table with a foreign key to a table that no longer exists - every insert into
`generation_files` fails with "no such table: main.files_old" once
`PRAGMA foreign_keys = ON` (which `Database.get_connection` always sets), so a
finished generation cannot record its own outputs.

Historical migrations are never edited, so this is the forward repair for any
database that already ran 035's rebuild path - the same shape migration 112
uses for a different 035 defect. Databases where 035 was a no-op (no CHECK
constraint on file_type, which is every database built by the current chain)
never had a rename to follow through, so this migration finds nothing and does
nothing.

The scan is over every table, not just `generation_files`: 035's rename
rewrote whichever tables referenced `files` at the time, and repairing only the
one that happens to be known would leave the same broken foreign key elsewhere.
Each table is rebuilt from its own SQL with only the REFERENCES target
rewritten, so columns and constraints this database has reached 114 with
survive verbatim.
"""

import re

from src.platform.database.database import db

_FILES_OLD_REF = re.compile(
    r"(REFERENCES\s+)([\"'`\[]?)files_old([\"'`\]]?)",
    re.IGNORECASE,
)


def _tables_referencing_files_old(conn):
    """(name, sql) for every table whose declaration points at `files_old`."""
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
        )
        if row[0] != "files_old" and _FILES_OLD_REF.search(row[1])
    ]


def _indexes_for(conn, table: str):
    """The CREATE INDEX statements currently defined on `table`.

    Read back rather than hardcoded: a fixed list both drops indexes a given
    database has and fails outright on one naming a column it never got.
    Auto-indexes (`sql IS NULL`) are recreated by the table declaration itself.
    """
    return [
        row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        )
    ]


def _repointed(table_sql: str) -> str:
    """The same table declaration, with every `REFERENCES files_old` made
    `REFERENCES files`. Quoting the rename may have introduced is dropped."""
    return _FILES_OLD_REF.sub(r"\1files", table_sql)


def _rebuild(conn, table: str, new_table_sql: str) -> None:
    """Swap `table` for one declared by `new_table_sql`, keeping every row.

    `legacy_alter_table` is what stops this rename from doing to other tables
    exactly what 035's rename did to this one. Both PRAGMAs are no-ops inside an
    open transaction, so the pending one is committed first. Foreign keys stay
    off across the swap because the table being repaired currently references a
    table that does not exist.
    """
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    column_list = ", ".join(columns)
    indexes = _indexes_for(conn, table)
    scratch = f"{table}_fk_repair_old"

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute(f"ALTER TABLE {table} RENAME TO {scratch}")
        conn.execute(new_table_sql)
        conn.execute(f"INSERT INTO {table} ({column_list}) SELECT {column_list} FROM {scratch}")
        conn.execute(f"DROP TABLE {scratch}")
        for statement in indexes:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.commit()
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")


def up():
    """Point every dangling `files_old` foreign key back at `files`."""
    with db.get_connection() as conn:
        broken = _tables_referencing_files_old(conn)
        if not broken:
            return

        for table, table_sql in broken:
            _rebuild(conn, table, _repointed(table_sql))

        print(
            f"Migration 114: repaired files_old foreign key on "
            f"{', '.join(name for name, _ in broken)}"
        )


def down():
    """Nothing to undo.

    The state this migration repairs - a foreign key naming a table that was
    dropped - is corruption, not a schema version anyone would want back.
    """
    return
