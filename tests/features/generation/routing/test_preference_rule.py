"""`rules.Preference` - never drops, only annotates which surviving
candidate is preferred (the engine's default backend, else the highest
`priority`) so the trace is self-explanatory. The actual pick is
`GenerationRouter.route()`'s job (delegated to
`BackendRegistry.select_backend_for_generation`), not this rule's.
"""

from unittest.mock import Mock

import pytest

from src.features.generation.routing.contracts import Candidate, RoutingContext, RoutingRequest
from src.features.generation.routing.rules import Preference


def _backend(backend_id, priority=0):
    backend = Mock(backend_id=backend_id)
    backend.config.priority = priority
    return backend


def _ctx(default_id=None):
    backend_registry = Mock()
    backend_registry.backend_config_store.get_default_backend.return_value = (
        Mock(id=default_id) if default_id else None
    )
    return RoutingContext(backend_registry=backend_registry)


class TestPreference:
    @pytest.mark.asyncio
    async def test_no_live_candidates_is_a_noop(self):
        candidates = [Candidate(backend=_backend("a"))]
        candidates[0].drop("excluded")

        result = await Preference().apply(candidates, RoutingRequest(engine="native", preset=Mock()), _ctx())

        assert result[0].reasons == ["excluded"]

    @pytest.mark.asyncio
    async def test_annotates_the_default_backend_only(self):
        candidates = [Candidate(backend=_backend("a")), Candidate(backend=_backend("b"))]

        result = await Preference().apply(candidates, RoutingRequest(engine="native", preset=Mock()), _ctx(default_id="b"))

        by_id = {c.backend_id: c for c in result}
        assert by_id["b"].reasons == ["default backend for this engine"]
        assert by_id["a"].reasons == []

    @pytest.mark.asyncio
    async def test_annotates_highest_priority_when_no_default(self):
        candidates = [Candidate(backend=_backend("a", priority=1)), Candidate(backend=_backend("b", priority=5))]

        result = await Preference().apply(candidates, RoutingRequest(engine="native", preset=Mock()), _ctx())

        by_id = {c.backend_id: c for c in result}
        assert "highest priority (5)" in by_id["b"].reasons[-1]
        assert by_id["a"].reasons == []

    @pytest.mark.asyncio
    async def test_default_not_among_live_candidates_falls_back_to_priority(self):
        candidates = [Candidate(backend=_backend("a", priority=1))]

        result = await Preference().apply(
            candidates, RoutingRequest(engine="native", preset=Mock()), _ctx(default_id="does-not-exist")
        )

        assert "highest priority" in result[0].reasons[-1]
