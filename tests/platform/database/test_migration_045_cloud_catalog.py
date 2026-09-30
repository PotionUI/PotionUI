import importlib.util
import sqlite3
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


class TestMigration045CloudCatalog(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp_dir = temp.name
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("045_cloud_catalog", self.db)

    def tearDown(self):
        Database._instance = None

    def _tables(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            return {row["name"] for row in cursor.fetchall()}

    def _columns(self, table):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table})")
            return {row["name"]: row for row in cursor.fetchall()}

    def _add_backend(self, backend_id="b1"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO backends (id, name, engine, driver) VALUES (?, 'B', 'cloud', 'cloud.fake')",
                (backend_id,),
            )

    def _add_entry(self, backend_id="b1", slug="fake~a"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO cloud_catalog (backend_id, slug, provider_model_id, label, spec, discovered_at, refreshed_at) "
                "VALUES (?, ?, 'fake/a', 'A', '{}', 't', 't')",
                (backend_id, slug),
            )

    def test_creates_the_catalog_and_its_state_table(self):
        self.migration.up()

        assert {"cloud_catalog", "cloud_catalog_state"} <= self._tables()
        assert set(self._columns("cloud_catalog")) == {
            "backend_id", "slug", "provider_model_id", "label", "vendor", "tasks", "outputs", "spec",
            "spec_version", "enabled", "suggested", "deprecated_at", "discovered_at", "refreshed_at",
            "missing_since", "enabled_at",
        }
        assert set(self._columns("cloud_catalog_state")) == {"backend_id", "refreshed_at", "listed", "skipped"}

    def test_a_new_entry_is_disabled_and_not_suggested_by_default(self):
        self.migration.up()
        self._add_backend()
        self._add_entry()

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT enabled, suggested, missing_since, spec_version FROM cloud_catalog")
            row = cursor.fetchone()
        assert (row["enabled"], row["suggested"], row["missing_since"], row["spec_version"]) == (0, 0, None, 1)

    def test_a_backend_holds_a_slug_once(self):
        self.migration.up()
        self._add_backend()
        self._add_entry()

        with self.assertRaises(sqlite3.IntegrityError):
            self._add_entry()

    def test_two_backends_may_hold_the_same_slug(self):
        self.migration.up()
        self._add_backend("b1")
        self._add_backend("b2")
        self._add_entry("b1")
        self._add_entry("b2")

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM cloud_catalog")
            assert cursor.fetchone()["n"] == 2

    def test_deleting_a_backend_removes_its_catalog_and_state(self):
        self.migration.up()
        self._add_backend()
        self._add_entry()
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO cloud_catalog_state (backend_id, refreshed_at) VALUES ('b1', 't')")

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM backends WHERE id = 'b1'")

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT (SELECT COUNT(*) FROM cloud_catalog) AS a, (SELECT COUNT(*) FROM cloud_catalog_state) AS b")
            row = cursor.fetchone()
        assert (row["a"], row["b"]) == (0, 0)

    def test_up_twice_keeps_the_existing_rows(self):
        self.migration.up()
        self._add_backend()
        self._add_entry()

        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM cloud_catalog")
            assert cursor.fetchone()["n"] == 1

    def test_down_drops_both_tables(self):
        self.migration.up()

        self.migration.down()

        assert not {"cloud_catalog", "cloud_catalog_state"} & self._tables()
