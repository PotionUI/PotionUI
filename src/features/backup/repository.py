"""SQLite access for backup and restore, addressed by file path.

Unlike every other repository here, these queries do not go through the
process-wide connection: a backup reads a database that may not be the one this
process booted against, and a restore inspects an archived copy in a staging
directory. The path is therefore an argument, and every handle is read-only
apart from the snapshot itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

VACUUM_TIMEOUT_SECONDS = 60.0


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """A read-only handle, falling back to a normal open when the read-only URI
    cannot build its shared-memory index."""
    try:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    except sqlite3.Error:
        return sqlite3.connect(db_path, timeout=30.0)


def vacuum_into(source: Path, destination: Path) -> None:
    conn = sqlite3.connect(source, timeout=VACUUM_TIMEOUT_SECONDS)
    conn.isolation_level = None
    try:
        conn.execute("PRAGMA busy_timeout = 60000").close()
        conn.execute("VACUUM INTO ?", (str(destination),)).close()
    finally:
        conn.close()


def integrity_problems(db_path: Path) -> List[str]:
    conn = open_readonly(db_path)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows if row[0] != "ok"]


def applied_migration_names(db_path: Path) -> List[str]:
    conn = open_readonly(db_path)
    try:
        rows = conn.execute(
            "SELECT migration_name FROM applied_migrations ORDER BY migration_name"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [row[0] for row in rows]


def row_counts(db_path: Path) -> Dict[str, int]:
    conn = open_readonly(db_path)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        return {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        conn.close()


def setting_value(db_path: Path, key: str) -> str | None:
    try:
        conn = open_readonly(db_path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return str(row[0]) if row and row[0] not in (None, "") else None


def generation_file_rows(db_path: Path) -> List[Any]:
    conn = open_readonly(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT f.file_path, f.file_type, f.thumbnail_small, f.thumbnail_medium, "
            "f.thumbnail_large, gf.generation_id "
            "FROM files f LEFT JOIN generation_files gf ON gf.file_id = f.id"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def upload_rows(db_path: Path) -> List[Any]:
    conn = open_readonly(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT filename, media_type, thumbnail_small, thumbnail_medium, thumbnail_large "
            "FROM uploads"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
