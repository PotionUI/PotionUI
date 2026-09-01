"""GenerationRunReportRepository against a real scratch SQLite database."""

import importlib.util
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from src.platform.database.database import Database
from src.features.generation.run_report_repository import GenerationRunReportRepository

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

        _load_migration("001_baseline", self.db).up()

        patcher = patch("src.platform.database.database.db", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = GenerationRunReportRepository()

        self._insert_generation("gen-1")
        self._insert_generation("gen-2")

    def tearDown(self):
        Database._instance = None

    def _insert_generation(self, generation_id):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generations (id, preset_id, form_data) VALUES (?, ?, ?)",
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
