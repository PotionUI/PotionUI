"""SQLite engine and schema migrations.

`db` is the process-wide connection factory; `migration_runner` brings the
schema up to date at startup. Both are singletons: the rest of the app reaches
for them rather than constructing its own.
"""

from .database import Database, get_database_connection
from .migration_runner import MigrationRunner, migration_runner

__all__ = [
    "Database",
    "db",
    "get_database_connection",
    "MigrationRunner",
    "migration_runner",
]


def __getattr__(name):
    """`db` is resolved on access, not bound here at import time. Binding it
    would hand every `from src.platform.database import db` a snapshot of
    whichever `Database` singleton existed when this package was first
    imported, which a later patch of the canonical
    `src.platform.database.database.db` could never reach.

    This only narrows the window: `from ... import db` still copies the
    result into the importer's own namespace. Code in this repository is
    therefore held to importing `db` inside the function that uses it, which
    the architecture guard in tests/architecture/test_db_import_hole.py
    enforces. The lazy resolution here is what protects out-of-tree plugins,
    which that guard cannot see."""
    if name == "db":
        from .database import db
        return db
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
