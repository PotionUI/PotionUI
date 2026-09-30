import builtins
import importlib.util
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.platform.database.database import Database
from src.platform.util.ids import generate_ulid
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
    repo_root = tmp_path_factory.mktemp("pre042-root")
    path = tmp_path_factory.mktemp("pre042-db") / "pre042.sqlite"
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
        migration = _load_migration("042_model_type_classification", database)
        yield database, migration
    Database._instance = None


def _columns(database, table):
    with database.get_cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table})")
        return {row["name"] for row in cursor.fetchall()}


def _tables(database):
    with database.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row["name"] for row in cursor.fetchall()}


def _flags(database):
    with database.get_cursor() as cursor:
        cursor.execute("SELECT root_id, model_type, scan_headers FROM model_root_bindings")
        return {(row["root_id"], row["model_type"]): row["scan_headers"] for row in cursor.fetchall()}


def _add_binding(database, root_id, model_type, subdir):
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_root_bindings (root_id, model_type, subdir, position, is_write) VALUES (?, ?, ?, 50, 0)",
            (root_id, model_type, subdir),
        )


def _add_root(database, root_id):
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_roots (id, label, path, path_key, kind, read_only, case_insensitive, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'library', 0, 0, 'online', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (root_id, root_id, f"/x/{root_id}", f"/x/{root_id}"),
        )


def test_up_adds_the_columns_and_tables(env):
    database, migration = env
    migration.up()

    assert "scan_headers" in _columns(database, "model_root_bindings")
    assert "type_source" in _columns(database, "models")
    assert {"model_header_verdicts", "model_type_assertions"} <= _tables(database)


def test_existing_models_default_to_the_folder_source(env):
    database, migration = env
    with database.get_cursor() as cursor:
        cursor.execute("INSERT INTO models (id, filename, model_type) VALUES (?, 'a.safetensors', 'lora')", (generate_ulid(),))

    migration.up()

    with database.get_cursor() as cursor:
        cursor.execute("SELECT type_source FROM models")
        assert cursor.fetchone()["type_source"] == "folder"


def test_home_bindings_seed_only_the_unet_folder(env):
    database, migration = env
    migration.up()

    flags = _flags(database)
    assert flags[("home", "unet")] == 1
    assert flags[("home", "checkpoint")] == 0
    assert flags[("home", "diffusion_model")] == 0
    assert flags[("home", "lora")] == 0


@pytest.mark.parametrize(
    ("subdir", "expected"),
    [
        ("Stable-diffusion", 1),
        ("stable-diffusion", 1),
        ("STABLE-DIFFUSION", 1),
        ("models/Stable-diffusion", 1),
        ("models\\Stable-diffusion", 1),
        ("UNet", 1),
        ("checkpoints", 0),
        ("my-unet", 0),
        ("", 0),
    ],
)
def test_seed_matches_the_last_folder_segment_case_insensitively(env, subdir, expected):
    database, migration = env
    _add_root(database, "r1")
    _add_binding(database, "r1", "checkpoint", subdir)

    migration.up()

    assert _flags(database)[("r1", "checkpoint")] == expected


def test_seed_never_enables_types_outside_the_header_classified_set(env):
    database, migration = env
    _add_root(database, "r1")
    _add_binding(database, "r1", "lora", "Stable-diffusion")

    migration.up()

    assert _flags(database)[("r1", "lora")] == 0


def test_a_second_run_keeps_what_an_admin_toggled(env):
    database, migration = env
    _add_root(database, "r1")
    _add_binding(database, "r1", "checkpoint", "Stable-diffusion")
    migration.up()
    with database.get_cursor() as cursor:
        cursor.execute("UPDATE model_root_bindings SET scan_headers = 0 WHERE root_id = 'r1'")
        cursor.execute("UPDATE model_root_bindings SET scan_headers = 1 WHERE root_id = 'home' AND model_type = 'checkpoint'")

    migration.up()

    flags = _flags(database)
    assert flags[("r1", "checkpoint")] == 0
    assert flags[("home", "checkpoint")] == 1


def test_down_removes_everything_up_added(env):
    database, migration = env
    migration.up()

    migration.down()

    assert "scan_headers" not in _columns(database, "model_root_bindings")
    assert "type_source" not in _columns(database, "models")
    assert not {"model_header_verdicts", "model_type_assertions"} & _tables(database)


def test_down_is_safe_twice_and_up_can_follow(env):
    database, migration = env
    migration.up()
    migration.down()
    migration.down()

    migration.up()

    assert "scan_headers" in _columns(database, "model_root_bindings")


def test_up_touches_no_files(env):
    database, migration = env

    def guarded(file, *args, **kwargs):
        raise AssertionError(f"migration opened {file}")

    with patch.object(builtins, "open", guarded), patch.object(Path, "iterdir", side_effect=AssertionError), patch(
        "os.walk", side_effect=AssertionError
    ):
        migration.up()



def test_assertions_reject_an_unknown_source(env):
    database, migration = env
    migration.up()

    with pytest.raises(Exception):
        with database.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO model_type_assertions (sha256, model_type, source, set_at) "
                "VALUES ('a', 'checkpoint', 'guess', CURRENT_TIMESTAMP)"
            )
