import hashlib
import json

from tests.platform.database._support import MIGRATIONS_DIR, MigrationCase, load_migration

LAST_V010_MIGRATION = "091_organize_instance_rules"

SESSION_TOKEN = "pui_mcp_" + "s" * 43
REVOKED_TOKEN = "pui_mcp_" + "r" * 43
IDLE_TOKEN = "pui_mcp_" + "i" * 43
SWITCHED_OFF_TOKEN = "pui_mcp_" + "o" * 43
GHOST_TOKEN = "pui_mcp_" + "g" * 43


def digest(plaintext):
    return hashlib.sha256(plaintext.encode()).hexdigest()


def all_migrations():
    return sorted(path.stem for path in MIGRATIONS_DIR.glob("*.py") if path.name != "__init__.py")


class TestV010DatabaseMigratesToHead(MigrationCase):
    PREREQUISITES = tuple(stem for stem in all_migrations() if stem <= LAST_V010_MIGRATION)

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            for user_id, account_type in (("admin", "ADMIN"), ("u1", "USER"), ("u2", "USER"), ("u3", "USER")):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', ?)",
                    (user_id, user_id, f"{user_id}@example.com", account_type),
                )
            tokens = (
                ("t-live", "u1", "laptop", SESSION_TOKEN, "2026-10-01T00:00:00+00:00", "2026-11-01T08:15:00.123456+00:00", None),
                ("t-revoked", "u1", "old", REVOKED_TOKEN, "2026-09-01T00:00:00+00:00", None, "2026-09-02T00:00:00+00:00"),
                ("t-idle", "u3", "phone", IDLE_TOKEN, "2026-10-05T00:00:00+00:00", None, None),
                ("t-off", "u2", "tablet", SWITCHED_OFF_TOKEN, "2026-10-06T00:00:00+00:00", None, None),
                ("t-ghost", "deleted-user", "gone", GHOST_TOKEN, "2026-10-07T00:00:00+00:00", None, None),
            )
            for token_id, user_id, name, plaintext, created, used, revoked in tokens:
                cursor.execute(
                    "INSERT INTO mcp_tokens (id, user_id, name, token_hash, token_prefix, created_at, last_used_at, revoked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (token_id, user_id, name, digest(plaintext), plaintext[:12], created, used, revoked),
                )
            cursor.execute("UPDATE settings SET value = 'true' WHERE key = 'mcp_enabled'")
            cursor.execute("SELECT id FROM settings WHERE key = 'mcp_user_enabled'")
            switch_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO user_settings (id, user_id, setting_id, value) VALUES ('us-u2', 'u2', ?, 'false')",
                (switch_id,),
            )
            cursor.execute(
                "INSERT INTO settings (id, key, value, value_type, type) "
                "VALUES ('s-llm', 'llm_default_provider', 'cfg-default', 'string', 'SYSTEM')"
            )
            cursor.execute(
                "INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at) "
                "VALUES ('cfg-default', 'start_generation', 0, 0, 'then')"
            )
            cursor.execute(
                "INSERT INTO user_disabled_tools (user_id, tool_name, created_at) VALUES ('u1', 'get_prompt', 'then')"
            )
        self.users_before = self.rows("SELECT id, username, account_type FROM users ORDER BY id")
        self.settings_before = dict(
            self.rows("SELECT key, value FROM settings WHERE key NOT IN ('mcp_user_enabled')")
        )

    def rows(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()]

    def migrate_to_head(self):
        for stem in all_migrations():
            if stem > LAST_V010_MIGRATION:
                load_migration(stem, self.db).up()

    def test_every_migration_after_the_release_runs_on_a_database_with_real_rows(self):
        self.migrate_to_head()

        self.assertTrue(self.columns("api_keys"))
        self.assertTrue(self.columns("kits"))

    def test_each_live_token_keeps_its_secret_owner_and_only_the_mcp_scope(self):
        self.migrate_to_head()

        for plaintext, owner in ((SESSION_TOKEN, "u1"), (IDLE_TOKEN, "u3"), (SWITCHED_OFF_TOKEN, "u2")):
            found = self.rows(
                "SELECT user_id, scopes, expires_at FROM api_keys WHERE secret_hash = ?", (digest(plaintext),)
            )
            self.assertEqual(found, [(owner, json.dumps(["mcp"]), None)], plaintext)

    def test_a_token_revoked_before_the_upgrade_is_still_revoked(self):
        self.migrate_to_head()

        row = self.rows("SELECT revoked_at FROM api_keys WHERE secret_hash = ?", (digest(REVOKED_TOKEN),))

        self.assertEqual(row, [("2026-09-02T00:00:00+00:00",)])

    def test_last_use_and_creation_time_survive(self):
        self.migrate_to_head()

        row = self.rows("SELECT created_at, last_used_at FROM api_keys WHERE id = 't-live'")

        self.assertEqual(row, [("2026-10-01T00:00:00+00:00", "2026-11-01T08:15:00.123456+00:00")])

    def test_the_global_mcp_switch_is_untouched(self):
        self.migrate_to_head()

        self.assertEqual(self.rows("SELECT value FROM settings WHERE key = 'mcp_enabled'"), [("true",)])

    def test_the_user_switched_off_stays_off_and_the_others_stay_on(self):
        self.migrate_to_head()

        self.assertEqual(
            self.rows("SELECT user_id, keys_enabled, mcp_enabled FROM api_key_user_access ORDER BY user_id"),
            [("u2", 1, 0)],
        )

    def test_the_chat_tool_choices_carry_over_to_the_mcp_tool_tables(self):
        self.migrate_to_head()

        self.assertEqual(self.rows("SELECT tool_name, enabled FROM mcp_tool_ceiling"), [("start_generation", 0)])
        opted_out = self.rows("SELECT tool_name, enabled FROM mcp_tool_defaults WHERE user_id = 'u1' AND tool_name = 'get_prompt'")
        self.assertEqual(opted_out, [("get_prompt", 0)])

    def test_users_and_unrelated_settings_are_untouched(self):
        self.migrate_to_head()

        self.assertEqual(self.rows("SELECT id, username, account_type FROM users ORDER BY id"), self.users_before)
        after = dict(self.rows("SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" for _ in self.settings_before), tuple(self.settings_before)))
        self.assertEqual(after, self.settings_before)

    def test_the_only_rows_not_carried_over_are_tokens_whose_owner_no_longer_exists(self):
        self.migrate_to_head()

        migrated = {row[0] for row in self.rows("SELECT id FROM api_keys")}

        self.assertEqual(migrated, {"t-live", "t-revoked", "t-idle", "t-off"})

    def test_running_the_whole_chain_twice_changes_nothing(self):
        self.migrate_to_head()
        first = self.rows("SELECT id, secret_hash, scopes, revoked_at FROM api_keys ORDER BY id")

        self.migrate_to_head_again()

        self.assertEqual(self.rows("SELECT id, secret_hash, scopes, revoked_at FROM api_keys ORDER BY id"), first)

    def migrate_to_head_again(self):
        for stem in ("095_api_keys", "096_mcp_tool_seed"):
            load_migration(stem, self.db).up()
