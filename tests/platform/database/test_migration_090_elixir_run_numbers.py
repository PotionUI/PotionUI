from tests.platform.database._support import MigrationCase


class TestMigration090ElixirRunNumbers(MigrationCase):
    MIGRATION = "090_elixir_run_numbers"
    PREREQUISITES = ("001_baseline", "079_elixirs")

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO users (id, username, email, password_hash) VALUES ('u1', 'u1', 'u1@example.com', 'x')")
            for elixir_id in ("e1", "e2"):
                cursor.execute(
                    "INSERT INTO elixirs (id, user_id, name, definition, created_at, updated_at) VALUES (?, 'u1', 'E', '{}', 'now', 'now')",
                    (elixir_id,),
                )

    def _run(self, run_id, elixir_id, created_at):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO elixir_runs (id, elixir_id, user_id, elixir_revision, scope, run_mode, status, graph, created_at, updated_at) "
                "VALUES (?, ?, 'u1', 1, 'graph', 'review', 'completed', '{}', ?, ?)",
                (run_id, elixir_id, created_at, created_at),
            )

    def _numbers(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT id, number FROM elixir_runs ORDER BY id")
            return {row[0]: row[1] for row in cursor.fetchall()}

    def _indexes(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'elixir_runs'")
            return {row[0] for row in cursor.fetchall()}

    def test_an_empty_database_gains_the_column_and_indexes(self):
        self.migration.up()

        self.assertIn("number", self.columns("elixir_runs"))
        self.assertTrue({"idx_elixir_runs_history", "uq_elixir_runs_number"} <= self._indexes())
        self.assertEqual(self._numbers(), {})

    def test_existing_runs_are_numbered_per_elixir_by_creation_order(self):
        self._run("b", "e1", "2026-01-02")
        self._run("a", "e1", "2026-01-01")
        self._run("c", "e1", "2026-01-03")
        self._run("x", "e2", "2026-02-01")

        self.migration.up()

        self.assertEqual(self._numbers(), {"a": 1, "b": 2, "c": 3, "x": 1})

    def test_ties_on_creation_time_break_by_id(self):
        self._run("r2", "e1", "2026-01-01")
        self._run("r1", "e1", "2026-01-01")

        self.migration.up()

        self.assertEqual(self._numbers(), {"r1": 1, "r2": 2})

    def test_replaying_keeps_numbers_and_numbers_only_the_new_rows(self):
        self._run("a", "e1", "2026-01-01")
        self._run("b", "e1", "2026-01-02")
        self.migration.up()
        self._run("c", "e1", "2026-01-03")

        self.migration.up()

        self.assertEqual(self._numbers(), {"a": 1, "b": 2, "c": 3})

    def test_runs_without_an_elixir_stay_unnumbered(self):
        self._run("a", "e1", "2026-01-01")
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE elixir_runs SET elixir_id = NULL")

        self.migration.up()

        self.assertEqual(self._numbers(), {"a": None})
