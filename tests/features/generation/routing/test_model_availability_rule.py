"""`rules.ModelAvailability` - ported from the pre-router
`GenerationOrchestrator._narrow_backends_by_availability` unit tests
unchanged in behavior.
"""

from unittest.mock import Mock, patch

import pytest

from src.features.generation.routing.contracts import Candidate, RoutingContext, RoutingRequest
from src.features.generation.routing.rules import ModelAvailability
from src.features.models.form_refs import make_model_ref


def _candidates(*backend_ids):
    return [Candidate(backend=Mock(backend_id=bid)) for bid in backend_ids]


def _ctx():
    return RoutingContext(backend_registry=Mock())


class TestModelAvailability:
    @pytest.mark.asyncio
    async def test_form_without_model_refs_does_not_narrow(self):
        """Legacy path values carry no identity, so nothing can be constrained."""
        candidates = _candidates("comfy_a")
        request = RoutingRequest(engine="comfyui", preset=Mock(), form_data={"checkpoint": "models/checkpoints/a.safetensors"})

        result = await ModelAvailability().apply(candidates, request, _ctx())

        assert not result[0].dropped

    @patch("src.features.models.availability_repository.model_availability_repo")
    @pytest.mark.asyncio
    async def test_unindexed_engine_skips_narrowing_rather_than_failing_everything(self, repo):
        """A configured-but-unindexed backend holds models; it has never been asked.

        Enforcing availability against an empty index would fail every generation on
        that engine instead of degrading to the previous behaviour.
        """
        repo.any_indexed.return_value = False
        candidates = _candidates("comfy_a")
        request = RoutingRequest(engine="comfyui", preset=Mock(), form_data={"checkpoint": make_model_ref("m1")})

        result = await ModelAvailability().apply(candidates, request, _ctx())

        assert not result[0].dropped

    @patch("src.features.models.availability.require_candidate_backends")
    @patch("src.features.models.availability_repository.model_availability_repo")
    @pytest.mark.asyncio
    async def test_indexed_engine_drops_backends_missing_a_model(self, repo, candidates_fn):
        repo.any_indexed.return_value = True
        candidates_fn.return_value = ["comfy_b"]
        candidates = _candidates("comfy_a", "comfy_b")
        request = RoutingRequest(
            engine="comfyui", preset=Mock(),
            form_data={"checkpoint": make_model_ref("m1"), "loras": [{"model": make_model_ref("m2")}]},
        )

        result = await ModelAvailability().apply(candidates, request, _ctx())

        by_id = {c.backend_id: c for c in result}
        assert by_id["comfy_a"].dropped
        assert not by_id["comfy_b"].dropped
        assert candidates_fn.call_args[0][1] == ["m1", "m2"]

    @patch("src.features.models.availability.require_candidate_backends")
    @patch("src.features.models.availability_repository.model_availability_repo")
    @pytest.mark.asyncio
    async def test_no_backend_holds_everything_raises_the_detailed_error(self, repo, candidates_fn):
        """The detailed explanation (which model blocks, where it lives) must reach the
        user, not a generic drop reason - it propagates out of `apply()` unchanged."""
        from src.features.models.availability import NoBackendHoldsAllModelsError

        repo.any_indexed.return_value = True
        candidates_fn.side_effect = NoBackendHoldsAllModelsError("'a.safetensors' is on Remote One")
        candidates = _candidates("comfy_a")
        request = RoutingRequest(engine="comfyui", preset=Mock(), form_data={"checkpoint": make_model_ref("m1")})

        with pytest.raises(NoBackendHoldsAllModelsError, match="Remote One"):
            await ModelAvailability().apply(candidates, request, _ctx())
