import sqlite3

from tests.platform.database._support import MigrationCase


class TestMigration097Kits(MigrationCase):
    MIGRATION = "097_kits"

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

    def kit(self, kit_id="kt_00000001", owner="u1", handle="mara"):
        self.run_sql(
            "INSERT INTO kits (id, owner_id, name, handle, created_at, updated_at) VALUES (?, ?, 'Mara', ?, 'n', 'n')",
            (kit_id, owner, handle),
        )

    def item(self, item_id, kit_id="kt_00000001", name="face", kind="image", media_ref=None):
        self.run_sql(
            "INSERT INTO kit_items (id, kit_id, kind, name, media_ref, created_at) VALUES (?, ?, ?, ?, ?, 'n')",
            (item_id, kit_id, kind, name, media_ref),
        )

    def test_the_tables_have_the_documented_columns(self):
        self.migration.up()

        self.assertEqual(
            self.columns("kits"),
            {"id", "owner_id", "name", "handle", "label", "position", "created_at", "updated_at"},
        )
        self.assertEqual(
            self.columns("kit_items"),
            {"id", "kit_id", "kind", "name", "position", "media_ref", "text", "created_at"},
        )
        self.assertEqual(self.columns("kit_handle_aliases"), {"owner_id", "handle", "kit_id"})

    def test_running_twice_is_harmless(self):
        self.migration.up()
        self.kit()

        self.migration.up()

        self.assertEqual(self.run_sql("SELECT COUNT(*) FROM kits"), [(1,)])

    def test_a_path_is_unique_per_owner_only(self):
        self.migration.up()
        self.kit()

        self.kit("kt_00000002", owner="u2")
        with self.assertRaises(sqlite3.IntegrityError):
            self.kit("kt_00000003", owner="u1")

    def test_an_item_name_is_unique_within_its_kit_only(self):
        self.migration.up()
        self.kit()
        self.kit("kt_00000002", handle="tomas")
        self.item("it_00000001")

        self.item("it_00000002", kit_id="kt_00000002")
        with self.assertRaises(sqlite3.IntegrityError):
            self.item("it_00000003")

    def test_an_item_kind_outside_the_four_is_refused(self):
        self.migration.up()
        self.kit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.item("it_00000001", kind="model")

    def test_deleting_the_library_file_keeps_the_item_with_no_media_ref(self):
        self.migration.up()
        self.kit()
        self.item("it_00000001", media_ref="up1")

        self.run_sql("DELETE FROM uploads WHERE id = 'up1'")

        self.assertEqual(self.run_sql("SELECT kind, media_ref FROM kit_items"), [("image", None)])

    def test_deleting_a_kit_removes_its_items_and_aliases_but_not_the_file(self):
        self.migration.up()
        self.kit()
        self.item("it_00000001", media_ref="up1")
        self.run_sql("INSERT INTO kit_handle_aliases (owner_id, handle, kit_id) VALUES ('u1', 'old', 'kt_00000001')")

        self.run_sql("DELETE FROM kits WHERE id = 'kt_00000001'")

        self.assertEqual(self.run_sql("SELECT COUNT(*) FROM kit_items"), [(0,)])
        self.assertEqual(self.run_sql("SELECT COUNT(*) FROM kit_handle_aliases"), [(0,)])
        self.assertEqual(self.run_sql("SELECT COUNT(*) FROM uploads"), [(1,)])

    def test_deleting_a_user_removes_their_kits(self):
        self.migration.up()
        self.kit()
        self.item("it_00000001")

        self.run_sql("DELETE FROM users WHERE id = 'u1'")

        self.assertEqual(self.run_sql("SELECT COUNT(*) FROM kits"), [(0,)])
        self.assertEqual(self.run_sql("SELECT COUNT(*) FROM kit_items"), [(0,)])
