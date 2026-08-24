import unittest
import tempfile
import os
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock
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
from src.platform.database.migration_runner import MigrationManager


class PersistenceTestBase(unittest.TestCase):
    """Base class for persistence tests with database setup"""
    
    def setUp(self):
        """Set up test database and run migrations"""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"
        
        # Create test database instance
        self.db = self._create_test_database(self.temp_db_path)
        
        # Run migrations to set up schema
        self._run_test_migrations()
    
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
        """Create a database instance for testing"""
        # Reset singleton instance to ensure fresh database for each test
        Database._instance = None

        # Create new database instance
        db = Database()
        db.db_path = db_path
        db.db_path.parent.mkdir(exist_ok=True)
        db._initialized = True  # Mark as initialized to avoid conflicts

        # Repositories bind `db` at import time, so each one that already did
        # holds its own reference and has to be redirected by name.
        for module_path in (
            "src.platform.database.database",
            "src.features.generation.file_repository",
            "src.features.generation.repository",
            "src.features.collections.repository",
        ):
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                continue
            module.db = db

        return db
    
    def _run_test_migrations(self):
        """Run all migrations for test database"""
        try:
            # Patch the migration_manager module's db reference to use our test database
            with patch('src.platform.database.migration_runner.db', self.db):
                # Create migration manager and run migrations
                migration_manager = MigrationManager()

                # Clear any existing migration records for clean slate
                with self.db.get_cursor() as cursor:
                    cursor.execute("DROP TABLE IF EXISTS applied_migrations")

                # Run migrations silently
                import sys, io
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    migration_manager.run_migrations()
                except Exception as e:
                    sys.stdout = old_stdout
                    print(f"Error running migrations: {e}")
                    import traceback
                    traceback.print_exc()
                    raise
                finally:
                    sys.stdout = old_stdout

                # Verify key tables exist
                self._verify_test_tables()

        except Exception as e:
            print(f"Error running migrations: {e}")
            # Fall back to basic table creation
            self._create_basic_test_tables()
    
    def _create_basic_test_tables(self):
        """Create basic tables for testing if migrations fail"""
        with self.db.get_cursor() as cursor:
            # Users table (match migration 007)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    account_type TEXT NOT NULL DEFAULT 'USER',
                    last_login TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CHECK (account_type IN ('USER', 'ADMIN'))
                )
            """)
            print("Created users table")
            
            # Generations table (match final schema after migrations)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS generations (
                    id TEXT PRIMARY KEY,
                    preset_id TEXT NOT NULL,
                    preset_version TEXT,
                    form_data TEXT NOT NULL,
                    user_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress REAL DEFAULT 0.0,
                    current_step TEXT,
                    current_step_num INTEGER DEFAULT 0,
                    total_steps INTEGER DEFAULT 0,
                    error_message TEXT,
                    output_directory TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            print("Created generations table")
            
            # Files table (match migration 010)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    file_size INTEGER,
                    pipe_name TEXT,
                    is_final BOOLEAN DEFAULT FALSE,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            print("Created files table")
            
            # Generation files junction table (match migration 010)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS generation_files (
                    id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
                    UNIQUE(generation_id, file_id)
                )
            """)
            print("Created generation_files table")
    
    def _verify_test_tables(self):
        """Verify that key tables exist after migration"""
        required_tables = ['users', 'generations', 'files', 'generation_files']
        
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = [row[0] for row in cursor.fetchall()]
            
            print(f"Existing tables: {existing_tables}")
            
            for table in required_tables:
                if table not in existing_tables:
                    raise Exception(f"Required table '{table}' not found after migrations")
    
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