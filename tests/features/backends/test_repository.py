"""Tests for BackendRepository - per-engine default isolation.

Uses a real temp-file sqlite database migrated through the full migration
chain (mirrors the pattern in tests/persistence/test_base.py). The repository
resolves `db` at call time from `src.platform.database.database`, so patching
that one canonical name redirects it (and every other repository) to the test
database below.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from src.features.backends.records import Backend
from src.features.backends.repository import BackendRepository


class TestBackendRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        Database._instance = None
        self.db = Database()
        self.db.db_path = self.temp_db_path
        self.db.db_path.parent.mkdir(exist_ok=True)
        self.db._initialized = True

        self._patchers = [
            patch("src.platform.database.database.db", self.db),
            patch("src.platform.database.migration_runner.db", self.db),
        ]
        for p in self._patchers:
            p.start()

        self._run_migrations()

        self.repo = BackendRepository()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        Path(self.temp_dir).rmdir()
        Database._instance = None

    def _run_migrations(self):
        migration_runner = MigrationRunner()
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            migration_runner.run_migrations()
        finally:
            sys.stdout = old_stdout

        # Migration 069 auto-defaults an engine with exactly one enabled
        # backend (the native one seeded by earlier migrations). Clear that
        # out so each test starts from a known, empty state.
        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM backends")

    def _make_backend(self, id, name, engine, is_default=False, enabled=True, driver=None):
        return Backend(
            id=id,
            name=name,
            engine=engine,
            driver=driver or engine,
            enabled=enabled,
            is_default=is_default,
            config={},
        )

    def test_set_default_clears_default_only_within_engine(self):
        a = self.repo.create(self._make_backend("comfy-a", "A", "comfyui", is_default=True))
        b = self.repo.create(self._make_backend("comfy-b", "B", "comfyui"))
        native = self.repo.create(self._make_backend("native-1", "Native", "native", is_default=True))

        self.repo.set_default("comfy-b", "comfyui")

        self.assertFalse(self.repo.get_by_id("comfy-a").is_default)
        self.assertTrue(self.repo.get_by_id("comfy-b").is_default)
        # The native engine's default is untouched by a comfyui set_default call.
        self.assertTrue(self.repo.get_by_id("native-1").is_default)

    def test_two_engines_can_each_have_their_own_default(self):
        self.repo.create(self._make_backend("comfy-1", "Comfy", "comfyui", is_default=True))
        self.repo.create(self._make_backend("native-1", "Native", "native", is_default=True))

        comfy_default = self.repo.get_default("comfyui")
        native_default = self.repo.get_default("native")

        self.assertEqual(comfy_default.id, "comfy-1")
        self.assertEqual(native_default.id, "native-1")

    def test_get_default_isolates_per_engine(self):
        self.repo.create(self._make_backend("comfy-1", "Comfy1", "comfyui", is_default=True))
        self.repo.create(self._make_backend("comfy-2", "Comfy2", "comfyui"))

        self.assertIsNone(self.repo.get_default("native"))
        self.assertEqual(self.repo.get_default("comfyui").id, "comfy-1")

    def test_create_with_is_default_unsets_previous_default_same_engine(self):
        self.repo.create(self._make_backend("comfy-1", "Comfy1", "comfyui", is_default=True))
        self.repo.create(self._make_backend("comfy-2", "Comfy2", "comfyui", is_default=True))

        self.assertFalse(self.repo.get_by_id("comfy-1").is_default)
        self.assertTrue(self.repo.get_by_id("comfy-2").is_default)

    def test_update_with_is_default_unsets_previous_default_same_engine(self):
        first = self.repo.create(self._make_backend("comfy-1", "Comfy1", "comfyui", is_default=True))
        second = self.repo.create(self._make_backend("comfy-2", "Comfy2", "comfyui"))

        second.is_default = True
        self.repo.update("comfy-2", second)

        self.assertFalse(self.repo.get_by_id("comfy-1").is_default)
        self.assertTrue(self.repo.get_by_id("comfy-2").is_default)

    def test_set_default_returns_false_for_unknown_backend(self):
        result = self.repo.set_default("does-not-exist", "comfyui")
        self.assertFalse(result)

    def test_get_by_engine_filters_correctly(self):
        self.repo.create(self._make_backend("comfy-1", "Comfy1", "comfyui"))
        self.repo.create(self._make_backend("comfy-2", "Comfy2", "comfyui"))
        self.repo.create(self._make_backend("native-1", "Native", "native"))

        comfy_backends = self.repo.get_by_engine("comfyui")

        self.assertEqual({b.id for b in comfy_backends}, {"comfy-1", "comfy-2"})


if __name__ == "__main__":
    unittest.main()
