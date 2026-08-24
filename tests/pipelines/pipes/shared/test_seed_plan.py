"""Tests for the shared seed-planning helper.

Includes a regression case for the generator/chroma bug that treated the
seeds *list* as a dict (`seeds[_] if _ in seeds else ...`), which caused any
provided seed list to be effectively ignored past index 0.
"""
from unittest.mock import patch

from src.pipelines.pipes._shared.generation.seed_plan import plan_seeds


class TestPlanSeeds:

    def test_uses_provided_seeds_for_each_index(self):
        assert plan_seeds([10, 20, 30], config_seed=-1, quantity=3) == [10, 20, 30]

    def test_scalar_seed_normalised_to_list(self):
        assert plan_seeds(55, config_seed=-1, quantity=1) == [55]

    def test_none_input_seeds_falls_back_for_every_index(self):
        with patch("src.pipelines.pipes._shared.generation.seed_plan.generate_seed") as mock_gen:
            mock_gen.side_effect = [111, 222, 333]
            assert plan_seeds(None, config_seed=-1, quantity=3) == [111, 222, 333]
            assert mock_gen.call_count == 3

    def test_fallback_used_when_seeds_shorter_than_quantity(self):
        with patch("src.pipelines.pipes._shared.generation.seed_plan.generate_seed") as mock_gen:
            mock_gen.return_value = 99
            result = plan_seeds([42], config_seed=-1, quantity=2)
            assert result == [42, 99]
            mock_gen.assert_called_once_with(-1)

    def test_config_seed_forwarded_to_generate_seed(self):
        with patch("src.pipelines.pipes._shared.generation.seed_plan.generate_seed") as mock_gen:
            mock_gen.return_value = 7
            plan_seeds([], config_seed=123, quantity=1)
            mock_gen.assert_called_once_with(123)

    def test_quantity_zero_returns_empty_list(self):
        assert plan_seeds([1, 2, 3], config_seed=-1, quantity=0) == []

    def test_chroma_dict_membership_bug_regression(self):
        """Regression test for the exact chroma bug: a seed list [100] must
        resolve seed for index 0 to 100 (not silently fall through to random
        because `0 in [100]` is False under the old `_ in seeds` check).

        Under the old (buggy) expression `seeds[_] if _ in seeds else
        generate_seed(...)`, with seeds=[100] and quantity=3:
          - index 0: `0 in [100]` -> False -> random fallback (BUG: ignores
            the provided seed 100 entirely, since the list contains the
            value 100, not the index 100).
          - index 1, 2: also False -> random fallback.
        The fixed plan_seeds must instead use position-based resolution.
        """
        with patch("src.pipelines.pipes._shared.generation.seed_plan.generate_seed") as mock_gen:
            mock_gen.side_effect = [555, 777]
            result = plan_seeds([100], config_seed=-1, quantity=3)

            # index 0 must use the provided seed, not a random fallback.
            assert result[0] == 100
            # indices 1 and 2 fall back to random seeds (list too short).
            assert result[1:] == [555, 777]

    def test_seed_value_that_looks_like_an_index_does_not_confuse_position_based_lookup(self):
        """A seed list containing values that happen to equal small indices
        (e.g. [0, 1]) must still resolve strictly by position, proving the
        fix isn't accidentally re-deriving the old membership-check bug."""
        result = plan_seeds([0, 1], config_seed=-1, quantity=2)
        assert result == [0, 1]
