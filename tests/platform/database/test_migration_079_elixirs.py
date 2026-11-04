import sqlite3
import unittest

from tests.platform.database._support import MigrationCase

LIMIT_KEYS = {
    "elixirs_max_nodes": "24",
    "elixirs_max_edges": "48",
    "elixirs_max_branches": "8",
    "elixirs_max_media_per_source": "1000",
    "elixirs_max_outputs_per_connection": "1000",
    "elixirs_max_invocations": "200",
}


class TestMigration079Elixirs(MigrationCase):
    MIGRATION = "079_elixirs"

    def _user(self, user_id="u1"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, 'x')",
                (user_id, user_id, f"{user_id}@example.com"),
            )

    def _elixir(self, elixir_id="e1", user_id="u1"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO elixirs (id, user_id, name, definition, created_at, updated_at) VALUES (?, ?, 'E', '{}', 'now', 'now')",
                (elixir_id, user_id),
            )

    def _run(self, run_id="r1", elixir_id="e1", user_id="u1", key=None, status="pending"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO elixir_runs (id, elixir_id, user_id, elixir_revision, scope, run_mode, status, graph, "
                "idempotency_key, created_at, updated_at) VALUES (?, ?, ?, 1, 'graph', 'review', ?, '{}', ?, 'now', 'now')",
                (run_id, elixir_id, user_id, status, key),
            )

    def _invocation(self, invocation_id, run_id="r1", key=None, generation_id=None):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO elixir_invocations (id, run_id, user_id, node_id, ordinal, input_key, idempotency_key, "
                "generation_id, created_at, updated_at) VALUES (?, ?, 'u1', 'n', 0, '-', ?, ?, 'now', 'now')",
                (invocation_id, run_id, key or invocation_id, generation_id),
            )

    def _names(self, kind):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
            return {row[0] for row in cursor.fetchall()}

    def _count(self, table):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            return cursor.fetchone()[0]

    def test_the_tables_and_indexes_are_created(self):
        self.assertNotIn("elixirs", self._names("table"))

        self.migration.up()

        self.assertLessEqual({"elixirs", "elixir_runs", "elixir_invocations"}, self._names("table"))
        self.assertLessEqual(
            {
                "idx_elixirs_user",
                "uq_elixir_runs_idempotency",
                "idx_elixir_runs_elixir",
                "idx_elixir_runs_status",
                "uq_elixir_invocations_key",
                "uq_elixir_invocations_slot",
                "uq_elixir_invocations_accepted",
                "uq_elixir_invocations_generation",
                "idx_elixir_invocations_run",
                "idx_elixir_invocations_status",
            },
            self._names("index"),
        )
        self.assertIn("submitted_boot", self.columns("elixir_invocations"))
        self.assertNotIn("elixirs_schema_version", self._names("table"))

    def test_the_limits_are_seeded_as_system_settings(self):
        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT key, value, value_type, type FROM settings WHERE key LIKE 'elixirs_%'")
            rows = {row[0]: (row[1], row[2], row[3]) for row in cursor.fetchall()}
        self.assertEqual(rows, {key: (value, "integer", "SYSTEM") for key, value in LIMIT_KEYS.items()})

    def test_a_second_run_keeps_rows_and_admin_changed_limits(self):
        self.migration.up()
        self._user()
        self._elixir()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE settings SET value = '5' WHERE key = 'elixirs_max_nodes'")

        self.migration.up()

        self.assertEqual(self._count("elixirs"), 1)
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT value FROM settings WHERE key = 'elixirs_max_nodes'")
            self.assertEqual(cursor.fetchone()[0], "5")
            cursor.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'elixirs_%'")
            self.assertEqual(cursor.fetchone()[0], len(LIMIT_KEYS))

    def test_an_elixir_for_a_missing_user_is_refused(self):
        self.migration.up()

        with self.assertRaises(sqlite3.IntegrityError):
            self._elixir(user_id="ghost")

    def test_deleting_a_user_removes_their_elixirs_runs_and_invocations(self):
        self.migration.up()
        self._user("u1")
        self._user("u2")
        self._elixir("e1", "u1")
        self._elixir("e2", "u2")
        self._run("r1", "e1", "u1")
        self._invocation("i1", "r1")

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = 'u1'")

        self.assertEqual(self._count("elixirs"), 1)
        self.assertEqual(self._count("elixir_runs"), 0)
        self.assertEqual(self._count("elixir_invocations"), 0)

    def test_deleting_an_elixir_keeps_its_runs(self):
        self.migration.up()
        self._user()
        self._elixir()
        self._run(status="completed")

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM elixirs WHERE id = 'e1'")
            cursor.execute("SELECT elixir_id FROM elixir_runs WHERE id = 'r1'")
            self.assertIsNone(cursor.fetchone()[0])

    def test_a_brew_key_and_a_generation_are_unique(self):
        self.migration.up()
        self._user()
        self._elixir()
        self._run("r1", key="k")

        with self.assertRaises(sqlite3.IntegrityError):
            self._run("r2", key="k")
        self._run("r3")
        self._invocation("i1", generation_id="g1")
        with self.assertRaises(sqlite3.IntegrityError):
            self._invocation("i2", run_id="r3", generation_id="g1")
        self._invocation("i3", run_id="r3", generation_id="g2")

    def test_an_unknown_run_status_is_refused(self):
        self.migration.up()
        self._user()
        self._elixir()

        with self.assertRaises(sqlite3.IntegrityError):
            self._run(status="exploded")


if __name__ == "__main__":
    unittest.main()
