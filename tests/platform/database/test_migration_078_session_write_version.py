from tests.platform.database._support import MigrationCase


class TestMigration078SessionWriteVersion(MigrationCase):
    MIGRATION = "078_session_write_version"

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES ('user1', 'tester', 'tester@example.com', 'hashed')"
            )
            cursor.execute(
                "INSERT INTO sessions (id, user_id, preset_id, name, data) "
                "VALUES ('s1', 'user1', 'krea2', 'Monica', '{}')"
            )

    def _row(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT version, last_origin FROM sessions WHERE id = 's1'")
            return tuple(cursor.fetchone())

    def test_a_pre_078_sessions_table_has_neither_column(self):
        self.assertFalse({"version", "last_origin"} & self.columns("sessions"))

    def test_existing_sessions_start_at_version_one_with_no_origin(self):
        self.migration.up()

        self.assertTrue({"version", "last_origin"} <= self.columns("sessions"))
        self.assertEqual(self._row(), (1, None))

    def test_new_sessions_default_to_version_one(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO sessions (id, user_id, preset_id, name, data) "
                "VALUES ('s2', 'user1', 'krea2', 'Rooftop', '{}')"
            )
            cursor.execute("SELECT version FROM sessions WHERE id = 's2'")
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_a_second_run_changes_nothing(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE sessions SET version = 4, last_origin = '{\"kind\": \"api\"}' WHERE id = 's1'")

        self.migration.up()

        self.assertEqual(self._row(), (4, '{"kind": "api"}'))
        self.assertEqual(sorted(self.columns("sessions")).count("version"), 1)
