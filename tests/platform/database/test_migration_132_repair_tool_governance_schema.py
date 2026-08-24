"""Migration 132 repairs a `tool_governance` table left behind by a
pre-per-config version of migration 126's `up()` (see the migration's
docstring): a table with `tool_name TEXT PRIMARY KEY` and no `llm_config_id`
column, even though `applied_migrations` already lists `126_add_tool_governance`
as applied so the runner will never re-run it.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from src.platform.database.database import Database

_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "src" / "platform" / "database" / "migrations"
)


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration132(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

    def tearDown(self):
        Database._instance = None

    def _create_pre_per_config_table(self):
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE tool_governance (
                    tool_name TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    locked INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def test_repairs_the_drifted_global_schema(self):
        self._create_pre_per_config_table()

        _load_migration("132_repair_tool_governance_schema", self.db).up()

        with self.db.get_connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tool_governance)")}
        self.assertIn("llm_config_id", columns)

    def test_per_config_insert_works_after_repair(self):
        self._create_pre_per_config_table()
        _load_migration("132_repair_tool_governance_schema", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at) "
                "VALUES ('cfg-a', 'search_gallery', 0, 1, '2026-08-18T00:00:00')"
            )
            conn.commit()
            row = conn.execute(
                "SELECT enabled, locked FROM tool_governance "
                "WHERE llm_config_id = 'cfg-a' AND tool_name = 'search_gallery'"
            ).fetchone()
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["locked"], 1)

    def test_drops_stale_rows_that_cannot_be_backfilled(self):
        self._create_pre_per_config_table()
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO tool_governance (tool_name, enabled, locked, updated_at) "
                "VALUES ('search_gallery', 0, 1, '2026-08-17T00:00:00')"
            )
            conn.commit()

        _load_migration("132_repair_tool_governance_schema", self.db).up()

        with self.db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM tool_governance").fetchone()["c"]
        self.assertEqual(count, 0)

    def test_up_is_idempotent(self):
        self._create_pre_per_config_table()
        migration = _load_migration("132_repair_tool_governance_schema", self.db)
        migration.up()
        migration.up()  # must not raise

        with self.db.get_connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tool_governance)")}
        self.assertIn("llm_config_id", columns)

    def test_noop_when_126_already_applied_with_current_schema(self):
        # Simulate an environment where 126 already produced the correct
        # per-config table (fresh install, or a DB that never drifted).
        _load_migration("126_add_tool_governance", self.db).up()

        migration = _load_migration("132_repair_tool_governance_schema", self.db)
        migration.up()  # must not raise or touch the table

        with self.db.get_connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tool_governance)")}
        self.assertIn("llm_config_id", columns)

    def test_noop_when_table_never_existed(self):
        migration = _load_migration("132_repair_tool_governance_schema", self.db)
        migration.up()  # must not raise

        with self.db.get_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertNotIn("tool_governance", tables)


if __name__ == '__main__':
    unittest.main()
