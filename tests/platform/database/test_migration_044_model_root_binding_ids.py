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
    repo_root = tmp_path_factory.mktemp("pre044-root")
    path = tmp_path_factory.mktemp("pre044-db") / "pre044.sqlite"
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
            "043_model_type_assertion_sources",
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
        yield database, _load_migration("044_model_root_binding_ids", database)
    Database._instance = None


def _seed(database):
    with database.get_cursor() as cursor:
        cursor.execute("DELETE FROM model_locations")
        cursor.execute("DELETE FROM model_root_bindings")
        cursor.execute("DELETE FROM model_roots")
        for root_id, path, key, ci in (("r1", "/lib", "/lib", 0), ("r2", "/nas", "/nas", 1)):
            cursor.execute(
                "INSERT INTO model_roots (id, label, path, path_key, kind, read_only, case_insensitive, state) "
                "VALUES (?, ?, ?, ?, 'library', 0, ?, 'online')",
                (root_id, root_id, path, key, ci),
            )
        bindings = (
            ("r1", "lora", "loras", 0, 1, 0),
            ("r1", "checkpoint", "Stable-diffusion", 0, 1, 1),
            ("r2", "lora", "LoRA\\Styles/", 1, 0, 0),
        )
        cursor.executemany(
            "INSERT INTO model_root_bindings (root_id, model_type, subdir, position, is_write, scan_headers) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            bindings,
        )
        for model_id, sha in (("m1", "a"), ("m2", "b"), ("m3", "c")):
            cursor.execute(
                "INSERT INTO models (id, filename, file_size, sha256, model_type, is_available, is_directory) "
                "VALUES (?, ?, 1, ?, 'lora', 1, 0)",
                (model_id, f"{model_id}.safetensors", sha),
            )
        locations = (
            ("l1", "m1", "r1", "lora", "a.safetensors", "a.safetensors", 5, 11, "a", "present", "2026-01-01"),
            ("l2", "m2", "r1", "checkpoint", "b.safetensors", "b.safetensors", 6, 12, "b", "missing", "2026-01-02"),
            ("l3", "m3", "r2", "lora", "c.safetensors", "c.safetensors", 7, 13, "c", "present", "2026-01-03"),
            ("l4", "m3", "r2", "vae", "orphan.safetensors", "orphan.safetensors", 8, 14, "d", "present", "2026-01-04"),
        )
        cursor.executemany(
            "INSERT INTO model_locations "
            "(id, model_id, root_id, model_type, rel_path, rel_key, size, mtime_ns, sha256, status, seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            locations,
        )


def _bindings(database):
    with database.get_cursor() as cursor:
        cursor.execute("SELECT * FROM model_root_bindings ORDER BY root_id, model_type")
        return [dict(row) for row in cursor.fetchall()]


def _locations(database):
    with database.get_cursor() as cursor:
        cursor.execute("SELECT * FROM model_locations ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]


def _columns(database, table):
    with database.get_cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table})")
        return {row["name"] for row in cursor.fetchall()}


def test_bindings_get_ids_and_keys_and_keep_their_flags(env):
    database, migration = env
    _seed(database)

    migration.up()

    rows = {(b["root_id"], b["model_type"]): b for b in _bindings(database)}
    assert len({b["id"] for b in rows.values()}) == 3
    assert all(len(b["id"]) == 26 for b in rows.values())
    assert rows[("r1", "lora")]["subdir_key"] == "loras"
    assert rows[("r2", "lora")]["subdir_key"] == "lora/styles"
    assert rows[("r2", "lora")]["subdir"] == "LoRA\\Styles/"
    assert (rows[("r1", "lora")]["position"], rows[("r1", "lora")]["is_write"]) == (0, 1)
    assert rows[("r1", "checkpoint")]["scan_headers"] == 1
    assert rows[("r1", "lora")]["scan_headers"] == 0
    assert rows[("r2", "lora")]["is_write"] == 0


def test_locations_point_at_their_binding_and_orphans_are_dropped(env):
    database, migration = env
    _seed(database)

    migration.up()

    ids = {(b["root_id"], b["model_type"]): b["id"] for b in _bindings(database)}
    locations = {row["id"]: row for row in _locations(database)}
    assert set(locations) == {"l1", "l2", "l3"}
    assert locations["l1"]["binding_id"] == ids[("r1", "lora")]
    assert locations["l2"]["binding_id"] == ids[("r1", "checkpoint")]
    assert locations["l3"]["binding_id"] == ids[("r2", "lora")]
    assert (locations["l2"]["size"], locations["l2"]["mtime_ns"], locations["l2"]["status"]) == (6, 12, "missing")
    assert locations["l3"]["sha256"] == "c"
    assert locations["l3"]["seen_at"] == "2026-01-03"


def test_a_root_can_hold_two_folders_of_one_type_afterwards(env):
    database, migration = env
    _seed(database)
    migration.up()

    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_root_bindings (id, root_id, model_type, subdir, subdir_key, position, is_write) "
            "VALUES ('b-new', 'r1', 'lora', 'LyCORIS', 'LyCORIS', 5, 0)"
        )
        with pytest.raises(Exception):
            cursor.execute(
                "INSERT INTO model_root_bindings (id, root_id, model_type, subdir, subdir_key, position, is_write) "
                "VALUES ('b-dup', 'r1', 'lora', 'LyCORIS', 'LyCORIS', 6, 0)"
            )


def test_the_same_relative_name_can_live_in_two_bindings(env):
    database, migration = env
    _seed(database)
    migration.up()
    binding = next(b for b in _bindings(database) if (b["root_id"], b["model_type"]) == ("r1", "lora"))

    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_root_bindings (id, root_id, model_type, subdir, subdir_key, position, is_write) "
            "VALUES ('b-new', 'r1', 'lora', 'LyCORIS', 'LyCORIS', 5, 0)"
        )
        cursor.execute(
            "INSERT INTO model_locations (id, model_id, root_id, model_type, rel_path, rel_key, binding_id) "
            "VALUES ('l-new', 'm2', 'r1', 'lora', 'a.safetensors', 'a.safetensors', 'b-new')"
        )
        with pytest.raises(Exception):
            cursor.execute(
                "INSERT INTO model_locations (id, model_id, root_id, model_type, rel_path, rel_key, binding_id) "
                f"VALUES ('l-dup', 'm2', 'r1', 'lora', 'a.safetensors', 'a.safetensors', '{binding['id']}')"
            )


def test_position_and_single_write_invariants_survive(env):
    database, migration = env
    _seed(database)
    migration.up()

    with database.get_cursor() as cursor:
        with pytest.raises(Exception):
            cursor.execute(
                "INSERT INTO model_root_bindings (id, root_id, model_type, subdir, subdir_key, position, is_write) "
                "VALUES ('x1', 'r2', 'lora', 'other', 'other', 0, 0)"
            )
        with pytest.raises(Exception):
            cursor.execute(
                "INSERT INTO model_root_bindings (id, root_id, model_type, subdir, subdir_key, position, is_write) "
                "VALUES ('x2', 'r2', 'lora', 'other', 'other', 9, 1)"
            )


def test_deleting_a_binding_deletes_its_locations(env):
    database, migration = env
    _seed(database)
    migration.up()
    binding = next(b for b in _bindings(database) if (b["root_id"], b["model_type"]) == ("r2", "lora"))

    with database.get_cursor() as cursor:
        cursor.execute("DELETE FROM model_root_bindings WHERE id = ?", (binding["id"],))

    assert {row["id"] for row in _locations(database)} == {"l1", "l2"}


def test_roots_gain_a_null_layout_profile(env):
    database, migration = env
    _seed(database)

    migration.up()

    with database.get_cursor() as cursor:
        cursor.execute("SELECT id, layout_profile FROM model_roots")
        assert {row["id"]: row["layout_profile"] for row in cursor.fetchall()} == {"r1": None, "r2": None}


def test_a_second_run_changes_nothing(env):
    database, migration = env
    _seed(database)
    migration.up()
    bindings = _bindings(database)
    locations = _locations(database)

    migration.up()

    assert _bindings(database) == bindings
    assert _locations(database) == locations


def test_down_restores_the_old_shape_and_keeps_the_first_binding_of_each_type(env):
    database, migration = env
    _seed(database)
    migration.up()
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_root_bindings (id, root_id, model_type, subdir, subdir_key, position, is_write) "
            "VALUES ('b-new', 'r1', 'lora', 'LyCORIS', 'LyCORIS', 5, 0)"
        )
        cursor.execute(
            "INSERT INTO model_locations (id, model_id, root_id, model_type, rel_path, rel_key, binding_id) "
            "VALUES ('l-new', 'm2', 'r1', 'lora', 'z.safetensors', 'z.safetensors', 'b-new')"
        )

    migration.down()

    assert "id" not in _columns(database, "model_root_bindings")
    assert "binding_id" not in _columns(database, "model_locations")
    assert "layout_profile" not in _columns(database, "model_roots")
    rows = {(b["root_id"], b["model_type"]): b for b in _bindings(database)}
    assert set(rows) == {("r1", "lora"), ("r1", "checkpoint"), ("r2", "lora")}
    assert rows[("r1", "lora")]["subdir"] == "loras"
    assert rows[("r1", "checkpoint")]["scan_headers"] == 1
    assert {row["id"] for row in _locations(database)} == {"l1", "l2", "l3"}


def test_up_after_down_round_trips(env):
    database, migration = env
    _seed(database)
    migration.up()
    migration.down()

    migration.up()

    assert {row["id"] for row in _locations(database)} == {"l1", "l2", "l3"}
    assert len(_bindings(database)) == 3


def test_a_fresh_database_with_no_rows_migrates(env):
    database, migration = env
    with database.get_cursor() as cursor:
        cursor.execute("DELETE FROM model_locations")
        cursor.execute("DELETE FROM model_root_bindings")

    migration.up()

    assert _bindings(database) == []
    assert "subdir_key" in _columns(database, "model_root_bindings")
