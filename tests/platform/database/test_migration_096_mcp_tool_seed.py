from tests.platform.database._support import MigrationCase

OLD = "2026-10-01T00:00:00+00:00"


class TestMigration096McpToolSeed(MigrationCase):
    MIGRATION = "096_mcp_tool_seed"
    PREREQUISITES = ("001_baseline", "095_api_keys")

    def pre_state(self):
        with self.db.get_cursor() as cursor:
            for user_id in ("u-keyed", "u-two-keys", "u-revoked", "u-none", "u-locked"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, 'x')",
                    (user_id, user_id, f"{user_id}@example.com"),
                )
            keys = (
                ("k1", "u-keyed", None), ("k2", "u-two-keys", None), ("k3", "u-two-keys", None),
                ("k4", "u-revoked", OLD), ("k5", "u-locked", None),
            )
            for key_id, user_id, revoked in keys:
                cursor.execute(
                    "INSERT INTO api_keys (id, user_id, name, secret_hash, display_prefix, scopes, created_at, revoked_at) "
                    "VALUES (?, ?, ?, ?, 'pui_', '[\"mcp\"]', ?, ?)",
                    (key_id, user_id, key_id, f"hash-{key_id}", OLD, revoked),
                )
            cursor.execute(
                "INSERT INTO settings (id, key, value, value_type, type) VALUES ('s-llm', 'llm_default_provider', 'cfg-default', 'string', 'SYSTEM')"
            )
            governance = (
                ("cfg-default", "start_generation", 0, 0),
                ("cfg-default", "list_presets", 1, 0),
                ("cfg-default", "delete_prompt", 1, 1),
                ("cfg-default", "write_memory", 0, 1),
                ("cfg-other", "search_gallery", 0, 0),
            )
            for config_id, tool, enabled, locked in governance:
                cursor.execute(
                    "INSERT INTO tool_governance (llm_config_id, tool_name, enabled, locked, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (config_id, tool, enabled, locked, OLD),
                )
            opt_outs = (
                ("u-keyed", "get_prompt"), ("u-keyed", "add_prompt"), ("u-keyed", "run_generation"),
                ("u-keyed", "delete_prompt"),
                ("u-revoked", "get_prompt"), ("u-none", "get_prompt"),
                ("u-two-keys", "list_presets"),
            )
            for user_id, tool in opt_outs:
                cursor.execute(
                    "INSERT INTO user_disabled_tools (user_id, tool_name, created_at) VALUES (?, ?, ?)", (user_id, tool, OLD)
                )

    def rows(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()]

    def defaults_of(self, user_id):
        return dict(self.rows("SELECT tool_name, enabled FROM mcp_tool_defaults WHERE user_id = ?", (user_id,)))

    def test_every_write_tool_in_todays_surface_is_kept_on_for_a_user_with_a_key(self):
        self.migration.up()

        defaults = self.defaults_of("u-two-keys")

        assert {name for name, enabled in defaults.items() if enabled == 1} == set(self.migration._WRITE_TOOLS)

    def test_the_write_list_is_the_twenty_six_tools_the_surface_pinned(self):
        self.assertEqual(len(self.migration._WRITE_TOOLS), 26)
        self.assertEqual(len(self.migration._READ_TOOLS), 19)
        self.assertEqual(len(set(self.migration._SURFACE)), 45)

    def test_a_user_who_opted_a_tool_out_in_chat_has_it_off_in_their_mcp_default(self):
        self.migration.up()

        defaults = self.defaults_of("u-keyed")

        self.assertEqual(defaults["get_prompt"], 0)
        self.assertEqual(defaults["add_prompt"], 0)
        self.assertEqual(defaults["write_memory"], 1)
        self.assertEqual(defaults["start_generation"], 1)

    def test_an_opt_out_for_a_tool_outside_todays_surface_is_not_carried_over(self):
        self.migration.up()

        self.assertNotIn("run_generation", self.defaults_of("u-keyed"))

    def test_an_opt_out_the_default_configuration_locked_on_was_ignored_in_chat_and_stays_ignored(self):
        self.migration.up()

        self.assertEqual(self.defaults_of("u-keyed")["delete_prompt"], 1)

    def test_a_read_tool_the_user_never_opted_out_of_gets_no_row_so_it_stays_the_default(self):
        self.migration.up()

        defaults = self.defaults_of("u-keyed")

        self.assertNotIn("list_presets", defaults)
        self.assertNotIn("read_memory", defaults)

    def test_a_read_tool_opted_out_in_chat_is_off_for_that_user_only(self):
        self.migration.up()

        self.assertEqual(self.defaults_of("u-two-keys")["list_presets"], 0)
        self.assertNotIn("list_presets", self.defaults_of("u-keyed"))

    def test_a_user_with_two_keys_is_seeded_once(self):
        self.migration.up()

        self.assertEqual(
            self.rows("SELECT COUNT(*) FROM mcp_tool_defaults WHERE user_id = 'u-two-keys' AND tool_name = 'add_prompt'"),
            [(1,)],
        )

    def test_a_user_with_only_a_revoked_key_or_none_gets_clean_defaults(self):
        self.migration.up()

        self.assertEqual(self.rows("SELECT COUNT(*) FROM mcp_tool_defaults WHERE user_id IN ('u-revoked', 'u-none')"), [(0,)])

    def test_nobody_gets_a_policy_row_so_new_tools_arrive_read_only(self):
        self.migration.up()

        self.assertEqual(self.rows("SELECT COUNT(*) FROM mcp_tool_default_policy"), [(0,)])

    def test_the_tools_the_administrator_turned_off_in_the_default_chat_configuration_become_ceiling_rows(self):
        self.migration.up()

        self.assertEqual(
            self.rows("SELECT tool_name, enabled FROM mcp_tool_ceiling ORDER BY tool_name"),
            [("start_generation", 0), ("write_memory", 0)],
        )

    def test_a_tool_turned_off_for_another_configuration_does_not_reach_the_ceiling(self):
        self.migration.up()

        self.assertEqual(self.rows("SELECT * FROM mcp_tool_ceiling WHERE tool_name = 'search_gallery'"), [])

    def test_without_a_default_configuration_there_are_no_ceiling_rows(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM settings WHERE key = 'llm_default_provider'")

        self.migration.up()

        self.assertEqual(self.rows("SELECT COUNT(*) FROM mcp_tool_ceiling"), [(0,)])

    def test_the_chat_tables_are_left_as_they_were(self):
        before = (
            self.rows("SELECT * FROM tool_governance ORDER BY llm_config_id, tool_name"),
            self.rows("SELECT * FROM user_disabled_tools ORDER BY user_id, tool_name"),
        )

        self.migration.up()

        self.assertEqual(before, (
            self.rows("SELECT * FROM tool_governance ORDER BY llm_config_id, tool_name"),
            self.rows("SELECT * FROM user_disabled_tools ORDER BY user_id, tool_name"),
        ))

    def test_running_it_again_changes_nothing(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE mcp_tool_defaults SET enabled = 0 WHERE user_id = 'u-keyed' AND tool_name = 'start_generation'")
            cursor.execute("DELETE FROM mcp_tool_ceiling WHERE tool_name = 'write_memory'")
        after_first = self.rows("SELECT user_id, tool_name, enabled FROM mcp_tool_defaults ORDER BY user_id, tool_name")

        self.migration.up()

        self.assertEqual(self.defaults_of("u-keyed")["start_generation"], 0)
        self.assertEqual(
            self.rows("SELECT user_id, tool_name, enabled FROM mcp_tool_defaults ORDER BY user_id, tool_name"), after_first
        )
