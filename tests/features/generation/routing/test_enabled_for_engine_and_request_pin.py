"""`rules.EnabledForEngine` (seeds the candidate list) and `rules.RequestPin`
(a `requested_backend_id` narrows to just that backend, or drops everything
naming why it couldn't)."""

from unittest.mock import Mock

import pytest

from src.features.generation.routing.contracts import Candidate, RoutingContext, RoutingRequest
from src.features.generation.routing.rules import EnabledForEngine, RequestPin


def _candidates(*backend_ids):
    return [Candidate(backend=Mock(backend_id=bid)) for bid in backend_ids]


def _ctx(backend_registry=None):
    return RoutingContext(backend_registry=backend_registry or Mock())


class TestEnabledForEngine:
    @pytest.mark.asyncio
    async def test_seeds_candidates_from_every_backend_for_the_engine(self):
        a, b = Mock(backend_id="a"), Mock(backend_id="b")
        backend_registry = Mock()
        backend_registry.get_backends_for_engine.return_value = [a, b]
        request = RoutingRequest(engine="native", preset=Mock())

        result = await EnabledForEngine().apply([], request, _ctx(backend_registry))

        assert [c.backend for c in result] == [a, b]
        assert all(not c.dropped for c in result)
        backend_registry.get_backends_for_engine.assert_called_once_with("native")

    @pytest.mark.asyncio
    async def test_no_backends_for_engine_seeds_an_empty_list(self):
        backend_registry = Mock()
        backend_registry.get_backends_for_engine.return_value = []

        result = await EnabledForEngine().apply([], RoutingRequest(engine="native", preset=Mock()), _ctx(backend_registry))

        assert result == []


class TestRequestPin:
    @pytest.mark.asyncio
    async def test_no_pin_is_a_noop(self):
        candidates = _candidates("a", "b")

        result = await RequestPin().apply(candidates, RoutingRequest(engine="native", preset=Mock()), _ctx())

        assert not any(c.dropped for c in result)
        assert not any(c.reasons for c in result)

    @pytest.mark.asyncio
    async def test_valid_pin_drops_every_other_candidate(self):
        candidates = _candidates("a", "b")
        request = RoutingRequest(engine="native", preset=Mock(), requested_backend_id="b")

        result = await RequestPin().apply(candidates, request, _ctx())

        by_id = {c.backend_id: c for c in result}
        assert by_id["a"].dropped
        assert not by_id["b"].dropped
        assert "requested backend 'b'" in by_id["b"].reasons[-1]

    @pytest.mark.asyncio
    async def test_invalid_pin_drops_everything_naming_why(self):
        candidates = _candidates("a", "b")
        request = RoutingRequest(engine="native", preset=Mock(), requested_backend_id="does-not-exist")

        result = await RequestPin().apply(candidates, request, _ctx())

        assert all(c.dropped for c in result)
        assert "does-not-exist" in result[0].reasons[-1]

    @pytest.mark.asyncio
    async def test_does_not_revive_an_already_dropped_matching_candidate(self):
        candidates = _candidates("a", "b")
        candidates[0].drop("disabled")
        request = RoutingRequest(engine="native", preset=Mock(), requested_backend_id="a")

        result = await RequestPin().apply(candidates, request, _ctx())

        assert all(c.dropped for c in result)
        assert result[0].reasons == ["disabled"]
