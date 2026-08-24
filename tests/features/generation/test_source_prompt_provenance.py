"""Tests for the Prompt Library provenance link: `Generation.source_prompt_id`
and the `GenerationRepository` reads that serve the per-prompt generations
endpoint and the prompt list's usage aggregates (`src.features.prompt_database.routes`).
"""

import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.platform.util.ids import generate_ulid


class TestSourcePromptProvenance(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user(
            user_id="other_user", username="otheruser", email="other@example.com"
        )

    def _create_generation(self, user_id: str, source_prompt_id=None, status: str = "completed",
                            created_at: str = None) -> str:
        gen_id = generate_ulid()
        self.repo.create(Generation(
            id=gen_id,
            preset_id="test_preset",
            form_data={"prompt": "test"},
            user_id=user_id,
            status=status,
            source_prompt_id=source_prompt_id,
        ))
        if created_at:
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE generations SET created_at = ? WHERE id = ?", (created_at, gen_id)
                )
        return gen_id

    # --- create + roundtrip ---------------------------------------------------

    def test_create_with_source_prompt_id_persists_and_roundtrips(self):
        gen_id = self._create_generation(self.user_id, source_prompt_id="prompt-1")

        fetched = self.repo.get_by_id(gen_id)

        self.assertEqual(fetched.source_prompt_id, "prompt-1")
        self.assertEqual(fetched.to_dict()["source_prompt_id"], "prompt-1")

    def test_create_without_source_prompt_id_defaults_to_none(self):
        gen_id = self._create_generation(self.user_id)

        fetched = self.repo.get_by_id(gen_id)

        self.assertIsNone(fetched.source_prompt_id)
        self.assertIsNone(fetched.to_dict()["source_prompt_id"])

    def test_dangling_source_prompt_id_is_stored_harmlessly(self):
        """A prompt can be deleted after generations reference it (no FK) -
        the column must accept and return an id that no longer resolves to
        any real prompt row."""
        gen_id = self._create_generation(self.user_id, source_prompt_id="deleted-prompt")

        fetched = self.repo.get_by_id(gen_id)

        self.assertEqual(fetched.source_prompt_id, "deleted-prompt")

    # --- get_by_source_prompt / count_by_source_prompt ------------------------

    def test_get_by_source_prompt_returns_only_matching_completed_generations(self):
        matching = self._create_generation(self.user_id, source_prompt_id="prompt-1")
        self._create_generation(self.user_id, source_prompt_id="prompt-2")
        self._create_generation(self.user_id, source_prompt_id=None)

        results = self.repo.get_by_source_prompt("prompt-1", self.user_id)

        self.assertEqual([g.id for g in results], [matching])

    def test_get_by_source_prompt_excludes_non_completed(self):
        self._create_generation(self.user_id, source_prompt_id="prompt-1", status="pending")
        self._create_generation(self.user_id, source_prompt_id="prompt-1", status="failed")

        results = self.repo.get_by_source_prompt("prompt-1", self.user_id)

        self.assertEqual(results, [])

    def test_get_by_source_prompt_scopes_to_the_owning_user(self):
        """A second user's generation must never surface through this side
        door, even when it carries the same source_prompt_id."""
        self._create_generation(self.other_user_id, source_prompt_id="prompt-1")

        results = self.repo.get_by_source_prompt("prompt-1", self.user_id)

        self.assertEqual(results, [])

    def test_get_by_source_prompt_orders_newest_first_and_paginates(self):
        older = self._create_generation(
            self.user_id, source_prompt_id="prompt-1", created_at="2026-01-01 00:00:00"
        )
        newer = self._create_generation(
            self.user_id, source_prompt_id="prompt-1", created_at="2026-01-02 00:00:00"
        )

        page = self.repo.get_by_source_prompt("prompt-1", self.user_id, limit=1, offset=0)
        self.assertEqual([g.id for g in page], [newer])

        second_page = self.repo.get_by_source_prompt("prompt-1", self.user_id, limit=1, offset=1)
        self.assertEqual([g.id for g in second_page], [older])

    def test_get_by_source_prompt_includes_files(self):
        from src.features.generation.records import File

        gen_id = self._create_generation(self.user_id, source_prompt_id="prompt-1")
        self.repo.add_file(gen_id, File(
            file_path="generations/x.png", file_type="IMAGE", user_id=self.user_id,
            is_final=True, thumbnail_small="generations/x_thumb.png",
        ))

        results = self.repo.get_by_source_prompt("prompt-1", self.user_id)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].files), 1)
        self.assertEqual(results[0].files[0].thumbnail_small, "generations/x_thumb.png")

    def test_count_by_source_prompt_matches_get_by_source_prompt(self):
        self._create_generation(self.user_id, source_prompt_id="prompt-1")
        self._create_generation(self.user_id, source_prompt_id="prompt-1")
        self._create_generation(self.user_id, source_prompt_id="prompt-2")

        self.assertEqual(self.repo.count_by_source_prompt("prompt-1", self.user_id), 2)
        self.assertEqual(self.repo.count_by_source_prompt("prompt-2", self.user_id), 1)
        self.assertEqual(self.repo.count_by_source_prompt("no-such-prompt", self.user_id), 0)

    # --- usage_stats_by_source_prompt -----------------------------------------

    def test_usage_stats_counts_every_status_and_tracks_last_used(self):
        self._create_generation(
            self.user_id, source_prompt_id="prompt-1", status="completed",
            created_at="2026-01-01 00:00:00",
        )
        self._create_generation(
            self.user_id, source_prompt_id="prompt-1", status="failed",
            created_at="2026-01-05 00:00:00",
        )

        stats = self.repo.usage_stats_by_source_prompt(["prompt-1"], self.user_id)

        self.assertEqual(stats["prompt-1"]["usage_count"], 2)
        self.assertEqual(stats["prompt-1"]["last_used_at"], "2026-01-05 00:00:00")

    def test_usage_stats_omits_zero_usage_prompts(self):
        stats = self.repo.usage_stats_by_source_prompt(["never-used"], self.user_id)

        self.assertEqual(stats, {})

    def test_usage_stats_scopes_to_the_owning_user(self):
        self._create_generation(self.other_user_id, source_prompt_id="prompt-1")

        stats = self.repo.usage_stats_by_source_prompt(["prompt-1"], self.user_id)

        self.assertEqual(stats, {})

    def test_usage_stats_empty_prompt_id_list_returns_empty_dict(self):
        self.assertEqual(self.repo.usage_stats_by_source_prompt([], self.user_id), {})
