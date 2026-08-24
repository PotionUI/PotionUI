"""GenerationRunReportRepository against a real scratch SQLite database.

Only migrations 002 (creates `generations`, the FK target) and 117 (creates
`generation_run_reports`) are run - the repository under test needs nothing
else from the 121-migration chain.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from src.platform.database.database import Database, db as REAL_DB
from src.features.generation.run_report_repository import GenerationRunReportRepository
import src.features.generation.run_report_repository as run_report_repository_module

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


class TestGenerationRunReportRepository(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

        _load_migration("002_create_generations", self.db).up()
        _load_migration("117_add_generation_run_reports", self.db).up()

        run_report_repository_module.db = self.db
        self.repo = GenerationRunReportRepository()

        self._insert_generation("gen-1")
        self._insert_generation("gen-2")

    def tearDown(self):
        # A dangling reference to this test's (about-to-vanish) temp database
        # would break the next test that imports this module fresh in the
        # same process - restore the real singleton, matching
        # PersistenceTestBase._restore_patched_db.
        run_report_repository_module.db = REAL_DB
        Database._instance = None

    def _insert_generation(self, generation_id):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generations (id, preset_name, form_data) VALUES (?, ?, ?)",
                (generation_id, "preset", "{}"),
            )
            conn.commit()

    def test_save_and_get_roundtrip(self):
        report = {"schema_version": 1, "status_history": [{"step": "a"}]}
        self.repo.save("gen-1", report)
        self.assertEqual(self.repo.get("gen-1"), report)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.repo.get("does-not-exist"))

    def test_save_upserts_rather_than_duplicates(self):
        self.repo.save("gen-1", {"v": 1})
        self.repo.save("gen-1", {"v": 2})
        self.assertEqual(self.repo.get("gen-1"), {"v": 2})

        with self.db.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM generation_run_reports WHERE generation_id = ?",
                ("gen-1",),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_exists_bulk_returns_only_saved_ids(self):
        self.repo.save("gen-1", {"v": 1})
        result = self.repo.exists_bulk(["gen-1", "gen-2", "gen-missing"])
        self.assertEqual(result, {"gen-1"})

    def test_exists_bulk_empty_input_returns_empty_set(self):
        self.assertEqual(self.repo.exists_bulk([]), set())

    def test_deleting_the_generation_cascades_to_its_report(self):
        self.repo.save("gen-2", {"v": 1})

        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM generations WHERE id = ?", ("gen-2",))
            conn.commit()

        self.assertIsNone(self.repo.get("gen-2"))


if __name__ == '__main__':
    unittest.main()
