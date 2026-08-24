"""
Global test configuration and fixtures.

This module provides pytest fixtures for test database setup, storage isolation,
and other common testing utilities.
"""

import os
import pytest
import sqlite3
import tempfile
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import patch, Mock


def pytest_collection_modifyitems(config, items):
    """Gate opt-in test categories behind explicit environment variables.

    `requires_gpu` needs a real CUDA device AND explicit opt-in
    (`POTIONUI_GPU_TESTS=1`) even when one is present - this box's GPU is
    shared, so a plain `pytest tests/` must never touch it. `requires_models`
    needs real model weights on disk AND explicit opt-in
    (`POTIONUI_MODEL_TESTS=1`) - `models/checkpoints` etc. are symlinks into
    the production depot, and no test may read it by default.
    """
    gpu_ok = os.environ.get("POTIONUI_GPU_TESTS") == "1"
    if gpu_ok:
        import torch
        gpu_ok = torch.cuda.is_available()
    models_ok = os.environ.get("POTIONUI_MODEL_TESTS") == "1"

    skip_gpu = pytest.mark.skip(reason="requires_gpu: set POTIONUI_GPU_TESTS=1 on a CUDA host to run")
    skip_models = pytest.mark.skip(reason="requires_models: set POTIONUI_MODEL_TESTS=1 to run against real model files")

    for item in items:
        if "requires_gpu" in item.keywords and not gpu_ok:
            item.add_marker(skip_gpu)
        if "requires_models" in item.keywords and not models_ok:
            item.add_marker(skip_models)


class TestDatabase:
    """Test database that uses in-memory SQLite"""

    def __init__(self):
        self.db_path = ":memory:"
        self._connection = None
        self._initialize_connection()

    def _initialize_connection(self):
        """Initialize the database connection"""
        self._connection = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0
        )
        self._connection.row_factory = sqlite3.Row

        # For in-memory database, we don't need WAL mode
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with automatic cleanup"""
        try:
            yield self._connection
        finally:
            # Don't close the connection for in-memory database
            # as it would lose all data
            pass

    @contextmanager
    def get_cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Get a database cursor with automatic transaction management"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def close(self):
        """Close the database connection"""
        if self._connection:
            self._connection.close()
            self._connection = None


@pytest.fixture(scope="session", autouse=True)
def ephemeral_credential_key():
    """Give the whole test session a throwaway encryption key.

    Without this, the first test that touches a credential store resolves the
    key from disk - and, finding none, generates one under the real storage
    directory. Handing the session a key through the environment means nothing
    is ever written there. Tests that exercise key resolution itself clear these
    variables for their own scope.
    """
    from src.platform.security.secrets import ENV_KEY, configure_secret_cipher, generate_key

    previous = os.environ.get(ENV_KEY)
    os.environ[ENV_KEY] = generate_key().decode("ascii")
    configure_secret_cipher(None)
    yield
    configure_secret_cipher(None)
    if previous is None:
        os.environ.pop(ENV_KEY, None)
    else:
        os.environ[ENV_KEY] = previous


@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory):
    """
    Create a temporary database path for the test session.

    This fixture provides a temporary directory for SQLite database files
    that will be cleaned up after the test session.

    Returns:
        Path: Temporary database file path
    """
    temp_dir = tmp_path_factory.mktemp("test_db")
    db_path = temp_dir / "test.db"
    return str(db_path)


@pytest.fixture(scope="function")
def test_db():
    """
    Create a fresh in-memory test database for each test.

    This fixture provides an isolated database instance for each test,
    with the full schema created by running all migrations.

    Yields:
        TestDatabase: Fresh database instance with schema
    """
    test_database = TestDatabase()

    # Patch both database references to ensure migrations use test database
    with patch('src.platform.database.database.db', test_database), \
         patch('src.platform.database.migration_runner.db', test_database):
        # Run all migrations to create the schema
        from src.platform.database.migration_runner import MigrationManager
        migration_manager = MigrationManager()

        # Run migrations silently (suppress print statements)
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            migration_manager.run_migrations()
        except Exception as e:
            # Restore stdout and re-raise
            sys.stdout = old_stdout
            print(f"Migration error: {e}")
            raise
        finally:
            sys.stdout = old_stdout

    # Now yield with the database fully migrated
    yield test_database

    # Clean up
    test_database.close()


@pytest.fixture(scope="function")
def db(test_db):
    """
    Alias for test_db fixture for convenience.

    Provides a fresh database with auto-cleanup for each test.
    Use this fixture when you need a clean database state.

    Yields:
        TestDatabase: Fresh database instance with schema
    """
    yield test_db


@pytest.fixture(scope="function")
def mock_db():
    """
    Mock the global db instance to use test database.

    This fixture patches the global database instance so that all
    code using the global db will use the test database instead.
    Also runs all migrations to ensure schema is up to date.

    Yields:
        TestDatabase: Test database patched into global scope
    """
    test_database = TestDatabase()

    # Patch the database for the entire test duration. `database.db` and
    # `migration_runner.db` cover the two names that resolve `db` fresh on
    # every call; `settings.repository.db` is a THIRD, separate name — that
    # module binds `db` at its own top-level import (not deferred), so once
    # anything has imported it (collection alone is enough), patching
    # `database.db` never reaches it: SettingRepository keeps talking to
    # whatever `db` was at that first import, live database included, for the
    # rest of the process. Patched here too so `SettingsManager`/
    # `SettingRepository` under `mock_db` are actually isolated.
    with patch('src.platform.database.database.db', test_database), \
         patch('src.platform.database.migration_runner.db', test_database), \
         patch('src.platform.settings.repository.db', test_database):
        # Create a new migration manager instance with patched db
        from src.platform.database.migration_runner import MigrationManager
        migration_manager = MigrationManager()

        # Run migrations silently
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            migration_manager.run_migrations()
        except Exception as e:
            sys.stdout = old_stdout
            print(f"Migration error: {e}")
            raise
        finally:
            sys.stdout = old_stdout

        # Yield with patch still active
        yield test_database

    # Clean up after patch context exits
    test_database.close()


@pytest.fixture(scope="function")
def test_storage(tmp_path):
    """
    Provide a temporary storage directory for test files.

    Creates a temporary directory structure matching the production layout:
    - storage/generations/
    - storage/tmp/
    - storage/models/

    The directory is automatically cleaned up after the test.

    Args:
        tmp_path: pytest fixture providing temporary directory

    Returns:
        Path: Temporary storage directory path
    """
    storage_dir = tmp_path / "test_storage"
    storage_dir.mkdir()

    # Create subdirectories
    (storage_dir / "generations").mkdir()
    (storage_dir / "tmp").mkdir()
    (storage_dir / "models").mkdir()

    # Set environment variable so FileStore uses test storage
    old_storage_path = os.environ.get('POTIONUI_STORAGE_PATH')
    os.environ['POTIONUI_STORAGE_PATH'] = str(storage_dir)

    yield storage_dir

    # Restore original environment variable
    if old_storage_path is not None:
        os.environ['POTIONUI_STORAGE_PATH'] = old_storage_path
    elif 'POTIONUI_STORAGE_PATH' in os.environ:
        del os.environ['POTIONUI_STORAGE_PATH']

    # Cleanup is automatic via tmp_path


@pytest.fixture(scope="function")
def file_service(test_storage):
    """
    Provide a FileStore instance configured with test storage.

    This fixture creates a FileStore that uses isolated test storage,
    ensuring file operations don't affect production data.

    Args:
        test_storage: Temporary storage directory fixture

    Returns:
        FileStore: Configured service instance
    """
    from src.platform.filesystem.file_store import FileStore

    service = FileStore(base_storage_dir=str(test_storage))
    return service


@pytest.fixture
def mock_settings_manager():
    """
    Mock settings manager fixture for dependency injection.

    Provides a mock object that can be configured to return
    specific settings values for testing.

    Returns:
        Mock: Mock settings manager instance
    """
    return Mock()


# Import all fixtures from fixture modules so they're available to tests
pytest_plugins = [
    'tests.fixtures.generation_fixtures',
    'tests.fixtures.user_fixtures',
    'tests.fixtures.preset_fixtures',
    'tests.fixtures.image_fixtures',
    'tests.fixtures.mesh_fixtures',
    'tests.fixtures.audio_fixtures',
]