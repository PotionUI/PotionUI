"""Regression for the package-level `db` re-export hole (see
tests/architecture/test_db_import_hole.py).

`from src.platform.database import db` - the package-level re-export - froze
the singleton handle at first import exactly like the submodule-level import
did, and the two bindings were independent: patching
`src.platform.database.database.db` never reached a module that had imported
the package-level name. Repositories now import `db` at call time, and the
re-exporting packages resolve it through a PEP 562 `__getattr__` rather than
binding it, so re-pointing the canonical handle is picked up on the very next
call.
"""

import importlib
from unittest.mock import patch

import pytest

from src.features.keybindings.repository import KeybindingRepository

# Every module that re-exports `db` for someone else to import.
REEXPORTING_MODULES = [
    "src.platform.database",
    "src.plugin_api",
    "src.plugin_api.storage",
]


def _second_database():
    from src.platform.database.migration_runner import MigrationRunner
    from tests.conftest import TestDatabase

    second_db = TestDatabase()
    with patch("src.platform.database.database.db", second_db), \
         patch("src.platform.database.migration_runner.db", second_db):
        MigrationRunner().run_migrations()
    return second_db


def _insert_default(database, action_id):
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO keybinding_defaults "
            "(id, key, modifiers, label, category, context, description, enabled, source, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (action_id, "k", "[]", "Label", "cat", "ctx", "desc", 1, "system", 0),
        )


@pytest.mark.parametrize("module_name", REEXPORTING_MODULES)
def test_reexport_is_not_bound_at_import_time(module_name):
    """A bound name would be a snapshot; the absence of `db` from the
    module's own namespace is what forces every access through
    `__getattr__`."""
    module = importlib.import_module(module_name)
    assert "db" not in vars(module), (
        f"{module_name} binds `db` in its own namespace again - that snapshot "
        "is the frozen handle this guard exists to prevent"
    )


@pytest.mark.parametrize("module_name", REEXPORTING_MODULES)
def test_reexport_follows_a_repointed_canonical_handle(module_name, mock_db):
    module = importlib.import_module(module_name)
    sentinel = object()
    with patch("src.platform.database.database.db", sentinel):
        assert module.db is sentinel, (
            f"{module_name}.db did not follow the re-pointed canonical handle"
        )


def test_converted_repository_reads_the_repointed_db(mock_db):
    """The behavioural point: a repository that used to import the
    package-level `db` at module level now follows a mid-test re-point."""
    second_db = _second_database()
    _insert_default(second_db, "only-in-second-db")

    repository = KeybindingRepository()

    assert "only-in-second-db" not in [d.id for d in repository.get_all_defaults()]

    with patch("src.platform.database.database.db", second_db):
        assert "only-in-second-db" in [d.id for d in repository.get_all_defaults()]


def test_write_lands_on_the_repointed_db_not_the_fixtures(mock_db):
    """The other half: the re-point has to redirect writes too, or a leaked
    write would still be landing in whatever db the module froze."""
    second_db = _second_database()

    with patch("src.platform.database.database.db", second_db):
        _insert_default(second_db, "written-under-repoint")
        assert "written-under-repoint" in [
            d.id for d in KeybindingRepository().get_all_defaults()
        ]

    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM keybinding_defaults WHERE id = ?", ("written-under-repoint",)
        )
        assert cursor.fetchone() is None, "write leaked into the fixture's own db"
