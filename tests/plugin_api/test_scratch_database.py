import importlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import src.platform.database.testing as database_testing
import src.plugin_api as plugin_api
import src.plugin_api.storage as plugin_storage
from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from src.platform.settings.records import SettingValueType
from src.plugin_api import SettingRepository, scratch_database, use_database

REPO_ROOT = Path(__file__).resolve().parents[2]

IMPORT_SCRIPT = """
import sys
import src.plugin_api as api

def loaded():
    watched = ("src.plugin_api.testing", "src.platform.database.testing")
    return ",".join(sorted(m for m in sys.modules if m in watched or m.split(".")[0] in ("pytest", "_pytest", "tests")))

print("IMPORTED:" + loaded())
api.scratch_database
print("ASKED:" + loaded())
"""


def _current():
    canonical = ("src.platform.database.database", "src.platform.database.migration_runner")
    return tuple(importlib.import_module(name).db for name in canonical) + (plugin_api.db, plugin_storage.db)


def _tables(database):
    with database.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        return {row["name"] for row in cursor.fetchall()}


def _create_note_table():
    from src.plugin_api import db

    with db.get_cursor() as cursor:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS example_plugin_notes ("
            "id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), text TEXT NOT NULL)"
        )


def _add_note(user_id, text):
    from src.plugin_api import db

    with db.get_cursor() as cursor:
        cursor.execute("INSERT INTO example_plugin_notes (user_id, text) VALUES (?, ?)", (user_id, text))


def _notes():
    from src.plugin_api import db

    with db.get_cursor() as cursor:
        cursor.execute("SELECT user_id, text FROM example_plugin_notes ORDER BY id")
        return [tuple(row) for row in cursor.fetchall()]


def _add_user(database, user_id):
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, ?, ?)",
            (user_id, f"name-{user_id}", f"{user_id}@test.com", "hash", "USER"),
        )


def test_a_scratch_database_carries_every_core_migration_and_is_not_yet_the_application_s(tmp_path):
    before = _current()
    instance = Database._instance
    path = tmp_path / "scratch.sqlite"

    database = scratch_database(path)

    assert isinstance(database, Database)
    assert database.db_path == path
    assert path.is_file()
    assert {"users", "settings", "plugins", "applied_migrations"} <= _tables(database)
    with database.get_cursor() as cursor:
        cursor.execute("SELECT migration_name FROM applied_migrations ORDER BY migration_name")
        applied = [row[0] for row in cursor.fetchall()]
    assert applied == MigrationRunner().get_available_migrations()
    assert _current() == before
    assert Database._instance is instance


def test_without_the_core_schema_a_scratch_database_starts_with_no_table(tmp_path):
    database = scratch_database(str(tmp_path / "nested" / "empty.sqlite"), core_schema=False)

    assert database.db_path == tmp_path / "nested" / "empty.sqlite"
    assert _tables(database) == set()


@pytest.mark.parametrize("core_schema", [True, False])
def test_a_scratch_database_refuses_a_file_that_already_exists(tmp_path, core_schema):
    path = tmp_path / "kept.sqlite"
    path.write_bytes(b"kept")

    with pytest.raises(FileExistsError, match="kept.sqlite"):
        scratch_database(path, core_schema=core_schema)

    assert path.read_bytes() == b"kept"


@pytest.mark.parametrize("core_schema", [True, False])
def test_two_scratch_databases_cannot_share_a_file_even_before_either_is_used(tmp_path, core_schema):
    path = tmp_path / "shared.sqlite"
    scratch_database(path, core_schema=core_schema)

    with pytest.raises(FileExistsError, match="shared.sqlite"):
        scratch_database(path, core_schema=core_schema)


@pytest.mark.parametrize("configured", [False, True])
def test_a_scratch_database_refuses_the_application_s_database_file_even_before_it_exists(
    tmp_path, monkeypatch, configured
):
    monkeypatch.chdir(tmp_path)
    if configured:
        path = tmp_path / "instance" / "db.sqlite"
        monkeypatch.setenv("POTIONUI_DB_PATH", str(path))
    else:
        path = tmp_path / "storage" / "db.sqlite"
        monkeypatch.delenv("POTIONUI_DB_PATH", raising=False)

    with pytest.raises(ValueError, match="application's database"):
        scratch_database(path)

    assert list(tmp_path.iterdir()) == []


def test_a_scratch_database_that_fails_to_fill_leaves_no_file_behind(tmp_path, monkeypatch):
    path = tmp_path / "scratch.sqlite"

    def fail(source, target):
        target.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copyfileobj", fail)
    with pytest.raises(OSError, match="disk full"):
        scratch_database(path)

    assert not path.exists()
    monkeypatch.undo()
    assert "users" in _tables(scratch_database(path))


def test_a_scratch_database_writes_nothing_but_its_file_and_the_template_it_copies(tmp_path, monkeypatch):
    temp = tmp_path / "temp"
    work = tmp_path / "work"
    temp.mkdir()
    work.mkdir()
    monkeypatch.setattr(database_testing, "_template_path", None)
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.chdir(work)
    before = _current()
    instance = Database._instance

    database = scratch_database(tmp_path / "tests" / "scratch.sqlite")

    templates = list(temp.iterdir())
    assert [template.name.startswith("potionui-test-db-template-") for template in templates] == [True]
    assert sorted(file.relative_to(tmp_path).as_posix() for file in tmp_path.rglob("*") if file.is_file()) == [
        f"temp/{templates[0].name}/template.sqlite",
        "tests/scratch.sqlite",
    ]
    assert list(work.iterdir()) == []
    assert _current() == before
    assert Database._instance is instance
    assert "users" in _tables(database)


def test_the_plugin_api_loads_the_testing_names_only_when_a_test_asks_for_them():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")] if p)
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_SCRIPT],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-2:] == [
        "IMPORTED:",
        "ASKED:src.platform.database.testing,src.plugin_api.testing",
    ]


def test_in_use_a_scratch_database_is_what_every_name_of_the_application_s_database_resolves_to(tmp_path):
    before = _current()
    database = scratch_database(tmp_path / "scratch.sqlite")

    with use_database(database) as current:
        assert current is database
        assert _current() == (database, database, database, database)

    assert _current() == before


def test_the_previous_database_comes_back_when_the_block_raises(tmp_path):
    before = _current()
    database = scratch_database(tmp_path / "scratch.sqlite", core_schema=False)

    with pytest.raises(RuntimeError, match="boom"):
        with use_database(database):
            raise RuntimeError("boom")

    assert _current() == before


def test_nested_scratch_databases_come_back_one_block_at_a_time(tmp_path):
    before = _current()
    outer = scratch_database(tmp_path / "outer.sqlite", core_schema=False)
    inner = scratch_database(tmp_path / "inner.sqlite", core_schema=False)

    with use_database(outer):
        with use_database(inner):
            assert plugin_api.db is inner
        assert plugin_api.db is outer

    assert _current() == before


def test_a_plugin_table_created_as_at_boot_can_reference_the_core_tables(tmp_path):
    with use_database(scratch_database(tmp_path / "scratch.sqlite")) as database:
        _create_note_table()
        _add_user(database, "user-1")
        _add_note("user-1", "kept")

        with pytest.raises(sqlite3.IntegrityError):
            _add_note("user-2", "refused")

        assert _notes() == [("user-1", "kept")]


def test_two_scratch_databases_share_nothing(tmp_path):
    first = scratch_database(tmp_path / "first.sqlite")
    second = scratch_database(tmp_path / "second.sqlite")

    with use_database(first):
        _create_note_table()

    assert "example_plugin_notes" in _tables(first)
    assert "example_plugin_notes" not in _tables(second)


def test_a_core_repository_reads_the_scratch_database_only_while_it_is_in_use(tmp_path, mock_db):
    repository = SettingRepository()
    database = scratch_database(tmp_path / "scratch.sqlite")

    with use_database(database):
        repository.create_setting("example.scratch_only", "1", SettingValueType.STRING)
        assert repository.get_setting_by_key("example.scratch_only").value == "1"

    assert plugin_api.db is mock_db
    assert repository.get_setting_by_key("example.scratch_only") is None
    with database.get_cursor() as cursor:
        cursor.execute("SELECT value FROM settings WHERE key = ?", ("example.scratch_only",))
        assert [row[0] for row in cursor.fetchall()] == ["1"]


def test_once_put_back_a_scratch_database_keeps_no_pooled_connection(tmp_path):
    database = scratch_database(tmp_path / "scratch.sqlite", core_schema=False)
    pooled = os.path.abspath(database.db_path)

    with use_database(database):
        _create_note_table()
        assert pooled in Database._pool._paths

    assert pooled not in Database._pool._paths
