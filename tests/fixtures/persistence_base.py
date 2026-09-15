import unittest
import tempfile
import os
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock
import sys

# Mock missing dependencies
def mock_generate_ulid():
    """Mock ULID generation for tests"""
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))

# Mock id_utils module if not available
try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    # Create a mock module
    mock_id_utils = MagicMock()
    mock_id_utils.generate_ulid = mock_generate_ulid
    sys.modules['src.platform.util.ids'] = mock_id_utils
    generate_ulid = mock_generate_ulid

import importlib

from src.platform.database.database import Database, db as REAL_DB


class PersistenceTestBase(unittest.TestCase):
    """Base class for persistence tests with database setup"""
    
    def setUp(self):
        """Set up test database with a fully migrated schema"""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        self.db = self._create_test_database(self.temp_db_path)
    
    def tearDown(self):
        """Clean up test database"""
        # Close any open connections first
        try:
            if hasattr(self, 'db'):
                # Clear all data from test tables before cleanup
                with self.db.get_cursor() as cursor:
                    cursor.execute("DELETE FROM generation_files")
                    cursor.execute("DELETE FROM files")
                    cursor.execute("DELETE FROM generations")
                    cursor.execute("DELETE FROM users")
        except:
            pass  # Ignore errors during cleanup

        self._restore_patched_db()

        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        os.rmdir(self.temp_dir)

        # Reset singleton for next test
        Database._instance = None

    def _restore_patched_db(self):
        """Point every module that was redirected at the temp database back at
        the real one.

        The temp database is deleted at the end of the test. A module still
        holding it raises "unable to open database file" the next time it is
        used -- in some unrelated test that never asked for a database of its
        own, and whose failure names none of this. Subclasses redirect further
        repositories in their own setUp, so rather than trust each one to undo
        its own work, every module still pointing at this test's database is
        found and handed the real one back.
        """
        test_db = getattr(self, "db", None)
        if test_db is None:
            return
        for module in list(sys.modules.values()):
            # vars(), not getattr(): getattr runs the attribute protocol, and a
            # module with a lazy __getattr__ (cv2, for one) answers it by loading
            # a native library that is not installed here. Reading the module's
            # dict asks the same question without asking the module anything.
            try:
                namespace = vars(module)
            except TypeError:
                continue
            if namespace.get("db", None) is test_db:
                module.db = REAL_DB

    def _create_test_database(self, db_path: Path) -> Database:
        # Reset singleton instance to ensure fresh database for each test
        Database._instance = None

        # Create new database instance
        db = Database()
        db.db_path = db_path
        db.db_path.parent.mkdir(exist_ok=True)
        db._initialized = True  # Mark as initialized to avoid conflicts

        from tests.fixtures.db_template import copy_template_db
        copy_template_db(db_path)

        # Repositories resolve `db` at call time, so redirecting the one
        # canonical name reaches every one of them.
        importlib.import_module("src.platform.database.database").db = db

        return db
    
    def create_test_user(self, user_id: str = "test_user", username: str = "testuser",
                        email: str = "test@example.com") -> str:
        """Create a test user and return the user_id"""
        with self.db.get_cursor() as cursor:
            # Check what columns exist in users table
            cursor.execute("PRAGMA table_info(users)")
            columns = [row[1] for row in cursor.fetchall()]
            print(f"Users table columns: {columns}")
            
            if 'account_type' in columns:
                cursor.execute("""
                    INSERT INTO users (id, username, email, password_hash, account_type)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, username, email, "test_hash", "USER"))
            else:
                cursor.execute("""
                    INSERT INTO users (id, username, email, password_hash)
                    VALUES (?, ?, ?, ?)
                """, (user_id, username, email, "test_hash"))
        return user_id
    
    def create_test_generation(self, generation_id: str = "test_gen", user_id: str = "test_user",
                              preset_id: str = "test_preset", form_data: dict = None) -> str:
        """Create a test generation and return the generation_id"""
        if form_data is None:
            form_data = {"prompt": "test prompt"}
        
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generations (id, preset_id, form_data, user_id)
                VALUES (?, ?, ?, ?)
            """, (generation_id, preset_id, str(form_data).replace("'", '"'), user_id))
        return generation_id
    
    def create_test_file(self, file_id: str = "test_file", user_id: str = "test_user",
                        file_path: str = "/test/path.jpg", file_type: str = "image") -> str:
        """Create a test file and return the file_id"""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO files (id, file_path, file_type, user_id)
                VALUES (?, ?, ?, ?)
            """, (file_id, file_path, file_type, user_id))
        return file_id