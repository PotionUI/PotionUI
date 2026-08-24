"""Migration 126 adds `tool_governance` (per-LLM-config tool enable/lock,
keyed by (llm_config_id, tool_name)) and `user_disabled_tools` (a global
per-user opt-out set) - see the migration's docstring.
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


class TestMigration126(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

    def tearDown(self):
        Database._instance = None

    def _tables(self, conn):
        return {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    def test_up_creates_both_tables(self):
        _load_migration("126_add_tool_governance", self.db).up()

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
        self.assertIn("tool_governance", tables)
        self.assertIn("user_disabled_tools", tables)

    def test_up_creates_the_user_index(self):
        _load_migration("126_add_tool_governance", self.db).up()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_user_disabled_tools_user'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_up_is_idempotent(self):
        migration = _load_migration("126_add_tool_governance", self.db)
        migration.up()
        migration.up()  # must not raise "table already exists"

    def test_tool_governance_row_round_trips(self):
        _load_migration("126_add_tool_governance", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at) "
                "VALUES ('cfg-a', 'search_gallery', 0, 1, '2026-08-17T00:00:00')"
            )
            conn.commit()
            row = conn.execute(
                "SELECT enabled, locked FROM tool_governance "
                "WHERE llm_config_id = 'cfg-a' AND tool_name = 'search_gallery'"
            ).fetchone()
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["locked"], 1)

    def test_tool_governance_pk_prevents_duplicates_for_the_same_config(self):
        _load_migration("126_add_tool_governance", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at) "
                "VALUES ('cfg-a', 'search_gallery', 1, 0, '2026-08-17T00:00:00')"
            )
            conn.commit()
            with self.assertRaises(Exception):
                conn.execute(
                    "INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at) "
                    "VALUES ('cfg-a', 'search_gallery', 0, 1, '2026-08-17T00:00:01')"
                )

    def test_same_tool_governed_independently_per_config(self):
        _load_migration("126_add_tool_governance", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at) "
                "VALUES ('cfg-a', 'search_gallery', 0, 0, '2026-08-17T00:00:00')"
            )
            conn.execute(
                "INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at) "
                "VALUES ('cfg-b', 'search_gallery', 1, 0, '2026-08-17T00:00:00')"
            )
            conn.commit()
            rows = {
                row["llm_config_id"]: row["enabled"]
                for row in conn.execute(
                    "SELECT llm_config_id, enabled FROM tool_governance WHERE tool_name = 'search_gallery'"
                )
            }
        self.assertEqual(rows, {"cfg-a": 0, "cfg-b": 1})

    def test_user_disabled_tools_pk_prevents_duplicates(self):
        _load_migration("126_add_tool_governance", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO user_disabled_tools (user_id, tool_name, created_at) "
                "VALUES ('u1', 'search_gallery', '2026-08-17T00:00:00')"
            )
            conn.commit()
            with self.assertRaises(Exception):
                conn.execute(
                    "INSERT INTO user_disabled_tools (user_id, tool_name, created_at) "
                    "VALUES ('u1', 'search_gallery', '2026-08-17T00:00:01')"
                )

    def test_down_drops_both_tables(self):
        migration = _load_migration("126_add_tool_governance", self.db)
        migration.up()

        migration.down()

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
        self.assertNotIn("tool_governance", tables)
        self.assertNotIn("user_disabled_tools", tables)

    def test_down_is_idempotent(self):
        migration = _load_migration("126_add_tool_governance", self.db)
        migration.up()
        migration.down()
        migration.down()  # must not raise


if __name__ == '__main__':
    unittest.main()
