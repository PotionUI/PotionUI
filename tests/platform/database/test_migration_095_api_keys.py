import hashlib
import json
import sqlite3

from tests.platform.database._support import MigrationCase

PLAINTEXT_USED = "pui_mcp_" + "a" * 43
PLAINTEXT_REVOKED = "pui_mcp_" + "b" * 43
PLAINTEXT_UNUSED = "pui_mcp_" + "c" * 43
PLAINTEXT_ORPHAN = "pui_mcp_" + "d" * 43


def digest(plaintext):
    return hashlib.sha256(plaintext.encode()).hexdigest()


class TestMigration095ApiKeys(MigrationCase):
    MIGRATION = "095_api_keys"

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            for user_id in ("u1", "u2", "u3"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, 'x')",
                    (user_id, user_id, f"{user_id}@example.com"),
                )
            tokens = (
                ("t-used", "u1", "laptop", PLAINTEXT_USED, "2026-10-01T00:00:00+00:00", "2026-11-01T08:15:00.123456+00:00", None),
                ("t-revoked", "u1", "old", PLAINTEXT_REVOKED, "2026-09-01T00:00:00+00:00", None, "2026-09-02T00:00:00+00:00"),
                ("t-unused", "u2", "phone", PLAINTEXT_UNUSED, "2026-10-05T00:00:00+00:00", None, None),
                ("t-orphan", "ghost", "gone", PLAINTEXT_ORPHAN, "2026-10-06T00:00:00+00:00", None, None),
            )
            for token_id, user_id, name, plaintext, created, used, revoked in tokens:
                cursor.execute(
                    "INSERT INTO mcp_tokens (id, user_id, name, token_hash, token_prefix, created_at, last_used_at, revoked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (token_id, user_id, name, digest(plaintext), plaintext[:12], created, used, revoked),
                )
            cursor.execute("SELECT id FROM settings WHERE key = 'mcp_user_enabled'")
            setting_id = cursor.fetchone()[0]
            for user_id, value in (("u2", "false"), ("u3", "true")):
                cursor.execute(
                    "INSERT INTO user_settings (id, user_id, setting_id, value) VALUES (?, ?, ?, ?)",
                    (f"us-{user_id}", user_id, setting_id, value),
                )

    def rows(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()]

    def test_the_new_tables_exist(self):
        self.migration.up()

        for table in (
            "api_keys", "api_key_events", "api_key_user_access", "mcp_tool_ceiling",
            "mcp_tool_defaults", "mcp_tool_default_policy", "mcp_key_tool_overrides",
        ):
            self.assertTrue(self.columns(table), table)
        self.assertEqual(
            self.columns("api_keys"),
            {
                "id", "user_id", "name", "secret_hash", "display_prefix", "last_four", "scopes", "created_at",
                "expires_at", "last_used_at", "last_used_via", "revoked_at", "revoked_by",
            },
        )

    def test_a_token_plaintext_resolves_by_the_same_hash_after_the_migration(self):
        self.migration.up()

        for plaintext, owner in ((PLAINTEXT_USED, "u1"), (PLAINTEXT_REVOKED, "u1"), (PLAINTEXT_UNUSED, "u2")):
            found = self.rows("SELECT user_id FROM api_keys WHERE secret_hash = ?", (digest(plaintext),))
            self.assertEqual(found, [(owner,)], plaintext)

    def test_a_migrated_key_keeps_its_identity_and_gets_only_the_mcp_scope(self):
        self.migration.up()

        row = self.rows(
            "SELECT id, user_id, name, display_prefix, last_four, scopes, created_at, expires_at, last_used_at, last_used_via, revoked_at "
            "FROM api_keys WHERE id = 't-used'"
        )[0]

        self.assertEqual(
            row,
            (
                "t-used", "u1", "laptop", PLAINTEXT_USED[:12], None, '["mcp"]', "2026-10-01T00:00:00+00:00",
                None, "2026-11-01T08:15:00.123456+00:00", "mcp", None,
            ),
        )

    def test_a_revoked_token_stays_revoked_and_names_its_owner_as_the_revoker(self):
        self.migration.up()

        row = self.rows("SELECT revoked_at, revoked_by FROM api_keys WHERE id = 't-revoked'")[0]

        self.assertEqual(row, ("2026-09-02T00:00:00+00:00", "u1"))

    def test_a_token_never_used_has_no_use_recorded(self):
        self.migration.up()

        self.assertEqual(self.rows("SELECT last_used_at, last_used_via FROM api_keys WHERE id = 't-unused'"), [(None, None)])

    def test_tokens_of_deleted_users_are_dropped(self):
        self.migration.up()

        self.assertEqual(self.rows("SELECT id FROM api_keys WHERE id = 't-orphan'"), [])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM api_keys"), [(3,)])

    def test_the_old_token_table_and_its_indexes_are_gone(self):
        self.migration.up()

        self.assertEqual(self.rows("SELECT name FROM sqlite_master WHERE name LIKE '%mcp_tokens%'"), [])

    def test_a_user_with_mcp_switched_off_is_moved_to_the_access_table(self):
        self.migration.up()

        self.assertEqual(
            self.rows("SELECT user_id, keys_enabled, mcp_enabled FROM api_key_user_access"),
            [("u2", 1, 0)],
        )

    def test_the_per_user_setting_and_every_override_of_it_are_gone(self):
        self.migration.up()

        self.assertEqual(self.rows("SELECT id FROM settings WHERE key = 'mcp_user_enabled'"), [])
        self.assertEqual(self.rows("SELECT id FROM user_settings WHERE id IN ('us-u2', 'us-u3')"), [])

    def test_the_instance_settings_are_seeded_with_the_decided_defaults(self):
        self.migration.up()

        rows = {
            key: (value, value_type, kind)
            for key, value, value_type, kind in self.rows(
                "SELECT key, value, value_type, type FROM settings WHERE key IN "
                "('api_keys_enabled', 'api_key_scopes_allowed', 'api_key_max_lifetime_days', 'mcp_tools_ceiling_policy')"
            )
        }

        self.assertEqual(rows["api_keys_enabled"], ("true", "boolean", "SYSTEM"))
        self.assertEqual(rows["api_key_max_lifetime_days"], ("0", "integer", "SYSTEM"))
        self.assertEqual(rows["mcp_tools_ceiling_policy"], ("all_on", "string", "SYSTEM"))
        value, value_type, kind = rows["api_key_scopes_allowed"]
        self.assertEqual((json.loads(value), value_type, kind), (["read", "generate", "content", "mcp"], "json", "SYSTEM"))

    def test_the_admin_scope_is_not_in_the_default_ceiling(self):
        self.migration.up()

        value = self.rows("SELECT value FROM settings WHERE key = 'api_key_scopes_allowed'")[0][0]

        self.assertNotIn("admin", json.loads(value))

    def test_mcp_enabled_is_left_as_it_was(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE settings SET value = 'true' WHERE key = 'mcp_enabled'")

        self.migration.up()

        self.assertEqual(self.rows("SELECT value FROM settings WHERE key = 'mcp_enabled'"), [("true",)])

    def test_running_it_again_changes_nothing(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE settings SET value = 'false' WHERE key = 'api_keys_enabled'")
            cursor.execute("UPDATE api_keys SET name = 'renamed' WHERE id = 't-used'")

        self.migration.up()

        self.assertEqual(self.rows("SELECT value FROM settings WHERE key = 'api_keys_enabled'"), [("false",)])
        self.assertEqual(self.rows("SELECT name FROM api_keys WHERE id = 't-used'"), [("renamed",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM api_keys"), [(3,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM api_key_user_access"), [(1,)])

    def test_the_secret_hash_stays_unique(self):
        self.migration.up()

        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO api_keys (id, user_id, name, secret_hash, display_prefix, scopes, created_at) "
                    "VALUES ('dup', 'u3', 'dup', ?, 'pui_', '[]', 'now')",
                    (digest(PLAINTEXT_USED),),
                )

    def test_deleting_a_user_removes_their_keys_events_and_access_row(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO api_key_events (id, key_id, owner_id, action, created_at) VALUES ('e1', 't-unused', 'u2', 'created', 'now')"
            )
            cursor.execute("DELETE FROM users WHERE id = 'u2'")

        self.assertEqual(self.rows("SELECT id FROM api_keys WHERE user_id = 'u2'"), [])
        self.assertEqual(self.rows("SELECT id FROM api_key_events"), [])
        self.assertEqual(self.rows("SELECT user_id FROM api_key_user_access"), [])

    def test_a_user_who_never_had_a_token_gets_no_access_row(self):
        self.migration.up()

        self.assertEqual(self.rows("SELECT user_id FROM api_key_user_access WHERE user_id IN ('u1', 'u3')"), [])

    def test_a_failure_after_the_copy_rolls_back_the_copy_the_drop_and_the_moves(self):
        good = self.migration._SETTINGS
        self.migration._SETTINGS = good + (("broken_setting", "x", "not_a_type", "fails the CHECK constraint"),)

        with self.assertRaises(sqlite3.IntegrityError):
            self.migration.up()

        self.assertEqual(self.rows("SELECT COUNT(*) FROM mcp_tokens"), [(4,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM api_keys"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM api_key_user_access"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM settings WHERE key = 'mcp_user_enabled'"), [(1,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM user_settings WHERE id IN ('us-u2', 'us-u3')"), [(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM settings WHERE key = 'api_keys_enabled'"), [(0,)])

        self.migration._SETTINGS = good
        self.migration.up()

        self.assertEqual(self.rows("SELECT COUNT(*) FROM api_keys"), [(3,)])
        self.assertEqual(self.rows("SELECT name FROM sqlite_master WHERE name = 'mcp_tokens'"), [])
        self.assertEqual(self.rows("SELECT user_id FROM api_key_user_access"), [("u2",)])
        for plaintext in (PLAINTEXT_USED, PLAINTEXT_REVOKED, PLAINTEXT_UNUSED):
            self.assertEqual(len(self.rows("SELECT id FROM api_keys WHERE secret_hash = ?", (digest(plaintext),))), 1)
