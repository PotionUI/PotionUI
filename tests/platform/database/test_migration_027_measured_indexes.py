import importlib.util
import sqlite3
import sys
from pathlib import Path

from tests.fixtures.persistence_base import PersistenceTestBase

_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "src" / "platform" / "database" / "migrations"
)

_DROPPED = (
    "idx_mcp_tokens_hash",
    "idx_models_sha256",
    "idx_presets_preset_id",
    "idx_prompt_segments_parent",
    "idx_providers_model_provider",
    "idx_session_versions_session",
    "idx_settings_key",
    "idx_setup_step_attempts_run",
    "idx_template_segments_parent",
    "idx_user_groups_name",
    "idx_users_email",
    "idx_users_username",
)


def _load_migration(database):
    stem = "027_measured_indexes"
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration027MeasuredIndexes(PersistenceTestBase):
    def _plan(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"EXPLAIN QUERY PLAN {sql}", params)
            return " | ".join(row[3] for row in cursor.fetchall())

    def _index_names(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
            return {row[0] for row in cursor.fetchall()}

    def test_run_report_retention_reads_the_index_not_the_table(self):
        plan = self._plan(
            "SELECT generation_id FROM generation_run_reports WHERE created_at < datetime('now', ?)",
            ("-30 days",),
        )
        assert "idx_run_reports_created_generation" in plan
        assert "COVERING" in plan

    def test_favorites_page_uses_the_partial_index_without_a_sort(self):
        plan = self._plan(
            "SELECT g.id FROM generations g WHERE g.user_id = ? AND g.is_favorite = 1 "
            "ORDER BY g.created_at DESC, g.id DESC LIMIT 24",
            ("u1",),
        )
        assert "idx_generations_favorites_created" in plan
        assert "TEMP B-TREE" not in plan

    def test_the_unfiltered_history_page_keeps_its_own_index(self):
        plan = self._plan(
            "SELECT g.id FROM generations g WHERE g.user_id = ? "
            "ORDER BY g.created_at DESC, g.id DESC LIMIT 24",
            ("u1",),
        )
        assert "idx_generations_user_created_id" in plan

    def test_model_lookup_by_path_is_indexed(self):
        plan = self._plan("SELECT * FROM models WHERE file_path = ?", ("/models/x.safetensors",))
        assert "idx_models_file_path" in plan

    def test_newest_first_prompt_search_needs_no_sort(self):
        plan = self._plan(
            "SELECT * FROM prompts WHERE user_id = ? AND flattened_text LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?",
            ("u1", "%portrait%", 20),
        )
        assert "idx_prompts_user_updated" in plan
        assert "TEMP B-TREE" not in plan

    def test_duplicates_of_unique_indexes_are_gone(self):
        names = self._index_names()
        assert not [name for name in _DROPPED if name in names]

    def test_uniqueness_is_still_enforced_after_the_drop(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO settings (id, key, value, value_type, type) VALUES ('s1', 'dup_key', '1', 'string', 'SYSTEM')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO settings (id, key, value, value_type, type) VALUES ('s2', 'dup_key', '2', 'string', 'SYSTEM')"
                )

    def test_an_index_that_no_unique_index_covers_is_left_alone(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("CREATE INDEX idx_settings_key ON settings (key, value)")
        migration = _load_migration(self.db)

        migration.up()

        assert "idx_settings_key" in self._index_names()

    def test_running_it_twice_is_harmless(self):
        migration = _load_migration(self.db)
        migration.up()
        migration.up()
        assert "idx_models_file_path" in self._index_names()
