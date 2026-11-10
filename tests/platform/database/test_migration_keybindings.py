from tests.platform.database._support import MigrationCase, load_migration

KEYBINDINGS = (
    ("010_add_toggle_floating_form_keybinding", "toggle_floating_form", "q"),
    ("011_add_toggle_last_generations_keybinding", "toggle_last_generations", "h"),
    ("012_add_toggle_floating_workbench_keybinding", "toggle_floating_workbench", "w"),
    ("013_add_toggle_workbench_panel_keybinding", "toggle_workbench_panel", "e"),
    ("017_add_save_session_keybinding", "save_session", "s"),
    ("094_add_delete_current_generation_keybinding", "delete_current_generation", "Delete"),
)


class TestKeybindingMigrations(MigrationCase):

    def _row(self, binding_id):
        with self.db.get_connection() as conn:
            return conn.execute(
                "SELECT key, modifiers, label, category, context, enabled "
                "FROM keybinding_defaults WHERE id = ?",
                (binding_id,),
            ).fetchone()

    def test_seeds_the_default_row(self):
        for stem, binding_id, key in KEYBINDINGS:
            with self.subTest(stem=stem):
                self.assertIsNone(self._row(binding_id))
                load_migration(stem, self.db).up()
                row = self._row(binding_id)
                self.assertIsNotNone(row)
                self.assertEqual(row[0], key)
                self.assertEqual(row[1], "")
                self.assertEqual(row[3], "generation")
                self.assertEqual(row[4], "generate")
                self.assertEqual(row[5], 1)

    def test_idempotent(self):
        for stem, binding_id, _key in KEYBINDINGS:
            with self.subTest(stem=stem):
                migration = load_migration(stem, self.db)
                migration.up()
                migration.up()
                with self.db.get_connection() as conn:
                    count = conn.execute(
                        "SELECT COUNT(*) FROM keybinding_defaults WHERE id = ?",
                        (binding_id,),
                    ).fetchone()[0]
                self.assertEqual(count, 1)
