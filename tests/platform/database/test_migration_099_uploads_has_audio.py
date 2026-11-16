import unittest

from tests.platform.database._support import MigrationCase


class TestMigration099UploadsHasAudio(MigrationCase):
    MIGRATION = "099_uploads_has_audio"

    def _columns(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(uploads)")
            return {row[1]: row for row in cursor.fetchall()}

    def _insert_upload(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES ('user1', 'tester', 'tester@example.com', 'hashed')"
            )
            cursor.execute(
                "INSERT INTO uploads (id, user_id, filename, media_type) "
                "VALUES ('u1', 'user1', 'a.mp4', 'video')"
            )

    def test_column_lands_on_uploads_as_nullable(self):
        self.assertNotIn("has_audio", self._columns())

        self.migration.up()

        column = self._columns()["has_audio"]
        self.assertEqual(column[2].upper(), "INTEGER")
        self.assertEqual(column[3], 0)
        self.assertIsNone(column[4])

    def test_rows_that_predate_the_column_read_back_null(self):
        self._insert_upload()

        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT has_audio FROM uploads WHERE id = 'u1'")
            self.assertIsNone(cursor.fetchone()["has_audio"])

    def test_a_second_run_changes_nothing(self):
        self.migration.up()
        before = set(self._columns())

        self.migration.up()

        self.assertEqual(set(self._columns()), before)


if __name__ == "__main__":
    unittest.main()
