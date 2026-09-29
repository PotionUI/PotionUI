import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.features.downloads.models import Download
from src.platform.database.database import Database
from tests.fixtures.persistence_base import make_lean_file_database

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


class TestMigration038DownloadFilenameSupplied(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        _load_migration("005_download_destination_backend", self.db).up()
        self.migration = _load_migration("038_download_filename_supplied", self.db)

    def tearDown(self):
        Database._instance = None

    def _has_column(self) -> bool:
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(downloads)")
            return any(row["name"] == "filename_supplied" for row in cursor.fetchall())

    def _insert(self, download_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO downloads (id, type, url, destination_path, filename) VALUES (?, 'model', 'u', 'd', 'f')",
                (download_id,),
            )

    def _supplied(self, download_id):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT filename_supplied FROM downloads WHERE id = ?", (download_id,))
            return cursor.fetchone()["filename_supplied"]

    def test_existing_rows_count_as_caller_supplied(self):
        self._insert("old")

        self.migration.up()

        self.assertTrue(self._has_column())
        self.assertEqual(self._supplied("old"), 1)

    def test_rows_inserted_afterwards_default_to_caller_supplied(self):
        self.migration.up()
        self._insert("new")

        self.assertEqual(self._supplied("new"), 1)

    def test_up_is_idempotent(self):
        self.migration.up()
        self.migration.up()

        self.assertTrue(self._has_column())

    def test_down_drops_the_column(self):
        self.migration.up()

        self.migration.down()

        self.assertFalse(self._has_column())


_pre_038_template = None


def _pre_038_template_path(repo_root):
    global _pre_038_template
    if _pre_038_template is None:
        path = Path(tempfile.mkdtemp(prefix="potionui-pre038-")) / "pre038.sqlite"
        Database._instance = None
        database = Database()
        database.db_path = path
        database._initialized = True
        make_lean_file_database(database)
        stems = sorted(p.stem for p in _MIGRATIONS.glob("*.py") if p.name != "__init__.py")
        with patch("pathlib.Path.cwd", return_value=repo_root):
            for stem in [stem for stem in stems if stem < "038_download_filename_supplied"]:
                _load_migration(stem, database).up()
        with database.get_connection() as conn:
            conn.execute("PRAGMA journal_mode = DELETE").close()
        Database._instance = None
        _pre_038_template = path
    return _pre_038_template


class TestMigration038OnTheFullChain(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        repo_root = Path(tempfile.mkdtemp())
        template = _pre_038_template_path(repo_root)
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        shutil.copyfile(template, self.db.db_path)
        make_lean_file_database(self.db)
        self.cwd_patch = patch("pathlib.Path.cwd", return_value=repo_root)
        self.cwd_patch.start()
        self.migration = _load_migration("038_download_filename_supplied", self.db)

    def tearDown(self):
        self.cwd_patch.stop()
        Database._instance = None

    def _insert(self, download_id, filename):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO downloads (id, type, url, destination_path, filename, status) "
                "VALUES (?, 'model', 'https://civitai.com/api/download/models/1', ?, ?, 'paused')",
                (download_id, f"/depot/{filename}", filename),
            )

    def _row(self, download_id):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM downloads WHERE id = ?", (download_id,))
            return cursor.fetchone()

    def test_a_row_queued_before_the_migration_is_a_supplied_name_and_survives_a_rerun(self):
        self._insert("legacy", "1")

        self.migration.up()
        self.migration.up()

        download = Download.from_row(self._row("legacy"))
        self.assertTrue(download.filename_supplied)
        self.assertTrue(download.to_dict()["filename_supplied"])
        self.assertEqual(download.filename, "1")

    def test_a_derived_name_row_keeps_its_flag_when_the_migration_is_rerun(self):
        self.migration.up()
        self._insert("derived", "1")
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE downloads SET filename_supplied = 0 WHERE id = 'derived'")

        self.migration.up()

        self.assertFalse(Download.from_row(self._row("derived")).filename_supplied)
