import unittest

from tests.platform.database._support import MigrationCase, load_migration


class TestMigration071LlmOpenrouterType(MigrationCase):
    MIGRATION = "071_llm_openrouter_type"

    def _insert(self, config_id, config_type, base_url, **extra):
        columns = {
            "id": config_id,
            "name": f"name {config_id}",
            "type": config_type,
            "base_url": base_url,
            "model": "model",
            "system_message": "system",
            **extra,
        }
        with self.db.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO llm_configurations ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                tuple(columns.values()),
            )

    def _type(self, config_id):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT type FROM llm_configurations WHERE id = ?", (config_id,))
            return cursor.fetchone()[0]

    def _snapshot(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM llm_configurations ORDER BY id")
            return [tuple(row) for row in cursor.fetchall()]

    def test_openrouter_configs_of_either_openai_type_become_openrouter(self):
        urls = {
            "plain": "https://openrouter.ai/api/v1",
            "slash": "https://openrouter.ai/api/v1/",
            "upper": "HTTPS://OpenRouter.AI/api/v1",
            "port": "https://openrouter.ai:443/api/v1",
            "dot": "https://openrouter.ai./api/v1",
            "spaces": "  https://openrouter.ai/api/v1  ",
            "schemeless": "openrouter.ai/api/v1",
        }
        for config_id, url in urls.items():
            self._insert(f"{config_id}_openai", "openai", url)
            self._insert(f"{config_id}_compatible", "openai-compatible", url)

        self.migration.up()

        for config_id in urls:
            self.assertEqual(self._type(f"{config_id}_openai"), "openrouter", config_id)
            self.assertEqual(self._type(f"{config_id}_compatible"), "openrouter", config_id)

    def test_other_hosts_keep_their_type(self):
        for config_id, config_type, url in [
            ("hosted", "openai", "https://api.openai.com/v1"),
            ("local", "openai-compatible", "http://localhost:1234/v1"),
            ("lookalike", "openai-compatible", "https://openrouter.ai.example.net/v1"),
            ("suffix", "openai-compatible", "https://notopenrouter.ai/v1"),
            ("subdomain", "openai-compatible", "https://api.openrouter.ai/v1"),
            ("userinfo", "openai-compatible", "https://openrouter.ai@evil.example/v1"),
            ("path", "openai-compatible", "https://proxy.example/openrouter.ai/v1"),
        ]:
            self._insert(config_id, config_type, url)

        self.migration.up()

        self.assertEqual(
            [self._type(config_id) for config_id in ("hosted", "local", "lookalike", "suffix", "subdomain", "userinfo", "path")],
            ["openai", "openai-compatible", "openai-compatible", "openai-compatible", "openai-compatible", "openai-compatible", "openai-compatible"],
        )

    def test_other_provider_types_on_that_host_are_untouched(self):
        self._insert("ollama", "ollama", "https://openrouter.ai/api/v1")
        self._insert("deepseek", "deepseek", "https://openrouter.ai/api/v1")
        self._insert("native", "native", "")

        self.migration.up()

        self.assertEqual([self._type(config_id) for config_id in ("ollama", "deepseek", "native")], ["ollama", "deepseek", "native"])

    def test_configs_without_a_usable_host_are_left_alone(self):
        for config_id, url in [("blank", ""), ("spaces", "   "), ("no_host", "https://"), ("broken", "http://[::1")]:
            self._insert(config_id, "openai-compatible", url)

        self.migration.up()

        for config_id in ("blank", "spaces", "no_host", "broken"):
            self.assertEqual(self._type(config_id), "openai-compatible", config_id)

    def test_only_the_type_changes(self):
        self._insert(
            "keeps", "openai-compatible", "https://openrouter.ai/api/v1",
            api_key="secret", provider_options='{"reasoning_effort": "low"}', temperature=0.3, max_tokens=500, enabled=0,
        )
        before = self._snapshot()

        self.migration.up()

        after = self._snapshot()
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(llm_configurations)")
            names = [row[1] for row in cursor.fetchall()]
        changed = [name for name, old, new in zip(names, before[0], after[0]) if old != new and name != "updated_at"]
        self.assertEqual(changed, ["type"])

    def test_a_second_run_changes_nothing(self):
        self._insert("router", "openai-compatible", "https://openrouter.ai/api/v1")
        self._insert("local", "openai-compatible", "http://localhost:1234/v1")
        self.migration.up()
        once = self._snapshot()

        self.migration.up()

        self.assertEqual(self._snapshot(), once)
        self.assertEqual((self._type("router"), self._type("local")), ("openrouter", "openai-compatible"))

    def test_an_empty_table_is_fine(self):
        self.migration.up()

        self.assertEqual(self._snapshot(), [])

    def test_after_migration_070_an_openai_openrouter_config_ends_as_openrouter(self):
        self._insert("router", "openai", "https://openrouter.ai/api/v1")
        self._insert("hosted", "openai", "https://api.openai.com/v1")
        self._insert("local", "openai", "http://localhost:1234/v1")

        load_migration("070_llm_openai_compatible", self.db).up()
        self.migration.up()

        self.assertEqual(
            [self._type(config_id) for config_id in ("router", "hosted", "local")],
            ["openrouter", "openai", "openai-compatible"],
        )


if __name__ == "__main__":
    unittest.main()
