"""001_baseline replaces the 143-file chain (001_create_configurations.py ..
140_add_inspirations_technique.py, including the duplicate-numbered 015/020/057
files) with a single migration that creates the exact schema that chain
produced on a fresh database, plus reseeds the three tables that carried
default data on a fresh install: `settings`, `keybinding_defaults`, and
`user_groups`.

The expected table/index/trigger/seed shapes below were captured by actually
running the old 143-file chain against a scratch database and diffing its
final `sqlite_master` + table contents (with ids/timestamps normalized)
against a fresh run of this migration - see the squash's PR description for
the diff. This test pins that same contract going forward.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from src.platform.database.database import Database

_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "src" / "platform" / "database" / "migrations"
)

EXPECTED_TABLES = {
    'automation_run_nodes', 'automation_runs', 'automations', 'backends',
    'chat_llm_call_traces', 'chat_messages', 'chat_sessions',
    'collection_generations', 'collection_prompts', 'collection_uploads',
    'collections', 'configurations', 'downloads', 'enhancement_feedback',
    'files', 'generation_files', 'generation_models', 'generation_parameters',
    'generation_run_reports', 'generation_segment_phrasebook',
    'generation_segments', 'generation_sources', 'generation_stats',
    'generation_tags', 'generations', 'inspiration_collection_items',
    'inspiration_collections', 'inspiration_comments', 'inspiration_saves',
    'inspirations', 'instance_claim', 'keybinding_defaults', 'llm_commands',
    'llm_configurations', 'llm_memory', 'mcp_tokens', 'media_index_queue',
    'media_system_tags', 'model_attribute_definitions', 'model_availability',
    'model_collection_members', 'model_collections', 'model_files',
    'model_hash_cache', 'model_preview_media', 'model_tags', 'models',
    'notifications', 'phrasebook_categories', 'phrasebook_values',
    'plugin_hooks', 'plugin_pages', 'plugin_setting_audit', 'plugin_settings',
    'plugins', 'presets', 'prompt_segments', 'prompts', 'providers',
    'remote_execution_events', 'remote_executions', 'saved_segments',
    'segment_categories', 'segment_template_segments', 'segment_templates',
    'session_versions', 'sessions', 'settings', 'setup_runs',
    'setup_step_attempts', 'tags', 'tool_governance', 'upload_tags',
    'uploads', 'user_disabled_tools', 'user_group_llms', 'user_group_members',
    'user_group_models', 'user_group_presets', 'user_groups',
    'user_keybindings', 'user_llms', 'user_model_attributes',
    'user_model_meta', 'user_models', 'user_onboarding_state', 'user_presets',
    'user_settings', 'users', 'workspaces',
}

EXPECTED_KEYBINDING_IDS = {
    'show_help', 'open_chat', 'quick_search', 'toggle_sidebar',
    'open_quick_actions', 'start_generation', 'new_tab', 'close_tab',
    'toggle_left_panel', 'go_generate', 'go_history', 'go_library',
    'go_models', 'go_phrasebook', 'go_prompts', 'go_inspirations',
}

EXPECTED_LITERAL_SETTING_IDS = {
    'models_dir': 'setting_models_dir',
    'cache_dir': 'setting_cache_dir',
    'output_directory': 'setting_output_directory',
    'temp_directory': 'setting_temp_directory',
    'nsfw_filter': 'setting_nsfw_filter',
    'notification_preferences': 'setting_notification_preferences',
}


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration001BaselineFreshInstall(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        # The runner always creates this before running any pending
        # migration - reproduce that so up() sees the real precondition.
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE applied_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_name TEXT UNIQUE NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        self.migration = _load_migration("001_baseline", self.db)

    def tearDown(self):
        Database._instance = None

    def test_creates_the_full_table_set(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' AND name != 'applied_migrations'"
                ).fetchall()
            }
        self.assertEqual(names, EXPECTED_TABLES)

    def test_creates_the_full_index_and_trigger_set(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            index_count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            ).fetchone()[0]
            trigger_count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
            ).fetchone()[0]
        self.assertEqual(index_count, 182)
        self.assertEqual(trigger_count, 22)

    def test_seeds_settings(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT id, key, type FROM settings").fetchall()
        self.assertEqual(len(rows), 39)

        by_key = {row[1]: row[0] for row in rows}
        self.assertEqual(set(by_key), set(EXPECTED_LITERAL_SETTING_IDS) | {
            'file_storage_directory', 'storage_backend', 's3_bucket', 's3_region',
            's3_endpoint_url', 's3_access_key_id', 's3_secret_key', 's3_path_style',
            's3_prefix', 'media_nsfw_filter_mode', 'media_nsfw_blur_threshold',
            'native_attention_backend', 'native_torch_compile', 'native_stream_prefetch',
            'model_cache_scope', 'media_tagger_model', 'media_tagger_device',
            'media_tagger_auto_download', 'media_tagger_tag_threshold',
            'media_tagger_character_threshold', 'media_vision_model',
            'media_vision_device', 'media_vision_auto_download',
            'prompt_embedding_provider', 'prompt_embedding_model',
            'prompt_embedding_device', 'prompt_embedding_auto_download',
            'prompt_embedding_ollama_base_url', 'prompt_embedding_ollama_model',
            'registration_policy', 'mcp_enabled', 'mcp_user_enabled',
            'chat_llm_call_tracing',
        })

        # The handful of settings the old chain seeded with a stable literal
        # id (rather than a runtime-generated one) must keep that literal id.
        for key, expected_id in EXPECTED_LITERAL_SETTING_IDS.items():
            self.assertEqual(by_key[key], expected_id)

        # Every other setting got a fresh, non-literal id.
        for key, setting_id in by_key.items():
            if key not in EXPECTED_LITERAL_SETTING_IDS:
                self.assertFalse(setting_id.startswith("setting_"), key)

    def test_seeds_keybinding_defaults(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT id, source FROM keybinding_defaults").fetchall()
        self.assertEqual({row[0] for row in rows}, EXPECTED_KEYBINDING_IDS)
        self.assertTrue(all(row[1] == 'system' for row in rows))

    def test_seeds_user_groups(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT id, is_system FROM user_groups").fetchall()
        self.assertEqual({row[0] for row in rows}, {'all_admins', 'all_users'})
        self.assertTrue(all(row[1] == 1 for row in rows))

    def test_sets_wal_mode(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_every_other_table_starts_empty(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            for table in EXPECTED_TABLES - {'settings', 'keybinding_defaults', 'user_groups'}:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                self.assertEqual(count, 0, table)


class TestMigration001BaselineGuard(unittest.TestCase):
    """The maintainer's one existing database already has 001_create_configurations
    through 140_add_inspirations_technique recorded in `applied_migrations`.
    001_baseline must detect that and do nothing, letting the runner mark it
    applied against a schema this migration never touched."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE applied_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_name TEXT UNIQUE NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO applied_migrations (migration_name) VALUES ('013_create_settings')"
            )
            conn.commit()
        self.migration = _load_migration("001_baseline", self.db)

    def tearDown(self):
        Database._instance = None

    def test_skips_schema_creation_on_a_pre_squash_database(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' AND name != 'applied_migrations'"
                ).fetchall()
            }
        self.assertEqual(names, set())


if __name__ == '__main__':
    unittest.main()
