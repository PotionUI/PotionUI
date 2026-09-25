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


class TestMigration033RemoveBreakSegments(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("033_remove_break_segments", self.db)

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES ('user1', 'tester', 'tester@example.com', 'hashed')"
            )
            cursor.execute(
                "INSERT INTO segment_categories (id, user_id, name) VALUES ('cat1', 'user1', 'Category')"
            )
            cursor.execute(
                "INSERT INTO prompts (id, user_id) VALUES ('prompt1', 'user1')"
            )
            cursor.execute(
                "INSERT INTO prompt_segments (id, prompt_id, position, type, content) "
                "VALUES ('ps1', 'prompt1', 0, 'content', 'kept')"
            )
            cursor.execute(
                "INSERT INTO prompt_segments (id, prompt_id, position, type, content) "
                "VALUES ('ps2', 'prompt1', 1, 'break', '')"
            )
            cursor.execute(
                "INSERT INTO saved_segments (id, user_id, category_id, name, type) "
                "VALUES ('s1', 'user1', 'cat1', 'Content segment', 'content')"
            )
            cursor.execute(
                "INSERT INTO saved_segments (id, user_id, category_id, name, type) "
                "VALUES ('s2', 'user1', 'cat1', 'Break segment', 'break')"
            )
            cursor.execute(
                "INSERT INTO segment_templates (id, user_id, name) VALUES ('t1', 'user1', 'Template')"
            )
            cursor.execute(
                "INSERT INTO segment_template_segments (id, template_id, position, type, content) "
                "VALUES ('tt1', 't1', 0, 'content', 'kept')"
            )
            cursor.execute(
                "INSERT INTO segment_template_segments (id, template_id, position, type, content) "
                "VALUES ('tt2', 't1', 1, 'break', '')"
            )
            cursor.execute("INSERT INTO generations (id, form_data) VALUES ('gen1', '{}')")
            cursor.execute(
                "INSERT INTO generation_segments (id, generation_id, segment_type, text) "
                "VALUES ('gs1', 'gen1', 'content', 'kept')"
            )
            cursor.execute(
                "INSERT INTO generation_segments (id, generation_id, segment_type, text) "
                "VALUES ('gs2', 'gen1', 'break', '')"
            )

    def tearDown(self):
        Database._instance = None

    def _ids(self, table):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"SELECT id FROM {table} ORDER BY id")
            return [row["id"] for row in cursor.fetchall()]

    def test_break_rows_are_removed_from_every_table(self):
        self.migration.up()

        self.assertEqual(self._ids("prompt_segments"), ["ps1"])
        self.assertEqual(self._ids("saved_segments"), ["s1"])
        self.assertEqual(self._ids("segment_template_segments"), ["tt1"])
        self.assertEqual(self._ids("generation_segments"), ["gs1"])

    def test_a_second_run_changes_nothing_further(self):
        self.migration.up()
        self.migration.up()

        self.assertEqual(self._ids("prompt_segments"), ["ps1"])
        self.assertEqual(self._ids("saved_segments"), ["s1"])
        self.assertEqual(self._ids("segment_template_segments"), ["tt1"])
        self.assertEqual(self._ids("generation_segments"), ["gs1"])


if __name__ == "__main__":
    unittest.main()
