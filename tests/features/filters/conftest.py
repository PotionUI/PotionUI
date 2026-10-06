import importlib.util
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
import yaml

import src.features.filters.repository as repository_module
from src.features.filters.catalog import FilterCatalog
from src.features.filters.collaborators import FilterCollaborators
from src.features.filters.repository import UserFilterRepository
from src.platform.security.user import AccountType, User

MIGRATION = (
    Path(__file__).resolve().parents[3] / "src" / "platform" / "database" / "migrations" / "056_create_user_filters.py"
)
REPO_ROOT = Path(__file__).resolve().parents[3]

CUBE_2 = "LUT_3D_SIZE 2\n" + "\n".join(f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n"


def filter_data(filter_id: str = "demo", **overrides: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "schema": 1,
        "id": filter_id,
        "name": filter_id.title(),
        "group": "Colour",
        "order": 10,
        "steps": [{"op": "tone", "contrast": 10}],
    }
    data.update(overrides)
    return data


def write_filter(root: Path, filter_id: str = "demo", cube: Optional[str] = None, **overrides: Any) -> Path:
    directory = root / filter_id
    directory.mkdir(parents=True, exist_ok=True)
    data = filter_data(filter_id, **overrides)
    if cube is not None:
        data.setdefault("lut", "lut.cube")
        (directory / "lut.cube").write_text(cube, encoding="utf-8")
    (directory / "filter.yml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return directory


def plugin(plugin_id: str, plugin_dir: Path, filters: Optional[List[str]] = None, filter_ops: Optional[List[dict]] = None):
    return SimpleNamespace(
        id=plugin_id,
        plugin_dir=str(plugin_dir),
        filters=[{"path": p} for p in (filters or [])],
        filter_ops=filter_ops or [],
    )


def registry(enabled: List[Any], all_plugins: Optional[List[Any]] = None):
    return SimpleNamespace(
        get_enabled_plugins=lambda: list(enabled),
        get_all_plugins=lambda: list(all_plugins if all_plugins is not None else enabled),
    )


def make_user(user_id: str = "user-1", account_type=AccountType.USER) -> User:
    return User(username=user_id, email=f"{user_id}@example.test", password_hash="x", account_type=account_type, id=user_id)


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    class _Db:
        @contextmanager
        def get_cursor(self):
            cursor = conn.cursor()
            yield cursor
            conn.commit()

    spec = importlib.util.spec_from_file_location("user_filters_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.db = _Db()
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
    for user_id in ("user-1", "user-2"):
        conn.execute("INSERT INTO users (id) VALUES (?)", (user_id,))
    module.up()

    @contextmanager
    def _get_conn():
        yield conn

    with patch.object(repository_module, "get_database_connection", _get_conn):
        yield conn
    conn.close()


@pytest.fixture
def filters_dir(tmp_path):
    root = tmp_path / "filters"
    (root / "marketplace").mkdir(parents=True)
    (root / "local").mkdir()
    return root


@pytest.fixture
def catalog(filters_dir):
    return FilterCatalog(str(filters_dir))


@pytest.fixture
def collaborators(connection, catalog):
    return FilterCollaborators(repository=UserFilterRepository(), catalog=catalog)


@pytest.fixture
def user():
    return make_user()

