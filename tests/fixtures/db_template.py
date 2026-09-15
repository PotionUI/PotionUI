import importlib
import io
import shutil
import sqlite3
import tempfile
import threading
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional

from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner

_lock = threading.Lock()
_template_path: Optional[Path] = None


def checkpoint_wal_and_drop_journal_sidecar(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()


def _build_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    database_module = importlib.import_module("src.platform.database.database")
    migration_runner_module = importlib.import_module("src.platform.database.migration_runner")

    previous_instance = Database._instance
    previous_db = database_module.db
    previous_migration_db = migration_runner_module.db

    Database._instance = None
    template_db = Database()
    template_db.db_path = path
    template_db.db_path.parent.mkdir(exist_ok=True)
    template_db._initialized = True
    database_module.db = template_db
    migration_runner_module.db = template_db
    try:
        with redirect_stdout(io.StringIO()):
            MigrationRunner().run_migrations()
    finally:
        Database._instance = previous_instance
        database_module.db = previous_db
        migration_runner_module.db = previous_migration_db

    checkpoint_wal_and_drop_journal_sidecar(path)


def template_db_path() -> Path:
    global _template_path
    with _lock:
        if _template_path is None:
            directory = Path(tempfile.mkdtemp(prefix="potionui-test-db-template-"))
            path = directory / "template.sqlite"
            _build_template(path)
            _template_path = path
        return _template_path


def copy_template_db(dest_path: Path, *, template_path: Optional[Path] = None) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_path or template_db_path(), dest_path)


def load_template_into_connection(*, template_path: Optional[Path] = None) -> sqlite3.Connection:
    target = sqlite3.connect(":memory:", check_same_thread=False)
    source = sqlite3.connect(template_path or template_db_path())
    try:
        source.backup(target)
    finally:
        source.close()
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys = ON")
    target.execute("PRAGMA busy_timeout = 30000")
    return target
