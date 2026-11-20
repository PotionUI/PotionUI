from tests.platform.database._support import MigrationCase


class TestMigration104EditorRuns(MigrationCase):
    MIGRATION = "104_editor_runs"

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES ('u1', 'u1', 'u1@example.com', 'x')"
            )
            cursor.execute(
                "INSERT INTO generations (id, preset_id, form_data, user_id, status, mode) "
                "VALUES ('old', 'p', '{}', 'u1', 'completed', 'edit')"
            )

    def run_sql(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()] if cursor.description else []

    def index_names(self):
        return {row[0] for row in self.run_sql("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'editor_runs'")}

    def test_the_table_and_its_indexes_are_created(self):
        self.migration.up()

        self.assertEqual(
            set(self.columns("editor_runs")),
            {"id", "user_id", "edit_id", "step_id", "task", "generation_id", "status", "rect", "crop",
             "candidates", "error", "created_at", "updated_at"},
        )
        self.assertTrue({"idx_editor_runs_generation", "idx_editor_runs_user_recent"} <= self.index_names())

    def test_generations_gain_a_nullable_origin_and_old_rows_read_back_null(self):
        self.assertNotIn("origin", self.columns("generations"))

        self.migration.up()

        info = {row[1]: row for row in self.run_sql("PRAGMA table_info(generations)")}
        self.assertEqual((info["origin"][2], info["origin"][3]), ("TEXT", 0))
        self.assertEqual(self.run_sql("SELECT origin FROM generations WHERE id = 'old'"), [(None,)])

    def test_a_run_is_removed_with_its_user(self):
        self.migration.up()
        self.run_sql(
            "INSERT INTO editor_runs (id, user_id, task, status, created_at, updated_at) "
            "VALUES ('er_1', 'u1', 'edit_region', 'queued', 'a', 'a')"
        )

        self.run_sql("DELETE FROM users WHERE id = 'u1'")

        self.assertEqual(self.run_sql("SELECT id FROM editor_runs"), [])

    def test_a_second_run_changes_nothing_and_keeps_the_rows(self):
        self.migration.up()
        self.run_sql(
            "INSERT INTO editor_runs (id, user_id, task, status, created_at, updated_at) "
            "VALUES ('er_1', 'u1', 'edit_region', 'done', 'a', 'a')"
        )
        self.run_sql("UPDATE generations SET origin = '{}'")
        columns = self.columns("generations")
        indexes = self.index_names()

        self.migration.up()

        self.assertEqual(self.columns("generations"), columns)
        self.assertEqual(self.index_names(), indexes)
        self.assertEqual(self.run_sql("SELECT status FROM editor_runs"), [("done",)])
        self.assertEqual(self.run_sql("SELECT origin FROM generations"), [("{}",)])
