"""What semantic history costs per request: embeddings, vector queries, rows.

The correctness of the ranking, the widening and the exact total lives in
``tests/features/media_index/test_semantic_history.py``. This file pins the
work done to reach those answers, because every one of them used to be paid
for once per widening pass or once per indexed generation.
"""

from tests.features.media_index.test_semantic_history import (
    FakeIndexer,
    SemanticHistoryTestBase,
)

from src.features.generation.history_query import GenerationHistoryQuery


class CountingRepository:
    """Delegates to a real repository, counting what each request costs.

    ``rows_materialized`` is ``Generation`` objects built (the expensive
    unit - each parses its stored form data); ``id_filter_batches`` is the
    candidate lists handed to the id-only filter, one entry per call.
    """

    def __init__(self, inner):
        self._inner = inner
        self.rows_materialized = 0
        self.id_filter_batches = []

    def get_all(self, *args, **kwargs):
        rows = self._inner.get_all(*args, **kwargs)
        self.rows_materialized += len(rows)
        return rows

    def matching_generation_ids(self, generation_ids, **filters):
        self.id_filter_batches.append(list(generation_ids))
        return self._inner.matching_generation_ids(generation_ids, **filters)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class SemanticCostTestBase(SemanticHistoryTestBase):
    def _counting_query(self, hits=None, collection_size=None, rankings=None):
        self.search_manager = FakeIndexer(
            hits=hits, collection_size=collection_size, rankings=rankings
        )
        self.counting_repo = CountingRepository(self.generation_repo)
        return GenerationHistoryQuery(
            self.counting_repo,
            media_index_repository=self.repo,
            media_indexer=self.search_manager,
        )

    def _gallery(self, size, completed_indexes=None):
        """``size`` generations ranked best-first; ``completed_indexes`` (or
        all of them, when omitted) get status "completed"."""
        hits = []
        for index in range(size):
            gen_id = f"gen{index}"
            completed = completed_indexes is None or index in completed_indexes
            self._generation_with_file(
                gen_id, f"f{index}", status="completed" if completed else "processing"
            )
            hits.append(self._hit(f"f{index}", gen_id, 1.0 - index * 0.001))
        return hits


class TestEmbeddingIsReusedAcrossWideningPasses(SemanticCostTestBase):
    def test_query_is_embedded_once_however_far_the_search_widens(self):
        # Nothing matches, so the search widens all the way: 100 -> 200 ->
        # 250. Text encoding is the expensive half of a gallery search and
        # the query text never changes, so it may only happen once.
        query = self._counting_query(hits=self._gallery(250, completed_indexes=set()))

        result = query.get_history(
            self.user_id, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert result["generations"] == []
        assert self.search_manager.embed_calls == ["castle"]
        assert [call["limit"] for call in self.search_manager.calls] == [100, 200, 250]

    def test_every_pass_searches_with_the_same_vector(self):
        query = self._counting_query(hits=self._gallery(250, completed_indexes=set()))

        query.get_history(
            self.user_id, include_tags=False,
            semantic_query="castle", status="completed",
        )

        vectors = [call["embedding"] for call in self.search_manager.calls]
        assert len(vectors) == 3
        assert all(vector == vectors[0] for vector in vectors)


class TestWideningFiltersOnlyWhatItAdded(SemanticCostTestBase):
    def test_each_pass_filters_only_the_ids_beyond_the_previous_cutoff(self):
        query = self._counting_query(hits=self._gallery(250, completed_indexes=set()))

        query.get_history(
            self.user_id, include_tags=False,
            semantic_query="castle", status="completed",
        )

        # Three passes over windows of 100/200/250. The batches they filter
        # are the *additions* - 100 + 100 + 50 - not the windows themselves,
        # which would re-filter the first 100 ids three times over.
        page_batches = self.counting_repo.id_filter_batches[:-1]
        assert [len(batch) for batch in page_batches] == [100, 100, 50]
        filtered = [gen_id for batch in page_batches for gen_id in batch]
        assert len(filtered) == len(set(filtered)) == 250

    def test_ranked_order_survives_being_filtered_in_pieces(self):
        # Matches sit either side of every widening boundary, so a correct
        # result can only come from concatenating the passes in order.
        query = self._counting_query(
            hits=self._gallery(250, completed_indexes={5, 150, 240})
        )

        result = query.get_history(
            self.user_id, limit=10, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert [g["id"] for g in result["generations"]] == ["gen5", "gen150", "gen240"]

    def test_multi_file_generations_reach_the_filter_once(self):
        gen = self._generation_with_file("gen1", "f1", status="completed")
        self._make_file("f2", gen)
        self._make_file("f3", gen)
        query = self._counting_query(hits=[
            self._hit("f1", "gen1", 0.30),
            self._hit("f2", "gen1", 0.29),
            self._hit("f3", "gen1", 0.28),
        ])

        result = query.get_history(
            self.user_id, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert [g["id"] for g in result["generations"]] == ["gen1"]
        assert result["total"] == 1
        assert self.counting_repo.id_filter_batches[0] == ["gen1"]


class TestWideningTrustsOnlyTheRankingInHand(SemanticCostTestBase):
    """Nothing holds the vector index still between widening passes, and the
    relative cutoff is recomputed per call, so a later ranking is not the
    earlier one plus a tail. Carrying the ordered result across passes would
    freeze the first pass's order and keep ids a later pass no longer ranks."""

    def _generations(self, *specs):
        for gen_id, status in specs:
            self._generation_with_file(gen_id, f"file-{gen_id}", status=status)

    def test_a_reordered_widened_ranking_wins_over_the_earlier_one(self):
        # Pass 1 ranks [a, b]; only `a` matches. Pass 2 ranks [c, a, b, d],
        # putting `c` ahead of `a` - so the answer is [c, a, d]. Appending
        # pass 2's additions to pass 1's matches would answer [a, c, d].
        self._generations(
            ("a", "completed"), ("b", "processing"),
            ("c", "completed"), ("d", "completed"),
        )
        query = self._counting_query(
            hits=[self._hit(f"file-{g}", g, 0.3) for g in ("c", "a", "b", "d")],
            collection_size=150,
            rankings=[
                [self._hit("file-a", "a", 0.30), self._hit("file-b", "b", 0.29)],
                [self._hit("file-c", "c", 0.40), self._hit("file-a", "a", 0.30),
                 self._hit("file-b", "b", 0.29), self._hit("file-d", "d", 0.20)],
            ],
        )

        result = query.get_history(
            self.user_id, limit=4, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert [g["id"] for g in result["generations"]] == ["c", "a", "d"]
        assert self.search_manager.embed_calls == ["castle"]
        assert [call["limit"] for call in self.search_manager.calls] == [100, 150]

    def test_a_hit_the_widened_ranking_drops_is_not_carried_forward(self):
        # `a` ranks in pass 1 and is gone from pass 2 - removed from the
        # index, or cut when the best hit moved. It must not survive in the
        # answer just because an earlier pass had already matched it.
        self._generations(("a", "completed"), ("b", "completed"))
        query = self._counting_query(
            hits=[self._hit("file-b", "b", 0.3)],
            collection_size=150,
            rankings=[
                [self._hit("file-a", "a", 0.30), self._hit("file-b", "b", 0.29)],
                [self._hit("file-b", "b", 0.29)],
            ],
        )

        result = query.get_history(
            self.user_id, limit=5, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert [g["id"] for g in result["generations"]] == ["b"]
        assert self.search_manager.embed_calls == ["castle"]
        assert [call["limit"] for call in self.search_manager.calls] == [100, 150]

    def test_an_id_seen_by_an_earlier_pass_is_never_filtered_twice(self):
        self._generations(
            ("a", "completed"), ("b", "processing"),
            ("c", "completed"), ("d", "completed"),
        )
        query = self._counting_query(
            hits=[self._hit(f"file-{g}", g, 0.3) for g in ("c", "a", "b", "d")],
            collection_size=150,
            rankings=[
                [self._hit("file-a", "a", 0.30), self._hit("file-b", "b", 0.29)],
                [self._hit("file-c", "c", 0.40), self._hit("file-a", "a", 0.30),
                 self._hit("file-b", "b", 0.29), self._hit("file-d", "d", 0.20)],
            ],
        )

        query.get_history(
            self.user_id, limit=4, include_tags=False,
            semantic_query="castle", status="completed",
        )

        page_batches = self.counting_repo.id_filter_batches[:-1]
        assert page_batches == [["a", "b"], ["c", "d"]]


class TestTotalDoesNotMaterializeTheGallery(SemanticCostTestBase):
    def test_only_the_requested_page_is_built_into_generations(self):
        # Every one of the 250 indexed generations matches the filter, so
        # the exact total is 250 - but a 20-item page may only ever build
        # 20 rows. Counting by materializing each candidate is what this
        # forbids.
        query = self._counting_query(hits=self._gallery(250))

        result = query.get_history(
            self.user_id, limit=20, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert result["total"] == 250
        assert [g["id"] for g in result["generations"]] == [f"gen{i}" for i in range(20)]
        assert self.counting_repo.rows_materialized == 20

    def test_a_later_page_materializes_only_that_page(self):
        query = self._counting_query(hits=self._gallery(250))

        result = query.get_history(
            self.user_id, limit=20, offset=200, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert result["total"] == 250
        assert [g["id"] for g in result["generations"]] == [
            f"gen{i}" for i in range(200, 220)
        ]
        assert self.counting_repo.rows_materialized == 20

    def test_a_page_with_no_matches_materializes_nothing(self):
        query = self._counting_query(hits=self._gallery(250, completed_indexes=set()))

        result = query.get_history(
            self.user_id, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert result["total"] == 0
        assert self.counting_repo.rows_materialized == 0

    def test_the_total_is_one_pass_over_the_indexed_ids(self):
        query = self._counting_query(hits=self._gallery(250))

        query.get_history(
            self.user_id, limit=20, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert self.search_manager.all_ids_calls == 1
        assert len(self.counting_repo.id_filter_batches[-1]) == 250


class TestTotalMatchesGroundTruth(SemanticCostTestBase):
    def _brute_force_total(self, **filters):
        indexed = self.search_manager.all_gallery_generation_ids(self.user_id)
        return len([
            gen for gen in self.generation_repo.get_all(**filters)
            if gen.id in set(indexed)
        ])

    def test_restrictive_filter_total_agrees_with_a_brute_force_count(self):
        completed = {3, 17, 101, 102, 199, 240}
        query = self._counting_query(hits=self._gallery(250, completed_indexes=completed))

        result = query.get_history(
            self.user_id, limit=2, include_tags=False,
            semantic_query="castle", status="completed",
        )

        expected = self._brute_force_total(user_id=self.user_id, status="completed")
        assert expected == len(completed)
        assert result["total"] == expected

    def test_permissive_filter_total_agrees_with_a_brute_force_count(self):
        query = self._counting_query(hits=self._gallery(120))

        result = query.get_history(
            self.user_id, limit=5, include_tags=False, semantic_query="castle",
        )

        assert result["total"] == self._brute_force_total(user_id=self.user_id)
        assert result["total"] == 120


class TestIndexMutationBetweenRequests(SemanticCostTestBase):
    def test_a_newly_indexed_generation_shows_up_on_the_next_request(self):
        query = self._counting_query(hits=self._gallery(3))

        first = query.get_history(self.user_id, include_tags=False, semantic_query="castle")
        assert [g["id"] for g in first["generations"]] == ["gen0", "gen1", "gen2"]
        assert first["total"] == 3

        self._generation_with_file("gen-new", "f-new", status="completed")
        self.search_manager.hits.insert(1, self._hit("f-new", "gen-new", 0.9995))
        self.search_manager.collection_size_value += 1

        second = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert [g["id"] for g in second["generations"]] == [
            "gen0", "gen-new", "gen1", "gen2"
        ]
        assert second["total"] == 4

    def test_a_removed_generation_drops_out_on_the_next_request(self):
        query = self._counting_query(hits=self._gallery(3))

        assert query.get_history(
            self.user_id, include_tags=False, semantic_query="castle"
        )["total"] == 3

        self.generation_repo.delete("gen1")
        self.search_manager.hits = [
            hit for hit in self.search_manager.hits if hit["generation_id"] != "gen1"
        ]
        self.search_manager.collection_size_value -= 1

        second = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert [g["id"] for g in second["generations"]] == ["gen0", "gen2"]
        assert second["total"] == 2


class TestExhaustedAndEmptyCollections(SemanticCostTestBase):
    def test_a_collection_smaller_than_the_first_window_is_queried_once(self):
        query = self._counting_query(hits=self._gallery(4, completed_indexes=set()))

        result = query.get_history(
            self.user_id, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert result["generations"] == []
        assert result["total"] == 0
        assert self.search_manager.embed_calls == ["castle"]
        assert [call["limit"] for call in self.search_manager.calls] == [100]

    def test_an_empty_collection_embeds_once_and_searches_once(self):
        query = self._counting_query(hits=[], collection_size=0)

        result = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert result["generations"] == []
        assert result["total"] == 0
        assert self.search_manager.embed_calls == ["castle"]
        assert len(self.search_manager.calls) == 1
        assert self.counting_repo.id_filter_batches == []
        assert self.counting_repo.rows_materialized == 0
