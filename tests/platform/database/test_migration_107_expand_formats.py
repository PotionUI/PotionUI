import contextlib
import io

from tests.platform.database._support import MigrationCase


class TestMigration107ExpandFormats(MigrationCase):
    MIGRATION = "107_expand_formats"

    def row(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT value, value_type, type FROM settings WHERE key = 'editor_expand_formats'")
            return cursor.fetchone()

    def run_up(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.migration.up()

    def test_it_seeds_an_empty_system_list(self):
        self.run_up()

        row = self.row()
        assert (row["value"], row["value_type"], row["type"]) == ("[]", "json", "SYSTEM")

    def test_it_runs_twice_and_keeps_what_an_admin_wrote(self):
        self.run_up()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE settings SET value = '[{\"id\": \"team\"}]' WHERE key = 'editor_expand_formats'")

        self.run_up()

        assert self.row()["value"] == '[{"id": "team"}]'
