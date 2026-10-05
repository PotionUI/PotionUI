import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from src.platform.database.database import Database

_MIGRATIONS = Path(__file__).resolve().parents[3] / "src" / "platform" / "database" / "migrations"


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration053Plans(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(temp.name) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("053_plans", self.db)

    def tearDown(self):
        Database._instance = None

    def _query(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def _columns(self, table):
        return {row["name"] for row in self._query(f"PRAGMA table_info({table})")}

    def test_up_creates_the_tables_columns_unlimited_plan_and_settings(self):
        self.migration.up()

        self.assertEqual(
            self._query("SELECT id, name, limits_json, is_system FROM plans"),
            [{"id": "unlimited", "name": "Unlimited", "limits_json": "[]", "is_system": 1}],
        )
        self.assertIn("plan_id", self._columns("users"))
        self.assertIn("plan_id", self._columns("user_groups"))
        self.assertTrue({"user_id", "kind", "units", "ref_id", "refunded_at"} <= self._columns("limit_events"))
        settings = {row["key"]: row["value"] for row in self._query("SELECT key, value FROM settings WHERE key LIKE 'plans_%'")}
        self.assertEqual(settings, {
            "plans_exempt_admins": "true",
            "plans_day_timezone": "UTC",
            "plans_contact_line": "Ask your admin for more.",
        })

    def test_up_is_idempotent_and_down_removes_everything(self):
        self.migration.up()
        self.migration.up()

        self.assertEqual(len(self._query("SELECT id FROM plans")), 1)
        self.migration.down()

        self.assertNotIn("plan_id", self._columns("users"))
        self.assertNotIn("plan_id", self._columns("user_groups"))
        tables = {row["name"] for row in self._query("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertFalse({"plans", "limit_events"} & tables)
        self.assertEqual(self._query("SELECT key FROM settings WHERE key LIKE 'plans_%'"), [])
