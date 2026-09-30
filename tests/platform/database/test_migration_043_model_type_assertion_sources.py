import builtins
import importlib.util
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.platform.database.database import Database
from tests.fixtures.persistence_base import make_lean_file_database

_MIGRATIONS = Path(__file__).resolve().parents[3] / "src" / "platform" / "database" / "migrations"


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


@pytest.fixture(scope="session")
def template(tmp_path_factory):
    repo_root = tmp_path_factory.mktemp("pre043-root")
    path = tmp_path_factory.mktemp("pre043-db") / "pre043.sqlite"
    Database._instance = None
    database = Database()
    database.db_path = path
    database._initialized = True
    make_lean_file_database(database)
    with patch("pathlib.Path.cwd", return_value=repo_root):
        for stem in (
            "001_baseline",
            "035_model_roots",
            "036_native_availability_logical_refs",
            "037_drop_model_file_path",
            "042_model_type_classification",
        ):
            _load_migration(stem, database).up()
    with database.get_connection() as conn:
        conn.execute("PRAGMA journal_mode = DELETE").close()
    Database._instance = None
    return path


@pytest.fixture
def env(template, tmp_path):
    Database._instance = None
    database = Database()
    database.db_path = tmp_path / "test.sqlite"
    database._initialized = True
    shutil.copyfile(template, database.db_path)
    make_lean_file_database(database)
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        yield database, _load_migration("043_model_type_assertion_sources", database)
    Database._instance = None


def _seed(database, rows):
    with database.get_cursor() as cursor:
        cursor.executemany(
            "INSERT INTO model_type_assertions (sha256, model_type, source, set_at) VALUES (?, ?, ?, ?)",
            rows,
        )


def _rows(database):
    with database.get_cursor() as cursor:
        cursor.execute("SELECT sha256, model_type, source, set_at FROM model_type_assertions ORDER BY sha256, source")
        return [tuple(row) for row in cursor.fetchall()]


def _pk(database):
    with database.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(model_type_assertions)")
        return [r["name"] for r in sorted((r for r in cursor.fetchall() if r["pk"]), key=lambda r: r["pk"])]


ROWS = [
    ("a", "checkpoint", "admin", "2026-01-01T00:00:00"),
    ("b", "diffusion_model", "download", "2026-01-02T00:00:00"),
    ("c", "vae", "recipe", "2026-01-03T00:00:00"),
]


def test_the_key_becomes_the_hash_and_source_and_existing_rows_survive(env):
    database, migration = env
    _seed(database, ROWS)
    assert _pk(database) == ["sha256"]

    migration.up()

    assert _pk(database) == ["sha256", "source"]
    assert _rows(database) == ROWS


def test_a_hash_can_hold_one_row_per_source_afterwards(env):
    database, migration = env
    _seed(database, ROWS)
    migration.up()

    _seed(database, [("a", "diffusion_model", "download", "2026-02-01T00:00:00")])

    assert [r for r in _rows(database) if r[0] == "a"] == [
        ("a", "checkpoint", "admin", "2026-01-01T00:00:00"),
        ("a", "diffusion_model", "download", "2026-02-01T00:00:00"),
    ]
    with pytest.raises(Exception):
        _seed(database, [("a", "vae", "download", "2026-03-01T00:00:00")])


def test_the_source_check_survives(env):
    database, migration = env
    migration.up()

    with pytest.raises(Exception):
        _seed(database, [("z", "vae", "guess", "2026-03-01T00:00:00")])


def test_a_second_run_changes_nothing(env):
    database, migration = env
    _seed(database, ROWS)
    migration.up()
    _seed(database, [("a", "diffusion_model", "download", "2026-02-01T00:00:00")])
    before = _rows(database)

    migration.up()

    assert _rows(database) == before
    assert _pk(database) == ["sha256", "source"]


def test_down_keeps_the_highest_ranked_row_per_hash(env):
    database, migration = env
    _seed(database, ROWS)
    migration.up()
    _seed(
        database,
        [
            ("a", "diffusion_model", "download", "2026-02-01T00:00:00"),
            ("b", "lora", "recipe", "2026-02-02T00:00:00"),
            ("b", "vae", "admin", "2026-02-03T00:00:00"),
        ],
    )

    migration.down()

    assert _pk(database) == ["sha256"]
    assert _rows(database) == [
        ("a", "checkpoint", "admin", "2026-01-01T00:00:00"),
        ("b", "vae", "admin", "2026-02-03T00:00:00"),
        ("c", "vae", "recipe", "2026-01-03T00:00:00"),
    ]


def test_down_twice_and_up_again_round_trip(env):
    database, migration = env
    _seed(database, ROWS)
    migration.up()
    migration.down()
    migration.down()

    migration.up()

    assert _pk(database) == ["sha256", "source"]
    assert _rows(database) == ROWS


def test_neither_direction_touches_files(env):
    database, migration = env
    _seed(database, ROWS)

    def guarded(file, *args, **kwargs):
        raise AssertionError(f"migration opened {file}")

    with patch.object(builtins, "open", guarded), patch.object(Path, "iterdir", side_effect=AssertionError), patch(
        "os.walk", side_effect=AssertionError
    ):
        migration.up()
        migration.down()
