"""`GenerationRepository.matching_generation_ids` - filter a candidate set.

It answers in ids so a caller holding a vector-search ranking can page and
count it without building a row per candidate, which means two properties it
must never lose: the caller's order, and a length that is an exact total even
when the candidate list is longer than one SQL statement can bind.
"""

import importlib
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase

from src.features.generation import repository as repository_module
from src.features.generation.repository import GenerationRepository


class MatchingIdsTestBase(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        for module_path in (
            "src.features.generation.repository",
            "src.features.generation.file_repository",
            "src.features.tags.repository",
        ):
            importlib.import_module(module_path).db = self.db
        self.repo = GenerationRepository()
        self.user_id = self.create_test_user()

    def _generation(self, generation_id, status="completed", favorite=False):
        self.create_test_generation(generation_id, self.user_id)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE generations SET status = ?, is_favorite = ? WHERE id = ?",
                (status, 1 if favorite else 0, generation_id),
            )
        return generation_id


class TestFiltering(MatchingIdsTestBase):
    def test_returns_only_the_candidates_that_pass_the_filters(self):
        self._generation("a", status="completed")
        self._generation("b", status="failed")
        self._generation("c", status="completed")

        matched = self.repo.matching_generation_ids(
            ["a", "b", "c"], user_id=self.user_id, status="completed"
        )

        assert matched == ["a", "c"]

    def test_ids_outside_the_candidate_set_are_never_returned(self):
        self._generation("a")
        self._generation("b")

        assert self.repo.matching_generation_ids(["a"], user_id=self.user_id) == ["a"]

    def test_unknown_ids_are_dropped_rather_than_counted(self):
        self._generation("a")

        matched = self.repo.matching_generation_ids(
            ["ghost1", "a", "ghost2"], user_id=self.user_id
        )

        assert matched == ["a"]

    def test_another_users_generation_never_matches(self):
        self._generation("a")
        other = self.create_test_user("other", "otheruser", "other@example.com")
        self.create_test_generation("theirs", other)

        matched = self.repo.matching_generation_ids(["a", "theirs"], user_id=self.user_id)

        assert matched == ["a"]

    def test_agrees_with_count_by_status_on_the_same_filters(self):
        for index in range(6):
            self._generation(f"g{index}", favorite=index % 2 == 0)

        matched = self.repo.matching_generation_ids(
            [f"g{index}" for index in range(6)],
            user_id=self.user_id, favorites_only=True,
        )

        assert len(matched) == self.repo.count_by_status(
            user_id=self.user_id, favorites_only=True
        )
        assert matched == ["g0", "g2", "g4"]

    def test_empty_candidate_list_short_circuits(self):
        self._generation("a")

        assert self.repo.matching_generation_ids([], user_id=self.user_id) == []


class TestOrderAndDeduplication(MatchingIdsTestBase):
    def test_the_callers_order_is_preserved_not_the_tables(self):
        for generation_id in ("a", "b", "c"):
            self._generation(generation_id)

        ranked = ["c", "a", "b"]

        assert self.repo.matching_generation_ids(ranked, user_id=self.user_id) == ranked

    def test_repeated_candidates_are_counted_once(self):
        self._generation("a")
        self._generation("b")

        matched = self.repo.matching_generation_ids(
            ["a", "b", "a", "a"], user_id=self.user_id
        )

        assert matched == ["a", "b"]


class TestBatching(MatchingIdsTestBase):
    """The ids ride in as bound parameters, which SQLite caps per statement."""

    def test_the_candidate_list_is_split_across_statements(self):
        # `_build_filters` runs once per statement, so its call count is how
        # many statements the candidate list was spread over.
        original = repository_module._ID_FILTER_BATCH
        repository_module._ID_FILTER_BATCH = 2
        self.addCleanup(setattr, repository_module, "_ID_FILTER_BATCH", original)
        for index in range(5):
            self._generation(f"g{index}")

        statements = []
        inner = self.repo._build_filters

        def counting_build_filters(*args, **kwargs):
            statements.append(kwargs.get("generation_ids"))
            return inner(*args, **kwargs)

        self.repo._build_filters = counting_build_filters

        matched = self.repo.matching_generation_ids(
            [f"g{index}" for index in range(5)], user_id=self.user_id
        )

        assert [len(batch) for batch in statements] == [2, 2, 1]
        assert matched == [f"g{index}" for index in range(5)]

    def test_a_candidate_list_past_the_old_bind_limit_still_answers_exactly(self):
        # Well past the 999-variable cap older SQLite builds enforce. Only a
        # handful of the candidates exist, so a short answer would show up
        # here as a wrong result rather than a raised error.
        real = [self._generation(f"g{index}") for index in range(5)]
        candidates = real + [f"ghost{index}" for index in range(2000)]

        matched = self.repo.matching_generation_ids(candidates, user_id=self.user_id)

        assert matched == real

    def test_matches_spread_across_batches_are_all_found_in_order(self):
        original = repository_module._ID_FILTER_BATCH
        repository_module._ID_FILTER_BATCH = 2
        self.addCleanup(setattr, repository_module, "_ID_FILTER_BATCH", original)

        for index in range(5):
            self._generation(f"g{index}", status="completed" if index % 2 == 0 else "failed")
        candidates = [f"g{index}" for index in range(5)]

        matched = self.repo.matching_generation_ids(
            candidates, user_id=self.user_id, status="completed"
        )

        assert matched == ["g0", "g2", "g4"]

    def test_batching_does_not_double_count_a_repeated_candidate(self):
        original = repository_module._ID_FILTER_BATCH
        repository_module._ID_FILTER_BATCH = 2
        self.addCleanup(setattr, repository_module, "_ID_FILTER_BATCH", original)

        self._generation("a")
        self._generation("b")

        matched = self.repo.matching_generation_ids(
            ["a", "b", "a", "b", "a"], user_id=self.user_id
        )

        assert matched == ["a", "b"]
