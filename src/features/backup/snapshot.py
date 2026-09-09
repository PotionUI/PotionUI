"""Point-in-time copies of the SQLite database."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from src.features.backup.repository import (
    applied_migration_names,
    integrity_problems,
    row_counts,
    vacuum_into,
)


def snapshot_database(source: Path, destination: Path) -> int:
    """Write a consistent copy of `source` to `destination` and return its size.

    `VACUUM INTO` reads through the write-ahead log inside a read transaction.
    Copying the database file instead would silently drop everything still in
    the WAL, and would race any writer that commits mid-copy.
    """
    source = Path(source)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"snapshot destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    vacuum_into(source, destination)
    return destination.stat().st_size


def integrity_check(db_path: Path) -> List[str]:
    """The problems `PRAGMA integrity_check` reports, empty when the file is sound."""
    return integrity_problems(Path(db_path))


def applied_migrations(db_path: Path) -> List[str]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    return applied_migration_names(db_path)


def migration_head(db_path: Path) -> Optional[str]:
    applied = applied_migrations(db_path)
    return applied[-1] if applied else None


def table_row_counts(db_path: Path) -> Dict[str, int]:
    """Row count per user table - the cheapest proof that a copy is complete."""
    return row_counts(Path(db_path))
