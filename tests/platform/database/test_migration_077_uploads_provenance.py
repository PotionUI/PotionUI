import unittest

from tests.platform.database._support import MigrationCase


class TestMigration077UploadsProvenance(MigrationCase):
    MIGRATION = "077_uploads_provenance"

    def _columns(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(uploads)")
            return {row[1]: row for row in cursor.fetchall()}

    def test_column_lands_on_uploads_as_nullable_text(self):
        self.assertNotIn("provenance", self._columns())

        self.migration.up()

        column = self._columns()["provenance"]
        self.assertEqual(column[2].upper(), "TEXT")
        self.assertEqual(column[3], 0)
        self.assertIsNone(column[4])

    def test_rows_that_predate_the_column_read_back_null(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES ('user1', 'tester', 'tester@example.com', 'hashed')"
            )
            cursor.execute(
                "INSERT INTO uploads (id, user_id, filename, media_type) VALUES ('u1', 'user1', 'a.png', 'image')"
            )

        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT provenance FROM uploads WHERE id = 'u1'")
            self.assertIsNone(cursor.fetchone()["provenance"])

    def test_a_second_run_changes_nothing(self):
        self.migration.up()
        before = set(self._columns())

        self.migration.up()

        self.assertEqual(set(self._columns()), before)


if __name__ == "__main__":
    unittest.main()
