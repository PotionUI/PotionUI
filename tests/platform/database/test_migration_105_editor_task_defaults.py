from tests.platform.database._support import MigrationCase


class TestMigration105EditorTaskDefaults(MigrationCase):
    MIGRATION = "105_editor_task_defaults"

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES ('u1', 'u1', 'u1@example.com', 'x')"
            )

    def run_sql(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()] if cursor.description else []

    def insert(self, task="edit_region"):
        self.run_sql(
            "INSERT INTO editor_task_defaults (user_id, task, preset_id, mode, setup_kind, set_at) "
            "VALUES ('u1', ?, 'p', 'edit', 'defaults', 'a')",
            (task,),
        )

    def test_the_table_is_created_with_its_columns(self):
        self.migration.up()

        self.assertEqual(
            set(self.columns("editor_task_defaults")),
            {"user_id", "task", "preset_id", "mode", "variant", "setup_kind", "setup_id", "setup_name",
             "preset_name", "mode_label", "set_at"},
        )

    def test_a_user_holds_one_default_per_task(self):
        self.migration.up()
        self.insert()

        with self.assertRaises(Exception):
            self.insert()
        self.insert("remove_object")

        self.assertEqual(len(self.run_sql("SELECT task FROM editor_task_defaults")), 2)

    def test_a_default_is_removed_with_its_user(self):
        self.migration.up()
        self.insert()

        self.run_sql("DELETE FROM users WHERE id = 'u1'")

        self.assertEqual(self.run_sql("SELECT task FROM editor_task_defaults"), [])

    def test_a_second_run_changes_nothing_and_keeps_the_rows(self):
        self.migration.up()
        self.insert()
        columns = self.columns("editor_task_defaults")

        self.migration.up()

        self.assertEqual(self.columns("editor_task_defaults"), columns)
        self.assertEqual(self.run_sql("SELECT task FROM editor_task_defaults"), [("edit_region",)])
