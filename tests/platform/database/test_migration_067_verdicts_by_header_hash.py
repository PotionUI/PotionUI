import shutil
import sqlite3
from unittest.mock import patch

import pytest

from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from src.platform.util.ids import generate_ulid
from tests.fixtures.db_template import build_template, template_db_path
from tests.fixtures.persistence_base import make_lean_file_database
from tests.platform.database._support import load_migration

NAME = "067_verdicts_by_header_hash"
FINGERPRINT = "fp"


@pytest.fixture(scope="session")
def previous_template(tmp_path_factory):
    path = tmp_path_factory.mktemp("pre067") / "pre067.sqlite"
    available = MigrationRunner.get_available_migrations
    with patch.object(
        MigrationRunner, "get_available_migrations", lambda self: [name for name in available(self) if name < NAME]
    ):
        build_template(path)
    return path


def _open(template, path):
    Database._instance = None
    database = Database()
    database.db_path = path
    database._initialized = True
    shutil.copyfile(template, path)
    make_lean_file_database(database)
    return database


@pytest.fixture
def env(previous_template, tmp_path):
    database = _open(previous_template, tmp_path / "test.sqlite")
    yield database, load_migration(NAME, database)
    Database._instance = None


@pytest.fixture
def fresh(tmp_path):
    database = _open(template_db_path(), tmp_path / "fresh.sqlite")
    yield database
    Database._instance = None


def _columns(database, table):
    with database.get_cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table})")
        return {row["name"] for row in cursor.fetchall()}


def _add_binding(database):
    root_id, binding_id = generate_ulid(), generate_ulid()
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_roots (id, label, path, path_key, kind, read_only, case_insensitive, state, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'library', 0, 0, 'online', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (root_id, root_id, f"/x/{root_id}", f"/x/{root_id}"),
        )
        cursor.execute(
            "INSERT INTO model_root_bindings (id, root_id, model_type, subdir, subdir_key, position, is_write) "
            "VALUES (?, ?, 'checkpoint', '', '', 50, 0)",
            (binding_id, root_id),
        )
    return root_id, binding_id


def _add_model(database, filename, sha256=None):
    model_id = generate_ulid()
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO models (id, filename, model_type, sha256) VALUES (?, ?, 'checkpoint', ?)",
            (model_id, filename, sha256),
        )
    return model_id


def _add_location(database, binding, model_id, rel_path, header_hash=None, status="present"):
    root_id, binding_id = binding
    columns = "id, model_id, binding_id, root_id, model_type, rel_path, rel_key, size, status, seen_at"
    values = [generate_ulid(), model_id, binding_id, root_id, "checkpoint", rel_path, rel_path, 10, status]
    with database.get_cursor() as cursor:
        cursor.execute(
            f"INSERT INTO model_locations ({columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)", values
        )
        if header_hash is not None:
            cursor.execute("UPDATE model_locations SET header_hash = ? WHERE id = ?", (header_hash, values[0]))


def _add_verdict(database, family, sha256=None, header_hash=None):
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_header_verdicts (sha256, header_hash, format, status, model_type, family, "
            "registry_fingerprint, classified_at) "
            "VALUES (?, ?, 'safetensors', 'decided', 'diffusion_model', ?, ?, CURRENT_TIMESTAMP)",
            (sha256, header_hash, family, FINGERPRINT),
        )


def _seed_old_verdict(database, sha256, family):
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_header_verdicts (sha256, format, status, model_type, family, registry_fingerprint, "
            "classified_at) VALUES (?, 'safetensors', 'decided', 'diffusion_model', ?, ?, CURRENT_TIMESTAMP)",
            (sha256, family, FINGERPRINT),
        )


def _verdict_families(database):
    with database.get_cursor() as cursor:
        cursor.execute("SELECT model_id, family FROM model_verdicts")
        return {row["model_id"]: row["family"] for row in cursor.fetchall()}


def test_the_previous_schema_keys_verdicts_by_digest_only(env):
    database, _ = env

    assert "header_hash" not in _columns(database, "model_header_verdicts")
    assert "header_hash" not in _columns(database, "model_locations")


def test_up_adds_the_header_hash_columns_and_the_model_verdict_view(env):
    database, migration = env

    migration.up()

    assert "header_hash" in _columns(database, "model_header_verdicts")
    assert "header_hash" in _columns(database, "model_locations")
    with database.get_cursor() as cursor:
        cursor.execute("SELECT type FROM sqlite_master WHERE name = 'model_verdicts'")
        assert cursor.fetchone()["type"] == "view"


def test_old_digest_keyed_verdicts_are_carried_over_and_stay_readable_through_their_model(env):
    database, migration = env
    with_verdict = _add_model(database, "a.safetensors", "a" * 64)
    without_verdict = _add_model(database, "b.safetensors", "b" * 64)
    _seed_old_verdict(database, "a" * 64, "flux")

    migration.up()

    assert _verdict_families(database) == {with_verdict: "flux"}
    assert without_verdict not in _verdict_families(database)
    with database.get_cursor() as cursor:
        cursor.execute("SELECT sha256, header_hash FROM model_header_verdicts")
        assert [tuple(row) for row in cursor.fetchall()] == [("a" * 64, None)]


def test_a_second_run_changes_nothing(env):
    database, migration = env
    model_id = _add_model(database, "a.safetensors", "a" * 64)
    _seed_old_verdict(database, "a" * 64, "flux")
    migration.up()

    migration.up()

    assert _verdict_families(database) == {model_id: "flux"}


def test_files_with_the_same_header_hash_share_one_verdict(env):
    database, migration = env
    migration.up()
    binding = _add_binding(database)
    first = _add_model(database, "a.safetensors", "a" * 64)
    second = _add_model(database, "b.safetensors", "b" * 64)
    _add_location(database, binding, first, "a.safetensors", header_hash="h1")
    _add_location(database, binding, second, "b.safetensors", header_hash="h1")
    _add_verdict(database, "flux", header_hash="h1")

    assert _verdict_families(database) == {first: "flux", second: "flux"}
    with database.get_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM model_header_verdicts")
        assert cursor.fetchone()["n"] == 1


def test_a_model_without_a_digest_reaches_its_verdict_through_its_location(env):
    database, migration = env
    migration.up()
    binding = _add_binding(database)
    pending = _add_model(database, "a.safetensors")
    _add_location(database, binding, pending, "a.safetensors", header_hash="h1")
    _add_verdict(database, "flux", header_hash="h1")

    assert _verdict_families(database) == {pending: "flux"}


def test_a_header_verdict_wins_over_an_old_digest_verdict(env):
    database, migration = env
    model_id = _add_model(database, "a.safetensors", "a" * 64)
    _seed_old_verdict(database, "a" * 64, "sd15")
    migration.up()
    binding = _add_binding(database)
    _add_location(database, binding, model_id, "a.safetensors", header_hash="h1")
    _add_verdict(database, "flux", header_hash="h1")

    assert _verdict_families(database) == {model_id: "flux"}


def test_a_present_location_decides_over_a_missing_one(env):
    database, migration = env
    migration.up()
    binding = _add_binding(database)
    model_id = _add_model(database, "a.safetensors")
    _add_location(database, binding, model_id, "a.safetensors", header_hash="old", status="missing")
    _add_location(database, binding, model_id, "z.safetensors", header_hash="new")
    _add_verdict(database, "sd15", header_hash="old")
    _add_verdict(database, "flux", header_hash="new")

    assert _verdict_families(database) == {model_id: "flux"}


def test_a_fresh_database_has_the_final_schema(fresh):
    assert "header_hash" in _columns(fresh, "model_header_verdicts")
    assert "header_hash" in _columns(fresh, "model_locations")
    assert _verdict_families(fresh) == {}
    _add_verdict(fresh, "flux", header_hash="h1")
    with pytest.raises(sqlite3.IntegrityError):
        _add_verdict(fresh, "sd15", header_hash="h1")
