"""`GenerationRouter.route()` - the full built-in chain, the rule-trace
shape, and `NoEligibleBackendError` with its per-backend detail. Uses the
REAL `BackendRegistry.select_backend_for_generation` (via
`object.__new__`, mirroring `tests/features/generation/test_orchestrator_model_refs.py`)
for the router's final-pick step, so this suite never re-tests that
tie-break's own logic - only that the router reaches it with the right
candidate set.
"""

from unittest.mock import Mock

import pytest

from src.features.backends.backend_registry import BackendRegistry, NoBackendForEngineError
from src.features.generation.routing.contracts import NoEligibleBackendError
from src.features.generation.routing.registry import BUILTIN_RULES
from src.features.generation.routing.router import GenerationRouter
from src.features.generation.routing.contracts import RoutingRequest


def _backend(backend_id, name=None, priority=0):
    # `Mock(name=...)` is reserved for the mock's own repr, not an attribute -
    # set `.name` after construction instead (a real trap: `Candidate.name`
    # reads `backend.name`, and `NoEligibleBackendError`'s message does too).
    backend = Mock(backend_id=backend_id)
    backend.name = name or backend_id
    backend.config.priority = priority
    return backend


def _registry(backends, default_id=None):
    registry = object.__new__(BackendRegistry)
    registry.get_backends_for_engine = Mock(return_value=backends)
    # `select_backend_for_generation`'s default-backend branch resolves the
    # actual instance through this cache, not through `get_backends_for_engine`
    # - see `tests/features/backends/test_backend_registry_selection.py`.
    registry._backends_cache = {b.backend_id: b for b in backends}
    registry.backend_config_store = Mock()
    registry.backend_config_store.get_default_backend.return_value = (
        Mock(id=default_id) if default_id else None
    )
    return registry


def _router(backends, default_id=None, requirements_cache=None):
    registry = _registry(backends, default_id=default_id)
    return GenerationRouter(list(BUILTIN_RULES), backend_registry=registry, requirements_cache=requirements_cache)


class TestGenerationRouterChain:
    @pytest.mark.asyncio
    async def test_single_backend_is_chosen(self):
        a = _backend("a")
        router = _router([a])

        decision = await router.route(RoutingRequest(engine="native", preset=Mock(requirements=[])))

        assert decision.chosen is a
        assert len(decision.kept) == 1
        assert decision.dropped == []

    @pytest.mark.asyncio
    async def test_default_backend_wins_over_higher_priority(self):
        a = _backend("a", priority=10)
        b = _backend("b", priority=1)
        router = _router([a, b], default_id="b")

        decision = await router.route(RoutingRequest(engine="comfyui", preset=Mock(requirements=[])))

        assert decision.chosen is b

    @pytest.mark.asyncio
    async def test_rule_trace_covers_every_built_in_rule_in_order(self):
        router = _router([_backend("a")])

        decision = await router.route(RoutingRequest(engine="native", preset=Mock(requirements=[])))

        assert [t.rule for t in decision.rule_trace] == [
            "enabled_for_engine", "model_availability", "requirements_eligibility", "preference",
        ]
        assert all(t.ms >= 0 for t in decision.rule_trace)
        # The seeding rule goes from 0 -> 1; nothing else narrows this scenario.
        assert decision.rule_trace[0].before == 0
        assert decision.rule_trace[0].after == 1

    @pytest.mark.asyncio
    async def test_no_backend_for_engine_raises_no_eligible_backend(self):
        router = _router([])

        with pytest.raises(NoEligibleBackendError) as exc_info:
            await router.route(RoutingRequest(engine="comfyui", preset=Mock(requirements=[])))

        assert exc_info.value.decision.candidates == []
        assert "comfyui" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_requirements_eligibility_drops_the_only_backend_with_a_hard_miss(self):
        cache = Mock()
        cache.peek_backend_missing.return_value = ["FaceDetailer node"]
        router = _router([_backend("a")], requirements_cache=cache)
        preset = Mock(requirements=[{"type": "comfyui_node", "class_type": "FaceDetailer"}])

        with pytest.raises(NoEligibleBackendError, match="FaceDetailer node"):
            await router.route(RoutingRequest(engine="comfyui", preset=preset))

    @pytest.mark.asyncio
    async def test_model_availability_error_propagates_through_the_full_chain(self):
        from unittest.mock import patch
        from src.features.models.availability import NoBackendHoldsAllModelsError
        from src.features.models.form_refs import make_model_ref

        router = _router([_backend("a")])
        preset = Mock(requirements=[])
        form_data = {"checkpoint": make_model_ref("m1")}

        with patch("src.features.models.availability_repository.model_availability_repo") as repo, \
             patch("src.features.models.availability.require_candidate_backends") as candidates_fn:
            repo.any_indexed.return_value = True
            candidates_fn.side_effect = NoBackendHoldsAllModelsError("'a.safetensors' is on Remote One")

            with pytest.raises(NoBackendHoldsAllModelsError, match="Remote One"):
                await router.route(RoutingRequest(engine="comfyui", preset=preset, form_data=form_data))


class TestGenerationRouterBackgroundTasks:
    @pytest.mark.asyncio
    async def test_schedule_background_tracks_and_cleans_up_the_task(self):
        import asyncio

        router = GenerationRouter([], backend_registry=Mock())
        done = asyncio.Event()

        async def _work():
            done.set()

        router._schedule_background(_work())

        assert len(router._background_tasks) == 1
        await asyncio.wait_for(done.wait(), timeout=1)
        await asyncio.sleep(0)  # let the done-callback discard the finished task
        assert len(router._background_tasks) == 0
