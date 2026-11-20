import sqlite3

from tests.platform.database._support import MigrationCase


class TestMigration101Edits(MigrationCase):
    MIGRATION = "101_edits"

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            for user_id in ("u1", "u2"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, 'x')",
                    (user_id, user_id, f"{user_id}@example.com"),
                )
            cursor.execute(
                "INSERT INTO uploads (id, user_id, filename, media_type) VALUES ('up1', 'u1', 'a.png', 'image')"
            )

    def run_sql(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()] if cursor.description else []

    def edit(self, edit_id="ed_1", user="u1", thumbnail=None):
        self.run_sql(
            "INSERT INTO edits (id, user_id, tool, source_kind, source_id, state, thumbnail_upload_id, created_at, updated_at) "
            "VALUES (?, ?, 'image', 'library', 'up1', '{}', ?, 'n', 'n')",
            (edit_id, user, thumbnail),
        )

    def test_the_table_has_the_documented_columns(self):
        self.migration.up()

        self.assertEqual(
            self.columns("edits"),
            {
                "id", "user_id", "tool", "name", "source_kind", "source_id", "state",
                "thumbnail_upload_id", "revision", "created_at", "updated_at",
            },
        )

    def test_running_twice_is_harmless_and_keeps_rows(self):
        self.migration.up()
        self.edit()

        self.migration.up()

        self.assertEqual(self.run_sql("SELECT id FROM edits"), [("ed_1",)])

    def test_a_new_edit_is_unnamed_at_revision_one(self):
        self.migration.up()
        self.edit()

        self.assertEqual(self.run_sql("SELECT name, revision FROM edits"), [(None, 1)])

    def test_the_recent_first_index_exists(self):
        self.migration.up()

        indexes = {row[0] for row in self.run_sql("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'edits'")}

        self.assertIn("idx_edits_user_recent", indexes)

    def test_an_edit_needs_an_existing_owner_and_a_thumbnail_that_exists(self):
        self.migration.up()

        with self.assertRaises(sqlite3.IntegrityError):
            self.edit(user="ghost")
        with self.assertRaises(sqlite3.IntegrityError):
            self.edit(thumbnail="missing")
