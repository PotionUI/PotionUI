"""Semantic (visual) history search: ranking, capping, and filter intersection."""

from tests.features.media_index.test_history_system_tags import HistoryTestBase

from src.features.generation.history_query import GenerationHistoryQuery


class FakeIndexer:
    """Stands in for ``MediaIndexer``; caps hits by ``limit`` like the
    real Chroma-backed ``search_gallery`` does, so tests can exercise the
    widening loop the same way the real vector store would force it."""

    def __init__(self, hits=None, error=None, collection_size=None):
        self.hits = hits or []
        self.error = error
        self.collection_size_value = (
            collection_size if collection_size is not None else len(self.hits)
        )
        self.calls = []

    def search_gallery(self, user_id, query, limit=100):
        self.calls.append({"user_id": user_id, "query": query, "limit": limit})
        if self.error:
            raise self.error
        return list(self.hits[:limit])

    def gallery_collection_size(self, user_id):
        return self.collection_size_value

    def all_gallery_generation_ids(self, user_id):
        """Models the real ranking-free id fetch: every distinct generation
        id in the collection, unbounded by any ``limit`` - so total counts
        can never trail behind however far the ranked-page query widened."""
        if self.error:
            return []
        seen = set()
        ordered = []
        for hit in self.hits:
            generation_id = hit.get("generation_id")
            if generation_id and generation_id not in seen:
                seen.add(generation_id)
                ordered.append(generation_id)
        return ordered


class SemanticHistoryTestBase(HistoryTestBase):
    def _semantic_query(self, hits=None, error=None, collection_size=None):
        self.search_manager = FakeIndexer(
            hits=hits, error=error, collection_size=collection_size
        )
        return GenerationHistoryQuery(
            self.generation_repo,
            media_index_repository=self.repo,
            media_indexer=self.search_manager,
        )

    def _generation_with_file(self, gen_id, file_id, status=None):
        gen = self.create_test_generation(gen_id, self.user_id)
        if status:
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE generations SET status = ? WHERE id = ?", (status, gen)
                )
        self._make_file(file_id, gen)
        return gen

    @staticmethod
    def _hit(file_id, generation_id, similarity):
        return {"file_id": file_id, "generation_id": generation_id, "similarity": similarity}


class TestSemanticOrdering(SemanticHistoryTestBase):
    def test_results_follow_vector_rank_not_created_at(self):
        for index in (1, 2, 3):
            self._generation_with_file(f"gen{index}", f"f{index}")
        query = self._semantic_query(hits=[
            self._hit("f3", "gen3", 0.30),
            self._hit("f1", "gen1", 0.28),
        ])

        result = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert [g["id"] for g in result["generations"]] == ["gen3", "gen1"]
        assert result["total"] == 2
        assert self.search_manager.calls == [
            {"user_id": self.user_id, "query": "castle", "limit": 100}
        ]

    def test_duplicate_generation_hits_collapse_to_best_rank(self):
        gen = self._generation_with_file("gen1", "f1")
        self._make_file("f2", gen)
        query = self._semantic_query(hits=[
            self._hit("f1", "gen1", 0.30),
            self._hit("f2", "gen1", 0.29),
        ])

        result = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert [g["id"] for g in result["generations"]] == ["gen1"]
        assert result["total"] == 1

    def test_page_payload_carries_files(self):
        self._generation_with_file("gen1", "f1")
        query = self._semantic_query(hits=[self._hit("f1", "gen1", 0.3)])

        result = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert [f["id"] for f in result["generations"][0]["files"]] == ["f1"]


class TestSemanticPagination(SemanticHistoryTestBase):
    def test_offset_and_limit_page_within_the_ranked_set(self):
        hits = []
        for index in range(1, 5):
            self._generation_with_file(f"gen{index}", f"f{index}")
            hits.append(self._hit(f"f{index}", f"gen{index}", 0.3 - index * 0.01))
        query = self._semantic_query(hits=hits)

        page = query.get_history(
            self.user_id, limit=2, offset=2, include_tags=False, semantic_query="castle"
        )

        assert [g["id"] for g in page["generations"]] == ["gen3", "gen4"]
        assert page["total"] == 4


class TestSemanticEdgeCases(SemanticHistoryTestBase):
    def test_no_hits_returns_empty_result(self):
        self._generation_with_file("gen1", "f1")
        query = self._semantic_query(hits=[])

        result = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert result["generations"] == []
        assert result["total"] == 0

    def test_missing_manager_returns_empty_result(self):
        self._generation_with_file("gen1", "f1")
        query = GenerationHistoryQuery(self.generation_repo, media_index_repository=self.repo)

        result = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert result["generations"] == []
        assert result["total"] == 0

    def test_search_errors_degrade_to_empty_result(self):
        self._generation_with_file("gen1", "f1")
        query = self._semantic_query(error=RuntimeError("model exploded"))

        result = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert result["generations"] == []
        assert result["total"] == 0

    def test_blank_semantic_query_falls_back_to_normal_listing(self):
        self._generation_with_file("gen1", "f1")
        query = self._semantic_query(hits=[])

        result = query.get_history(self.user_id, include_tags=False, semantic_query="   ")

        assert [g["id"] for g in result["generations"]] == ["gen1"]
        assert self.search_manager.calls == []


class TestSemanticFilterIntersection(SemanticHistoryTestBase):
    def test_sql_filters_still_apply_to_ranked_ids(self):
        self._generation_with_file("gen1", "f1", status="completed")
        self._generation_with_file("gen2", "f2", status="failed")
        query = self._semantic_query(hits=[
            self._hit("f2", "gen2", 0.31),
            self._hit("f1", "gen1", 0.30),
        ])

        result = query.get_history(
            self.user_id, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert [g["id"] for g in result["generations"]] == ["gen1"]
        assert result["total"] == 1

    def test_other_users_generations_never_leak(self):
        self._generation_with_file("gen1", "f1")
        other_user = self.create_test_user("other", "otheruser", "other@example.com")
        self.create_test_generation("gen-other", other_user)
        query = self._semantic_query(hits=[
            self._hit("fx", "gen-other", 0.31),
            self._hit("f1", "gen1", 0.30),
        ])

        result = query.get_history(self.user_id, include_tags=False, semantic_query="castle")

        assert [g["id"] for g in result["generations"]] == ["gen1"]


class TestSemanticWidensPastCappedTopK(SemanticHistoryTestBase):
    """A single fixed-size vector query intersected with SQL filters can miss
    a real filter match that simply ranks below the query's window - the
    query has to widen until either the page fills or the whole gallery
    collection has been scanned."""

    def test_filter_match_ranked_below_initial_top_k_is_not_dropped(self):
        # 150 generations ranked best (index 0) to worst (index 149); only
        # the one at index 120 - outside a single SEMANTIC_TOP_K=100 query -
        # is "completed". A capped, non-widening query would see nothing.
        hits = []
        target_index = 120
        for index in range(150):
            gen_id = f"gen{index}"
            status = "completed" if index == target_index else "processing"
            self._generation_with_file(gen_id, f"f{index}", status=status)
            hits.append(self._hit(f"f{index}", gen_id, 1.0 - index * 0.001))
        query = self._semantic_query(hits=hits)

        result = query.get_history(
            self.user_id, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert [g["id"] for g in result["generations"]] == ["gen120"]
        assert result["total"] == 1
        # First query capped at SEMANTIC_TOP_K (100); since that page held
        # no filter match, the query widened once more, covering the full
        # 150-item collection.
        assert [call["limit"] for call in self.search_manager.calls] == [100, 150]

    def test_widening_stops_once_the_page_is_filled(self):
        # 300 items; a match right after the first widened window (100 ->
        # 200) already fills a limit=1 page, so the loop must not keep
        # widening all the way to 300.
        hits = []
        for index in range(300):
            gen_id = f"gen{index}"
            status = "completed" if index in (110, 250) else "processing"
            self._generation_with_file(gen_id, f"f{index}", status=status)
            hits.append(self._hit(f"f{index}", gen_id, 1.0 - index * 0.001))
        query = self._semantic_query(hits=hits)

        result = query.get_history(
            self.user_id, limit=1, offset=0, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert [g["id"] for g in result["generations"]] == ["gen110"]
        # Exact total (gen110 AND gen250 are "completed"), independent of how
        # far the ranked-page query widened to fill a 1-item page.
        assert result["total"] == 2
        assert [call["limit"] for call in self.search_manager.calls] == [100, 200]

    def test_no_filter_match_anywhere_scans_the_whole_collection_once(self):
        hits = []
        for index in range(250):
            gen_id = f"gen{index}"
            self._generation_with_file(gen_id, f"f{index}", status="processing")
            hits.append(self._hit(f"f{index}", gen_id, 1.0 - index * 0.001))
        query = self._semantic_query(hits=hits)

        result = query.get_history(
            self.user_id, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert result["generations"] == []
        assert result["total"] == 0
        assert [call["limit"] for call in self.search_manager.calls] == [100, 200, 250]


class TestSemanticTotalIsExact(SemanticHistoryTestBase):
    """``total`` has to answer "how many matches exist", not "how many did
    the ranked-page query happen to see before it stopped widening" - those
    are different questions once a filter has more matches than one page."""

    def test_total_reflects_the_full_filtered_set_not_just_the_filled_page(self):
        # 300 generations, all "completed" (all match the filter), ranked
        # best (index 0) to worst (index 299). A page query for limit=20
        # only ever needs to widen to the first 100 - far short of all 300
        # matches - so total must come from somewhere else entirely.
        hits = []
        for index in range(300):
            gen_id = f"gen{index}"
            self._generation_with_file(gen_id, f"f{index}", status="completed")
            hits.append(self._hit(f"f{index}", gen_id, 1.0 - index * 0.001))
        query = self._semantic_query(hits=hits)

        first_page = query.get_history(
            self.user_id, limit=20, offset=0, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert first_page["total"] == 300
        assert [g["id"] for g in first_page["generations"]] == [f"gen{i}" for i in range(20)]

        last_page = query.get_history(
            self.user_id, limit=20, offset=280, include_tags=False,
            semantic_query="castle", status="completed",
        )

        assert last_page["total"] == 300
        assert [g["id"] for g in last_page["generations"]] == [
            f"gen{i}" for i in range(280, 300)
        ]

    def test_total_agrees_with_the_non_semantic_path_for_the_same_filters(self):
        hits = []
        for index in range(5):
            gen_id = f"gen{index}"
            self._generation_with_file(gen_id, f"f{index}", status="completed")
            hits.append(self._hit(f"f{index}", gen_id, 1.0 - index * 0.001))
        query = self._semantic_query(hits=hits)

        semantic_result = query.get_history(
            self.user_id, include_tags=False, semantic_query="castle", status="completed",
        )
        non_semantic_result = query.get_history(
            self.user_id, include_tags=False, status="completed",
        )

        assert semantic_result["total"] == non_semantic_result["total"] == 5