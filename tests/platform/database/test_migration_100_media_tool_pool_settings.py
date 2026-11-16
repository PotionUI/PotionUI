from tests.platform.database._support import MigrationCase

KEYS = {
    "media_tools_max_concurrent": "0",
    "media_tools_max_previews": "2",
    "media_tools_max_running_per_user": "0",
    "media_tools_max_queued_per_user": "8",
    "media_tools_ffmpeg_threads": "0",
}


class TestMigration100MediaToolPoolSettings(MigrationCase):
    MIGRATION = "100_media_tool_pool_settings"

    def rows(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT key, value, value_type, type FROM settings WHERE key LIKE 'media_tools_%'")
            return {row["key"]: (row["value"], row["value_type"], row["type"]) for row in cursor.fetchall()}

    def test_seeds_the_defaults_as_system_integer_settings(self):
        self.migration.up()

        assert {key: value for key, (value, _, _) in self.rows().items()} == KEYS
        assert {(kind, scope) for _, kind, scope in self.rows().values()} == {("integer", "SYSTEM")}

    def test_keeps_a_value_an_admin_already_set_and_is_idempotent(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE settings SET value = '6' WHERE key = 'media_tools_max_concurrent'")

        self.migration.up()

        assert self.rows()["media_tools_max_concurrent"][0] == "6"
        assert len(self.rows()) == 5
