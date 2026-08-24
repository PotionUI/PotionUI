import sqlite3
import logging
import time
from contextlib import contextmanager
from typing import Generator
import threading
from pathlib import Path
import os

logger = logging.getLogger(__name__)

class Database:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(Database, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # Support test database isolation via environment variable
        # If POTIONUI_DB_PATH is set, use it; otherwise use default
        db_path = os.getenv('POTIONUI_DB_PATH', 'storage/db.sqlite')

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(exist_ok=True)
        self._initialized = True

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with automatic cleanup"""
        conn = sqlite3.connect(
            self.db_path, 
            check_same_thread=False,
            timeout=30.0  # Wait up to 30 seconds for locks to clear
        )
        conn.row_factory = sqlite3.Row  # Enable dict-like access to rows

        # Enable WAL mode for better concurrent access. `PRAGMA journal_mode=WAL`
        # (unlike the two setter pragmas below) returns a one-row result even when
        # setting the mode, not just when querying it - closing each of these
        # cursors explicitly (rather than discarding the bare `conn.execute(...)`
        # return value) keeps this connection commit-clean from the moment it's
        # handed out, instead of relying on refcounting to finalize them in time.
        conn.execute("PRAGMA journal_mode=WAL").close()
        conn.execute("PRAGMA foreign_keys = ON").close()  # Enable foreign key support

        # Set busy timeout to handle concurrent access
        conn.execute("PRAGMA busy_timeout = 30000").close()  # 30 seconds

        # `synchronous` isn't persisted in the database file the way journal_mode
        # is - it resets to SQLite's compiled default (FULL) on every fresh
        # connection, so this has to be set here, per-connection, not just once
        # in a migration. NORMAL is safe under WAL (only checkpoints fsync) and
        # avoids an fsync on every commit.
        conn.execute("PRAGMA synchronous = NORMAL").close()

        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def get_cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Get a database cursor with automatic transaction management.

        The cursor is closed *before* commit, not after: SQLite refuses to commit
        a connection with any of its statements still unfinalized ("cannot commit
        transaction - SQL statements in progress"), and closing finalizes this
        cursor's own statement. `_commit_with_retry` covers the residual case of
        a *different* cursor on this same connection (see its docstring).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
                cursor.close()
                self._commit_with_retry(conn)
            except Exception:
                cursor.close()
                conn.rollback()
                raise

    @staticmethod
    def _commit_with_retry(conn: sqlite3.Connection, attempts: int = 3, delay_s: float = 0.05) -> None:
        """Commit, retrying a bounded number of times on the specific transient
        "SQL statements in progress" error.

        This fires when *some* statement on this connection - not necessarily
        the one this `get_cursor()` call just ran - is still unfinalized at
        commit time. Closing this call's own cursor (see `get_cursor`) rules
        that one out; a short retry absorbs the remaining case where another
        cursor on the same connection hasn't released its statement yet,
        instead of the whole status/history write being dropped.
        """
        for attempt in range(attempts):
            try:
                conn.commit()
                return
            except sqlite3.OperationalError as e:
                if "sql statements in progress" not in str(e).lower() or attempt == attempts - 1:
                    raise
                logger.debug(f"commit retry {attempt + 1}/{attempts} after: {e}")
                time.sleep(delay_s)

# Global database instance
db = Database()

# Convenience function for getting database connections
def get_database_connection():
    """Get database connection - wrapper around global db instance"""
    return db.get_connection()
