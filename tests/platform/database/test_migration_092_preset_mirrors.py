import sqlite3

from tests.platform.database._support import MigrationCase


class TestMigration092PresetMirrors(MigrationCase):
    MIGRATION = "092_preset_mirrors"

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO presets (id, preset_id) VALUES ('row-krea', 'krea2')")

    def _rows(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def test_adds_nullable_mirror_and_display_columns(self):
        self.migration.up()

        assert {
            "source_preset_id", "display_name", "display_description", "display_cover", "offered_modes", "first_mode",
        } <= self.columns("presets")
        existing = self._rows("SELECT * FROM presets WHERE preset_id = 'krea2'")[0]
        assert [existing[key] for key in ("source_preset_id", "display_name", "offered_modes")] == [None, None, None]

    def test_creates_the_source_index_and_the_tombstone_table(self):
        self.migration.up()

        indexes = {row["name"] for row in self._rows("PRAGMA index_list(presets)")}
        assert "idx_presets_source" in indexes
        assert self.columns("preset_tombstones") == {"preset_id", "name", "source_preset_id", "deleted_at"}

    def test_running_twice_changes_nothing(self):
        self.migration.up()
        before = self.columns("presets")

        self.migration.up()

        assert self.columns("presets") == before
        assert len(self._rows("SELECT * FROM presets")) == 1

    def test_a_source_with_mirrors_cannot_be_deleted(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO presets (id, preset_id, source_preset_id, display_name) "
                "VALUES ('row-anime', 'MIRROR1', 'krea2', 'Anime')"
            )

        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.get_cursor() as cursor:
                cursor.execute("DELETE FROM presets WHERE preset_id = 'krea2'")

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM presets WHERE preset_id = 'MIRROR1'")
            cursor.execute("DELETE FROM presets WHERE preset_id = 'krea2'")
        assert self._rows("SELECT * FROM presets") == []

    def test_a_mirror_needs_an_existing_source_row(self):
        self.migration.up()

        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO presets (id, preset_id, source_preset_id, display_name) "
                    "VALUES ('row-x', 'MIRROR2', 'not-installed', 'Ghost')"
                )
