from tests.platform.database._support import MigrationCase

class TestMigration102EditsSourceName(MigrationCase):
    MIGRATION = "102_edits_source_name"
    PREREQUISITES = ("001_baseline", "101_edits")

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES ('u1', 'u1', 'u1@example.com', 'x')"
            )
            cursor.execute(
                "INSERT INTO edits (id, user_id, tool, source_kind, source_id, state, created_at, updated_at) "
                "VALUES ('ed_old', 'u1', 'image', 'library', 'up1', '{}', 'n', 'n')"
            )

    def run_sql(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()] if cursor.description else []

    def test_the_column_is_added(self):
        self.assertNotIn("source_name", self.columns("edits"))

        self.migration.up()

        self.assertIn("source_name", self.columns("edits"))

    def test_older_rows_read_back_null_and_the_column_is_nullable(self):
        self.migration.up()

        self.run_sql(
            "INSERT INTO edits (id, user_id, tool, source_kind, source_id, state, created_at, updated_at) "
            "VALUES ('ed_new', 'u1', 'image', 'library', 'up1', '{}', 'n', 'n')"
        )

        self.assertEqual(self.run_sql("SELECT id, source_name FROM edits ORDER BY id"), [("ed_new", None), ("ed_old", None)])

    def test_a_second_run_changes_nothing_and_keeps_the_values(self):
        self.migration.up()
        self.run_sql("UPDATE edits SET source_name = 'a.png'")
        before = self.columns("edits")

        self.migration.up()

        self.assertEqual(self.columns("edits"), before)
        self.assertEqual(self.run_sql("SELECT source_name FROM edits"), [("a.png",)])
