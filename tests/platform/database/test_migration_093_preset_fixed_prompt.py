import sqlite3

from tests.platform.database._support import MigrationCase


class TestMigration093PresetFixedPrompt(MigrationCase):
    MIGRATION = "093_preset_fixed_prompt"

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO presets (id, preset_id) VALUES ('row-1', 'krea2')")
            cursor.execute("INSERT INTO users (id, username, email, password_hash) VALUES ('u1', 'u1', 'u1@example.com', 'x')")
            cursor.execute("INSERT INTO generations (id, preset_id, form_data, user_id) VALUES ('g1', 'krea2', '{}', 'u1')")

    def _fixed_prompts(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT preset_id, fixed_prompt FROM presets")
            return {row[0]: row[1] for row in cursor.fetchall()}

    def _save_applied(self, generation_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generation_applied_prompts (generation_id, applied, created_at) VALUES (?, '[]', 'now')",
                (generation_id,),
            )

    def _applied_ids(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT generation_id FROM generation_applied_prompts")
            return [row[0] for row in cursor.fetchall()]

    def test_existing_presets_get_an_empty_fixed_prompt(self):
        self.migration.up()

        self.assertIn("fixed_prompt", self.columns("presets"))
        self.assertEqual(self._fixed_prompts(), {"krea2": "{}"})

    def test_the_applied_prompts_table_is_created(self):
        self.migration.up()

        self.assertEqual(self.columns("generation_applied_prompts"), {"generation_id", "applied", "created_at"})

    def test_running_it_again_changes_nothing(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE presets SET fixed_prompt = '{\"prefix\": \"film\"}'")
        self._save_applied("g1")

        self.migration.up()

        self.assertEqual(self._fixed_prompts(), {"krea2": '{"prefix": "film"}'})
        self.assertEqual(self._applied_ids(), ["g1"])

    def test_deleting_a_generation_removes_its_applied_prompts(self):
        self.migration.up()
        self._save_applied("g1")

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generations WHERE id = 'g1'")

        self.assertEqual(self._applied_ids(), [])

    def test_applied_prompts_need_an_existing_generation(self):
        self.migration.up()

        with self.assertRaises(sqlite3.IntegrityError):
            self._save_applied("missing")
