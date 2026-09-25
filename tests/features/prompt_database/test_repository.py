"""Persistence tests for normalized rich Prompt aggregates."""

import sqlite3
from unittest.mock import patch

from src.features.segments.dto import RichSegment
from src.features.prompt_database.records import Prompt
from src.features.prompt_database.repository import (
    PromptRepository,
    flatten_segments,
    resolve_rich_segment_text,
)
from src.platform.util.ids import generate_ulid
from tests.fixtures.persistence_base import PersistenceTestBase


def _chip():
    return {
        "id": "chip-1",
        "categoryPath": "lighting mood",
        "valueId": "golden",
        "label": "Golden hour",
        "value": "warm golden light",
        "allValues": [
            {
                "id": "golden",
                "label": "Golden hour",
                "value": "warm golden light",
                "preview_file_id": "preview-1",
            }
        ],
        "shuffle": True,
        "autoRegen": True,
    }


class TestPromptRepository(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repository = PromptRepository()
        self.user_1 = self.create_test_user(
            "prompt-user-1", "promptuser1", "prompt1@example.com"
        )
        self.user_2 = self.create_test_user(
            "prompt-user-2", "promptuser2", "prompt2@example.com"
        )

    def test_reset_schema_replaces_poc_tables(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
        self.assertIn("prompts", tables)
        self.assertIn("prompt_segments", tables)
        self.assertIn("saved_segments", tables)
        self.assertNotIn("model_prompts", tables)

    def test_rich_round_trip_order_and_flattening(self):
        created = self.repository.create(
            Prompt(
                id=generate_ulid(),
                user_id=self.user_1,
                name=None,
                usage_hint="negative",
                tags=["portrait"],
                segments=[
                    RichSegment(
                        content="portrait in #[lighting mood]",
                        chips={"chip-1": _chip()},
                        name="Subject",
                        color="#123456",
                        description="Opening card",
                    ),
                    RichSegment(content="must not appear", enabled=False),
                    RichSegment(content="close-up"),
                ],
            )
        )

        self.assertEqual(
            created.flattened_text,
            "portrait in warm golden light close-up",
        )
        self.assertEqual([item.type for item in created.segments], [
            "content", "content", "content"
        ])
        self.assertEqual(created.segments[0].chips["chip-1"].valueId, "golden")
        self.assertTrue(created.segments[0].chips["chip-1"].shuffle)
        self.assertFalse(created.segments[1].enabled)
        self.assertEqual(created.segments[0].name, "Subject")
        self.assertEqual(created.usage_hint, "negative")
        self.assertIn("portrait in warm", created.display_name)

    def test_variables_round_trip_through_create_and_get(self):
        variables = {
            "mood": {"type": "text", "value": "noir"},
            "scene": {
                "type": "choice",
                "mode": "shuffle",
                "pinnedIndex": None,
                "options": ["day", "night"],
            },
        }
        created = self.repository.create(
            Prompt(
                id=generate_ulid(), user_id=self.user_1,
                segments=[RichSegment(content="a fox")], variables=variables,
            )
        )

        self.assertEqual(created.variables, variables)
        fetched = self.repository.get_by_id(created.id, self.user_1)
        self.assertEqual(fetched.variables, variables)

    def test_segment_resources_round_trip_through_get_and_bulk_read(self):
        created = self.repository.create(
            Prompt(
                id=generate_ulid(), user_id=self.user_1,
                segments=[
                    RichSegment(
                        content="@[references:uploads/a.png] waves",
                        resources={"res-1": {"field": "references", "item_key": "uploads/a.png"}},
                    ),
                    RichSegment(content="a fox"),
                ],
            )
        )

        fetched = self.repository.get_by_id(created.id, self.user_1)
        self.assertEqual(fetched.segments[0].resources["res-1"].item_key, "uploads/a.png")
        self.assertEqual(fetched.segments[1].resources, {})
        bulk = self.repository.get_by_ids([created.id], self.user_1)
        self.assertEqual(bulk[0].segments[0].resources["res-1"].field, "references")

    def test_variables_default_to_none_and_round_trip_none(self):
        created = self.repository.create(
            Prompt(id=generate_ulid(), user_id=self.user_1, segments=[RichSegment(content="a fox")])
        )

        self.assertIsNone(created.variables)
        fetched = self.repository.get_by_id(created.id, self.user_1)
        self.assertIsNone(fetched.variables)

    def test_update_replaces_variables(self):
        created = self.repository.create(
            Prompt(
                id=generate_ulid(), user_id=self.user_1,
                segments=[RichSegment(content="a fox")],
                variables={"mood": {"type": "text", "value": "noir"}},
            )
        )

        updated = self.repository.update(
            created.id, self.user_1,
            Prompt(
                id=created.id, user_id=self.user_1,
                segments=[RichSegment(content="a fox")], variables=None,
            ),
        )

        self.assertIsNone(updated.variables)
        fetched = self.repository.get_by_id(created.id, self.user_1)
        self.assertIsNone(fetched.variables)

    def test_complete_child_replacement_is_atomic(self):
        original = self.repository.create(
            Prompt(
                id=generate_ulid(),
                user_id=self.user_1,
                name="Original",
                segments=[RichSegment(content="A"), RichSegment(content="B")],
            )
        )
        assert original.id is not None
        before_ids = [segment.id for segment in original.segments]

        replacement = Prompt(
            id=original.id,
            user_id=self.user_1,
            name="Replacement",
            segments=[RichSegment(content="X"), RichSegment(content="Y")],
        )
        updated = self.repository.update(original.id, self.user_1, replacement)
        assert updated is not None
        self.assertEqual([segment.content for segment in updated.segments], ["X", "Y"])
        self.assertTrue(before_ids[0] not in {segment.id for segment in updated.segments})

        failing = Prompt(
            id=original.id,
            user_id=self.user_1,
            name="Must roll back",
            segments=[RichSegment(content="one"), RichSegment(content="two")],
        )
        with patch(
            "src.features.prompt_database.repository.generate_ulid",
            return_value="duplicate-child-id",
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                self.repository.update(original.id, self.user_1, failing)

        after = self.repository.get_by_id(original.id, self.user_1)
        assert after is not None
        self.assertEqual(after.name, "Replacement")
        self.assertEqual([segment.content for segment in after.segments], ["X", "Y"])

    def test_user_isolation_and_cascade(self):
        prompt = self.repository.create(
            Prompt(
                id=generate_ulid(),
                user_id=self.user_1,
                segments=[RichSegment(content="private")],
            )
        )
        assert prompt.id is not None
        self.assertIsNone(self.repository.get_by_id(prompt.id, self.user_2))
        self.assertFalse(self.repository.delete(prompt.id, self.user_2))
        self.assertTrue(self.repository.delete(prompt.id, self.user_1))
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM prompt_segments WHERE prompt_id = ?", (prompt.id,)
            )
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_get_all_and_count_filter_by_collection_id(self):
        """A 'prompts'-scope collection filters get_all/count the same way a
        History collection filters generations - mirrors GenerationRepository's
        collection_id filter."""
        from src.features.collections.repository import CollectionRepository

        in_prompt = self.repository.create(
            Prompt(id=generate_ulid(), user_id=self.user_1, segments=[RichSegment(content="in")])
        )
        out_prompt = self.repository.create(
            Prompt(id=generate_ulid(), user_id=self.user_1, segments=[RichSegment(content="out")])
        )

        collections = CollectionRepository()
        collection = collections.create("Favorites", self.user_1, "prompts")
        collections.add_prompt_members(collection.id, [in_prompt.id], self.user_1, "prompts")

        filtered = self.repository.get_all(user_id=self.user_1, collection_id=collection.id)
        self.assertEqual([p.id for p in filtered], [in_prompt.id])

        count = self.repository.count(self.user_1, collection_id=collection.id)
        self.assertEqual(count, 1)

        unfiltered = self.repository.get_all(user_id=self.user_1)
        self.assertEqual({p.id for p in unfiltered}, {in_prompt.id, out_prompt.id})

    def test_get_source_ids_scopes_by_user_provider_and_model(self):
        """The bulk-import dedupe surface: only this user's rows under this
        source_provider (and, when given, this model_id) count as known."""
        from src.features.models.repository import ModelRepository
        from src.features.models.records import Model

        models = ModelRepository()
        model_a = models.create(Model(filename="a.safetensors", file_path="/models/a.safetensors", model_type="checkpoint"))
        model_b = models.create(Model(filename="b.safetensors", file_path="/models/b.safetensors", model_type="checkpoint"))

        self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, segments=[RichSegment(content="a")],
            source_provider="civitai-provider", source_id="101", model_id=model_a.id,
        ))
        self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, segments=[RichSegment(content="b")],
            source_provider="civitai-provider", source_id="102", model_id=model_b.id,
        ))
        self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, segments=[RichSegment(content="c")],
            source_provider="manual", source_id="103", model_id=model_a.id,
        ))
        self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_2, segments=[RichSegment(content="d")],
            source_provider="civitai-provider", source_id="104", model_id=model_a.id,
        ))

        all_for_provider = self.repository.get_source_ids(self.user_1, "civitai-provider")
        self.assertEqual(all_for_provider, {"101", "102"})

        scoped_to_model = self.repository.get_source_ids(self.user_1, "civitai-provider", model_id=model_a.id)
        self.assertEqual(scoped_to_model, {"101"})

        self.assertEqual(self.repository.get_source_ids(self.user_1, "unknown-provider"), set())

    def test_at_least_one_child_is_required(self):
        with self.assertRaises(ValueError):
            self.repository.create(
                Prompt(id=generate_ulid(), user_id=self.user_1, segments=[])
            )

    def test_flattening_joins_enabled_segments_with_a_space(self):
        self.assertEqual(
            flatten_segments(
                [
                    RichSegment(content="alpha"),
                    RichSegment(content="ignored", enabled=False),
                    RichSegment(content="omega"),
                ]
            ),
            "alpha omega",
        )

    def test_flattening_wraps_prefix_and_suffix_byte_for_byte(self):
        self.assertEqual(
            flatten_segments(
                [
                    RichSegment(content="alpha", prefix="(", suffix=")"),
                    RichSegment(content="omega"),
                ]
            ),
            "(alpha) omega",
        )

    def test_flattening_empty_body_with_prefix_contributes_nothing(self):
        self.assertEqual(
            flatten_segments(
                [
                    RichSegment(content="   ", prefix="(", suffix=")"),
                    RichSegment(content="omega"),
                ]
            ),
            "omega",
        )

    def test_flattening_disabled_segment_with_affixes_contributes_nothing(self):
        self.assertEqual(
            flatten_segments(
                [
                    RichSegment(content="alpha", prefix="(", suffix=")", enabled=False),
                    RichSegment(content="omega"),
                ]
            ),
            "omega",
        )

    def test_resolve_rich_segment_text_hyphenated_path(self):
        chip = _chip()
        chip["categoryPath"] = "potion-light"
        chip["value"] = "warm glow"
        segment = RichSegment(content="#potion-light scene", chips={"chip-1": chip})

        self.assertEqual(resolve_rich_segment_text(segment), "warm glow scene")

    def test_resolve_rich_segment_text_leaves_trailing_period_as_prose(self):
        chip = _chip()
        chip["categoryPath"] = "potion-light"
        chip["value"] = "warm glow"
        segment = RichSegment(content="#potion-light.", chips={"chip-1": chip})

        self.assertEqual(resolve_rich_segment_text(segment), "warm glow.")

    def test_resolve_rich_segment_text_leaves_trailing_hyphen_as_prose(self):
        chip = _chip()
        chip["categoryPath"] = "potion-light"
        chip["value"] = "warm glow"
        segment = RichSegment(content="#potion-light-", chips={"chip-1": chip})

        self.assertEqual(resolve_rich_segment_text(segment), "warm glow-")

    def test_segment_prefix_and_suffix_round_trip(self):
        created = self.repository.create(
            Prompt(
                id=generate_ulid(),
                user_id=self.user_1,
                segments=[
                    RichSegment(content="a fox", prefix="(", suffix=")"),
                    RichSegment(content="a hound"),
                ],
            )
        )
        self.assertEqual(created.segments[0].prefix, "(")
        self.assertEqual(created.segments[0].suffix, ")")
        self.assertIsNone(created.segments[1].prefix)
        self.assertIsNone(created.segments[1].suffix)

        fetched = self.repository.get_by_id(created.id, self.user_1)
        assert fetched is not None
        self.assertEqual(fetched.segments[0].prefix, "(")
        self.assertEqual(fetched.segments[0].suffix, ")")

    def _create_prompt(self, *segments_content) -> Prompt:
        return self.repository.create(
            Prompt(
                id=generate_ulid(),
                user_id=self.user_1,
                segments=[RichSegment(content=content) for content in segments_content],
            )
        )

    def test_get_all_bulk_segments_match_single_row_helper(self):
        multi = self._create_prompt("alpha", "beta")
        single = self._create_prompt("gamma")

        results = self.repository.get_all(user_id=self.user_1)
        by_id = {prompt.id: prompt for prompt in results}

        for prompt in (multi, single):
            expected = self.repository.get_by_id(prompt.id, self.user_1)
            actual = by_id[prompt.id]
            self.assertEqual([s.model_dump() for s in actual.segments], [s.model_dump() for s in expected.segments])

    def test_get_by_ids_bulk_segments_match_single_row_helper(self):
        multi = self._create_prompt("alpha", "beta")
        single = self._create_prompt("gamma")

        results = self.repository.get_by_ids([multi.id, single.id], self.user_1)
        by_id = {prompt.id: prompt for prompt in results}

        for prompt in (multi, single):
            expected = self.repository.get_by_id(prompt.id, self.user_1)
            actual = by_id[prompt.id]
            self.assertEqual([s.model_dump() for s in actual.segments], [s.model_dump() for s in expected.segments])

    def test_text_search_bulk_segments_match_single_row_helper(self):
        multi = self._create_prompt("findme alpha", "beta")

        results = self.repository.text_search(self.user_1, "findme")
        self.assertEqual(len(results), 1)

        expected = self.repository.get_by_id(multi.id, self.user_1)
        self.assertEqual(
            [s.model_dump() for s in results[0].segments],
            [s.model_dump() for s in expected.segments],
        )

    def test_get_all_bounded_cursor_count(self):
        for i in range(5):
            self._create_prompt(f"content {i}", f"more {i}")

        original_get_cursor = self.db.get_cursor
        calls = {"count": 0}

        from contextlib import contextmanager

        @contextmanager
        def counting_get_cursor():
            calls["count"] += 1
            with original_get_cursor() as cursor:
                yield cursor

        with patch.object(self.db, "get_cursor", counting_get_cursor):
            results = self.repository.get_all(user_id=self.user_1)

        self.assertEqual(len(results), 5)
        self.assertEqual(calls["count"], 1)

    def test_get_all_bounded_select_count(self):
        for i in range(5):
            self._create_prompt(f"content {i}", f"more {i}")

        original_get_cursor = self.db.get_cursor
        statements = []

        from contextlib import contextmanager

        @contextmanager
        def tracing_get_cursor():
            with original_get_cursor() as cursor:
                cursor.connection.set_trace_callback(statements.append)
                try:
                    yield cursor
                finally:
                    cursor.connection.set_trace_callback(None)

        with patch.object(self.db, "get_cursor", tracing_get_cursor):
            results = self.repository.get_all(user_id=self.user_1)

        self.assertEqual(len(results), 5)
        self.assertEqual(len(statements), 2)

    def test_segments_bulk_chunks_over_sqlite_parameter_limit(self):
        ids = [f"missing-{i}" for i in range(501)]

        with self.db.get_cursor() as cursor:
            segments = self.repository._segments_bulk(cursor, ids)

        self.assertEqual(len(segments), 501)
        self.assertTrue(all(value == [] for value in segments.values()))

    def test_get_ids_and_text_returns_flattened_text_without_segments(self):
        prompt = self._create_prompt("hello world")

        rows = self.repository.get_ids_and_text(self.user_1)

        self.assertIn((prompt.id, prompt.flattened_text), rows)

    def test_get_ids_and_text_filters_by_model_id(self):
        from src.features.models.repository import ModelRepository
        from src.features.models.records import Model

        models = ModelRepository()
        model_a = models.create(Model(filename="a.safetensors", file_path="/models/a.safetensors", model_type="checkpoint"))
        model_b = models.create(Model(filename="b.safetensors", file_path="/models/b.safetensors", model_type="checkpoint"))

        matching = self.repository.create(
            Prompt(id=generate_ulid(), user_id=self.user_1, model_id=model_a.id, segments=[RichSegment(content="x")])
        )
        other = self.repository.create(
            Prompt(id=generate_ulid(), user_id=self.user_1, model_id=model_b.id, segments=[RichSegment(content="y")])
        )

        rows = self.repository.get_ids_and_text(self.user_1, model_id=model_a.id)

        ids = [row[0] for row in rows]
        self.assertIn(matching.id, ids)
        self.assertNotIn(other.id, ids)

    def test_get_all_filters_by_q_matches_name_or_flattened_text(self):
        named = self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, name="Golden Fox",
            segments=[RichSegment(content="unrelated body")],
        ))
        by_body = self._create_prompt("a wandering fox in the woods")
        other = self._create_prompt("a distant mountain")

        results = self.repository.get_all(user_id=self.user_1, q="fox")

        ids = {prompt.id for prompt in results}
        self.assertEqual(ids, {named.id, by_body.id})
        self.assertNotIn(other.id, ids)

    def test_get_all_filters_by_tags_any_of(self):
        portrait = self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, tags=["portrait", "warm"],
            segments=[RichSegment(content="a")],
        ))
        anime = self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, tags=["anime"],
            segments=[RichSegment(content="b")],
        ))
        untagged = self._create_prompt("c")

        results = self.repository.get_all(user_id=self.user_1, tags=["portrait", "anime"])

        ids = {prompt.id for prompt in results}
        self.assertEqual(ids, {portrait.id, anime.id})
        self.assertNotIn(untagged.id, ids)

    def test_get_all_has_variables_filter(self):
        with_vars = self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, variables={"subject": "fox"},
            segments=[RichSegment(content="a")],
        ))
        without_vars = self._create_prompt("b")

        with_only = self.repository.get_all(user_id=self.user_1, has_variables=True)
        self.assertEqual([p.id for p in with_only], [with_vars.id])

        without_only = self.repository.get_all(user_id=self.user_1, has_variables=False)
        self.assertEqual([p.id for p in without_only], [without_vars.id])

    def test_get_all_nsfw_excluded_by_default_and_included_on_request(self):
        clean = self._create_prompt("clean")
        flagged = self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, nsfw=True,
            segments=[RichSegment(content="explicit")],
        ))

        default_results = self.repository.get_all(user_id=self.user_1)
        self.assertEqual({p.id for p in default_results}, {clean.id})

        included_results = self.repository.get_all(user_id=self.user_1, nsfw="include")
        self.assertEqual({p.id for p in included_results}, {clean.id, flagged.id})

    def test_get_all_used_and_never_filters(self):
        from src.features.generation.records import Generation
        from src.features.generation.repository import GenerationRepository

        used_prompt = self._create_prompt("used")
        never_prompt = self._create_prompt("never")
        GenerationRepository().create(Generation(
            id=generate_ulid(), preset_id="p", form_data={}, user_id=self.user_1,
            status="completed", source_prompt_id=used_prompt.id,
        ))

        used_results = self.repository.get_all(user_id=self.user_1, used="used")
        self.assertEqual([p.id for p in used_results], [used_prompt.id])

        never_results = self.repository.get_all(user_id=self.user_1, used="never")
        self.assertEqual([p.id for p in never_results], [never_prompt.id])

    def test_get_all_used_after_filters_by_last_generation(self):
        from src.features.generation.records import Generation
        from src.features.generation.repository import GenerationRepository

        recent = self._create_prompt("recent")
        stale = self._create_prompt("stale")
        gen_repo = GenerationRepository()
        recent_gen = generate_ulid()
        stale_gen = generate_ulid()
        gen_repo.create(Generation(
            id=recent_gen, preset_id="p", form_data={}, user_id=self.user_1,
            status="completed", source_prompt_id=recent.id,
        ))
        gen_repo.create(Generation(
            id=stale_gen, preset_id="p", form_data={}, user_id=self.user_1,
            status="completed", source_prompt_id=stale.id,
        ))
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE generations SET created_at = ? WHERE id = ?", ("2026-01-10 00:00:00", recent_gen))
            cursor.execute("UPDATE generations SET created_at = ? WHERE id = ?", ("2026-01-01 00:00:00", stale_gen))

        results = self.repository.get_all(user_id=self.user_1, used_after="2026-01-05T00:00:00")

        self.assertEqual([p.id for p in results], [recent.id])

    def test_get_all_sorts_by_usage_count_and_last_used_at(self):
        from src.features.generation.records import Generation
        from src.features.generation.repository import GenerationRepository

        quiet = self._create_prompt("quiet")
        popular = self._create_prompt("popular")
        gen_repo = GenerationRepository()
        for _ in range(3):
            gen_repo.create(Generation(
                id=generate_ulid(), preset_id="p", form_data={}, user_id=self.user_1,
                status="completed", source_prompt_id=popular.id,
            ))

        desc = self.repository.get_all(user_id=self.user_1, sort_by="usage_count", sort_order="desc")
        self.assertEqual([p.id for p in desc], [popular.id, quiet.id])

        asc = self.repository.get_all(user_id=self.user_1, sort_by="usage_count", sort_order="asc")
        self.assertEqual([p.id for p in asc], [quiet.id, popular.id])

    def test_count_reflects_the_same_filters_as_get_all(self):
        self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, tags=["portrait"],
            segments=[RichSegment(content="a")],
        ))
        self._create_prompt("b")

        self.assertEqual(self.repository.count(self.user_1, tags=["portrait"]), 1)
        self.assertEqual(self.repository.count(self.user_1), 2)

    def test_tag_counts_sorted_by_count_then_tag_and_scoped_to_user(self):
        self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, tags=["portrait", "warm"],
            segments=[RichSegment(content="a")],
        ))
        self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, tags=["portrait"],
            segments=[RichSegment(content="b")],
        ))
        self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_2, tags=["portrait"],
            segments=[RichSegment(content="c")],
        ))

        counts = self.repository.tag_counts(self.user_1)

        self.assertEqual(counts, [("portrait", 2), ("warm", 1)])

    def test_tag_counts_excludes_nsfw_by_default(self):
        self.repository.create(Prompt(
            id=generate_ulid(), user_id=self.user_1, tags=["explicit"], nsfw=True,
            segments=[RichSegment(content="a")],
        ))

        self.assertEqual(self.repository.tag_counts(self.user_1), [])
        self.assertEqual(self.repository.tag_counts(self.user_1, nsfw="include"), [("explicit", 1)])
