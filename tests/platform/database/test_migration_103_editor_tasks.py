from tests.platform.database._support import MigrationCase

class TestMigration103EditorTasksSessionKind(MigrationCase):
    MIGRATION = "103_editor_tasks"
    PREREQUISITES = ("001_baseline", "039_session_pinned", "078_session_write_version")

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES ('u1', 'u1', 'u1@example.com', 'x')"
            )
            cursor.execute(
                "INSERT INTO sessions (id, user_id, preset_id, name, data) VALUES ('old', 'u1', 'p', 'Old', '{}')"
            )

    def run_sql(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()] if cursor.description else []

    def indexes(self):
        return {row[0]: row[1] for row in self.run_sql(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'sessions'"
        )}

    def test_the_column_is_added_not_null_with_the_generation_default(self):
        self.assertNotIn("kind", self.columns("sessions"))

        self.migration.up()

        info = {row[1]: row for row in self.run_sql("PRAGMA table_info(sessions)")}
        self.assertIn("kind", info)
        self.assertEqual(info["kind"][2], "TEXT")
        self.assertEqual(info["kind"][3], 1)
        self.assertEqual(info["kind"][4], "'generation'")

    def test_existing_rows_and_rows_without_a_kind_read_back_generation(self):
        self.migration.up()

        self.run_sql("INSERT INTO sessions (id, user_id, preset_id, name, data) VALUES ('new', 'u1', 'p', 'New', '{}')")

        self.assertEqual(
            self.run_sql("SELECT id, kind FROM sessions ORDER BY id"), [("new", "generation"), ("old", "generation")]
        )

    def test_the_user_preset_kind_index_exists(self):
        self.migration.up()

        index = self.indexes()["idx_sessions_user_preset_kind"]
        self.assertIn("user_id, preset_id, kind", index)

    def test_the_unique_name_key_is_unchanged(self):
        self.migration.up()
        self.run_sql("INSERT INTO sessions (id, user_id, preset_id, name, data, kind) VALUES ('a', 'u1', 'p', 'Same', '{}', 'editor')")

        with self.assertRaises(Exception):
            self.run_sql("INSERT INTO sessions (id, user_id, preset_id, name, data, kind) VALUES ('b', 'u1', 'p', 'Same', '{}', 'generation')")

    def test_a_second_run_changes_nothing_and_keeps_the_values(self):
        self.migration.up()
        self.run_sql("UPDATE sessions SET kind = 'editor'")
        columns = self.columns("sessions")
        indexes = self.indexes()

        self.migration.up()

        self.assertEqual(self.columns("sessions"), columns)
        self.assertEqual(self.indexes(), indexes)
        self.assertEqual(self.run_sql("SELECT kind FROM sessions"), [("editor",)])
