"""`GenerationRepository.all_matching_ids` and `GenerationHistoryQuery.get_matching_ids`
- backing "select all N matching" bulk selection.

Unlike `matching_generation_ids` (filter a candidate id set), these answer
"which ids match the filters at all" without a candidate list, capped so a
bulk-selection request stays bounded regardless of history size.
"""

import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase

from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.repository import GenerationRepository


class MatchingIdsTestBase(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.query = GenerationHistoryQuery(generation_repo=self.repo)
        self.user_id = self.create_test_user()

    def _generation(self, generation_id, status="completed", favorite=False):
        self.create_test_generation(generation_id, self.user_id)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE generations SET status = ?, is_favorite = ? WHERE id = ?",
                (status, 1 if favorite else 0, generation_id),
            )
        return generation_id


class TestRepositoryAllMatchingIds(MatchingIdsTestBase):
    def test_returns_every_id_matching_the_filters(self):
        self._generation("a", status="completed")
        self._generation("b", status="failed")
        self._generation("c", status="completed")

        ids = self.repo.all_matching_ids(user_id=self.user_id, status="completed")

        assert set(ids) == {"a", "c"}

    def test_another_users_generation_never_matches(self):
        self._generation("mine")
        other = self.create_test_user("other", "otheruser", "other@example.com")
        self.create_test_generation("theirs", other)

        assert self.repo.all_matching_ids(user_id=self.user_id) == ["mine"]

    def test_limit_caps_the_result(self):
        for index in range(5):
            self._generation(f"g{index}")

        ids = self.repo.all_matching_ids(user_id=self.user_id, limit=2)

        assert len(ids) == 2

    def test_no_limit_returns_everything(self):
        for index in range(5):
            self._generation(f"g{index}")

        ids = self.repo.all_matching_ids(user_id=self.user_id)

        assert len(ids) == 5


class TestHistoryQueryGetMatchingIds(MatchingIdsTestBase):
    def test_total_is_exact_even_when_ids_are_capped(self):
        for index in range(5):
            self._generation(f"g{index}")

        original_cap = GenerationHistoryQuery._MATCHING_IDS_CAP
        GenerationHistoryQuery._MATCHING_IDS_CAP = 2
        try:
            result = self.query.get_matching_ids(user_id=self.user_id)
        finally:
            GenerationHistoryQuery._MATCHING_IDS_CAP = original_cap

        assert result["total"] == 5
        assert len(result["ids"]) == 2
        assert result["truncated"] is True

    def test_not_truncated_when_everything_fits(self):
        self._generation("a")
        self._generation("b")

        result = self.query.get_matching_ids(user_id=self.user_id)

        assert result["total"] == 2
        assert set(result["ids"]) == {"a", "b"}
        assert result["truncated"] is False

    def test_filters_apply_the_same_as_get_history(self):
        self._generation("a", favorite=True)
        self._generation("b", favorite=False)

        result = self.query.get_matching_ids(user_id=self.user_id, favorites_only=True)

        assert result["ids"] == ["a"]
        assert result["total"] == 1
