import unittest
import tempfile
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch
from src.platform.database.database import Database


class TestDatabase(unittest.TestCase):
    def setUp(self):
        """Create a temporary database for testing"""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"
        
        # Create a test database instance with custom path
        self.db = TestDatabase._create_test_database(self.temp_db_path)
    
    def tearDown(self):
        """Clean up test database"""
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        os.rmdir(self.temp_dir)
    
    @staticmethod
    def _create_test_database(db_path: Path) -> Database:
        """Create a database instance for testing"""
        # Reset the singleton instance for testing
        Database._instance = None
        db = Database()
        db.db_path = db_path
        db.db_path.parent.mkdir(exist_ok=True)
        return db
    
    def test_database_creation(self):
        """Test that database file is created"""
        with self.db.get_connection():
            pass
        self.assertTrue(self.temp_db_path.exists())
    
    def test_get_connection(self):
        """Test getting a database connection"""
        with self.db.get_connection() as conn:
            self.assertIsInstance(conn, sqlite3.Connection)
            self.assertEqual(conn.row_factory, sqlite3.Row)
    
    def test_get_cursor(self):
        """Test getting a database cursor with transaction management"""
        with self.db.get_cursor() as cursor:
            cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
            cursor.execute("INSERT INTO test (name) VALUES (?)", ("test_name",))
        
        # Verify the transaction was committed
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM test WHERE id = 1")
            result = cursor.fetchone()
            self.assertEqual(result[0], "test_name")
    
    def test_cursor_rollback_on_exception(self):
        """Test that transactions are rolled back on exceptions"""
        # First create the table (DDL operations auto-commit in SQLite)
        with self.db.get_cursor() as cursor:
            cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        
        # Now test rollback on DML operations (which can be rolled back)
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute("INSERT INTO test (name) VALUES (?)", ("test_name",))
                raise Exception("Test exception")
        except Exception:
            pass
        
        # Verify the insert was rolled back
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM test")
            result = cursor.fetchone()
            self.assertEqual(result[0], 0)
    
    def test_foreign_keys_enabled(self):
        """Test that foreign keys are enabled"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys")
            result = cursor.fetchone()
            self.assertEqual(result[0], 1)

    def test_synchronous_is_normal(self):
        """`synchronous` resets to SQLite's compiled default (FULL) on every
        fresh connection - unlike journal_mode it isn't persisted in the
        database file - so it must be set on this connection, not just once
        in a migration."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA synchronous")
            result = cursor.fetchone()
            self.assertEqual(result[0], 1)  # 1 == NORMAL

    def test_connection_close_does_not_checkpoint_the_wal(self):
        """Checkpointing on every connection close (including reads) waits
        out other readers/writers under busy_timeout, turning routine calls
        into near-exclusive-lock requests under load. Passive checkpointing
        is left to SQLite's default wal_autocheckpoint.

        sqlite3.Connection is a C type - its methods can't be monkeypatched
        on the instance or the class - so this spies via a proxy returned in
        place of the real connection instead of patching `execute` directly.
        """
        executed = []

        class _RecordingConn:
            def __init__(self, real_conn):
                self._real = real_conn

            def execute(self, sql, *args, **kwargs):
                executed.append(sql)
                return self._real.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._real, name)

        real_connect = sqlite3.connect

        def connecting_proxy(*args, **kwargs):
            return _RecordingConn(real_connect(*args, **kwargs))

        with patch('src.platform.database.database.sqlite3.connect', side_effect=connecting_proxy):
            with self.db.get_connection():
                pass

        self.assertFalse(
            any('wal_checkpoint' in sql.lower() for sql in executed),
            f"connection close ran a wal_checkpoint: {executed}",
        )

    def test_commit_retries_on_sql_statements_in_progress(self):
        """`_commit_with_retry` must retry (not immediately fail) a commit that
        hits SQLite's "cannot commit transaction - SQL statements in progress"
        - the same error status_tracker.py's persist call was surfacing - and
        must still raise straight through for any other OperationalError."""
        calls = {"n": 0}
        real_commit = self.db.get_connection

        class _FlakyConn:
            def __init__(self, real_conn):
                self._real = real_conn

            def commit(self):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise sqlite3.OperationalError("cannot commit transaction - SQL statements in progress")
                self._real.commit()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE retry_test (id INTEGER PRIMARY KEY)")
            conn.commit()
            cursor.close()

        with real_commit() as conn:
            flaky = _FlakyConn(conn)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO retry_test (id) VALUES (1)")
            cursor.close()
            Database._commit_with_retry(flaky, attempts=5, delay_s=0.001)

        self.assertEqual(calls["n"], 3)

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM retry_test")
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_commit_retry_gives_up_on_unrelated_operational_error(self):
        """A different OperationalError (e.g. a real lock) must not be retried
        away silently - it should raise on the first attempt."""
        class _AlwaysLockedConn:
            def commit(self):
                raise sqlite3.OperationalError("database is locked")

        with self.assertRaises(sqlite3.OperationalError):
            Database._commit_with_retry(_AlwaysLockedConn(), attempts=5, delay_s=0.001)


if __name__ == '__main__':
    unittest.main()