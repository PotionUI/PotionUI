"""`rules.EnabledForEngine` - seeds the candidate list from every enabled
backend of the request's engine."""

from unittest.mock import Mock

import pytest

from src.features.generation.routing.contracts import RoutingContext, RoutingRequest
from src.features.generation.routing.rules import EnabledForEngine


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
