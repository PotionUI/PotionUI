from tests.platform.database._support import MigrationCase


class TestMigration080ElixirsMediaLimits(MigrationCase):
    MIGRATION = "080_elixirs_media_limits"
    PREREQUISITES = ("001_baseline", "079_elixirs")

    def _value(self, key):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None

    def _set(self, key, value):
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))

    def test_the_old_defaults_are_raised_to_1000(self):
        self.assertEqual(self._value("elixirs_max_media_per_source"), "50")

        self.migration.up()

        self.assertEqual(self._value("elixirs_max_media_per_source"), "1000")
        self.assertEqual(self._value("elixirs_max_outputs_per_connection"), "1000")

    def test_a_value_an_admin_changed_is_kept(self):
        self._set("elixirs_max_media_per_source", "120")

        self.migration.up()

        self.assertEqual(self._value("elixirs_max_media_per_source"), "120")
        self.assertEqual(self._value("elixirs_max_outputs_per_connection"), "1000")

    def test_other_limits_are_untouched(self):
        self.migration.up()

        self.assertEqual(self._value("elixirs_max_invocations"), "200")
