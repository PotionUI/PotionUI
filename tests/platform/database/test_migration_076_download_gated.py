from tests.platform.database._support import MigrationCase


class TestMigration076DownloadGated(MigrationCase):
    MIGRATION = "076_download_gated"
    PREREQUISITES = ("001_baseline", "005_download_destination_backend")

    def _columns(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(downloads)")
            return {row["name"] for row in cursor.fetchall()}

    def test_adds_both_columns_with_gated_defaulting_to_false(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO downloads (id, type, url, destination_path, filename) VALUES ('old', 'model', 'u', 'd', 'f')"
            )

        self.migration.up()

        self.assertTrue({"gated", "gated_repo"} <= self._columns())
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT gated, gated_repo FROM downloads WHERE id = 'old'")
            row = cursor.fetchone()
        self.assertEqual(row["gated"], 0)
        self.assertIsNone(row["gated_repo"])

    def test_up_is_idempotent(self):
        self.migration.up()
        self.migration.up()

        self.assertTrue({"gated", "gated_repo"} <= self._columns())
