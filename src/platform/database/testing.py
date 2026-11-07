import atexit
import importlib
import io
import os
import shutil
import sqlite3
import tempfile
import threading
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any, Iterator, Optional, Union

from src.platform.database.database import LIVE_DATABASE_PATH, Database
from src.platform.database.migration_runner import MigrationRunner
from src.platform.database.sql_functions import register_sql_functions

_REDIRECTED_MODULES = ("src.platform.database.database", "src.platform.database.migration_runner")
_lock = threading.Lock()
_template_path: Optional[Path] = None


def checkpoint_wal(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _database_at(path: Path) -> Database:
    database = object.__new__(Database)
    database.db_path = path
    database._initialized = True
    return database


@contextmanager
def use_database(database: Any) -> Iterator[Any]:
    modules = [importlib.import_module(name) for name in _REDIRECTED_MODULES]
    previous = [module.db for module in modules]
    for module in modules:
        module.db = database
    try:
        yield database
    finally:
        for module, replaced in zip(modules, previous):
            module.db = replaced
        Database.close_pool()


def _build_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    template = _database_at(path)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    register_sql_functions(connection)
    connection.execute("PRAGMA journal_mode = MEMORY").close()
    connection.execute("PRAGMA synchronous = OFF").close()
    connection.execute("PRAGMA foreign_keys = ON").close()

    @contextmanager
    def shared_connection():
        yield connection

    template.get_connection = shared_connection
    try:
        with use_database(template), redirect_stdout(io.StringIO()):
            MigrationRunner().run_migrations()
    finally:
        connection.close()

    checkpoint_wal(path)


def _remove_template_directory(directory: Path, owner_pid: int) -> None:
    if os.getpid() != owner_pid:
        return
    shutil.rmtree(directory, ignore_errors=True)


def template_db_path() -> Path:
    global _template_path
    with _lock:
        if _template_path is None:
            directory = Path(tempfile.mkdtemp(prefix="potionui-test-db-template-"))
            atexit.register(_remove_template_directory, directory, os.getpid())
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


def _same_file(first: Union[str, Path], second: Union[str, Path]) -> bool:
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def scratch_database(path: Union[str, Path], *, core_schema: bool = True) -> Database:
    path = Path(path)
    application = (LIVE_DATABASE_PATH, os.getenv("POTIONUI_DB_PATH", str(LIVE_DATABASE_PATH)))
    if any(_same_file(path, candidate) for candidate in application):
        raise ValueError(f"a scratch database cannot be the application's database file: {path}")
    template = template_db_path() if core_schema else None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        target = open(path, "xb")
    except FileExistsError:
        raise FileExistsError(f"a scratch database needs a file that does not exist yet: {path}") from None
    try:
        with target:
            if template is not None:
                with open(template, "rb") as source:
                    shutil.copyfileobj(source, target)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return _database_at(path)
