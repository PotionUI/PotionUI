"""SQLite engine and schema migrations.

`db` is the process-wide connection factory; `migration_manager` brings the
schema up to date at startup. Both are singletons: the rest of the app reaches
for them rather than constructing its own.
"""

from .database import Database, db, get_database_connection
from .migration_runner import MigrationManager, migration_manager

__all__ = [
    "Database",
    "db",
    "get_database_connection",
    "MigrationManager",
    "migration_manager",
]
