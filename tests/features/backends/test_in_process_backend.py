"""Tests for src.features.backends.in_process_backend.InProcessBackend.

InProcessBackend is the base class for backends that execute pipelines inside
this process via GenerationManager on a background thread (native + comfyui
engines both use it). See docs/backends.md.
"""

import asyncio
from typing import Any, Dict
from unittest.mock import Mock

import pytest

from src.features.backends.in_process_backend import InProcessBackend


class ConcreteInProcessBackend(InProcessBackend):
    """Minimal concrete subclass - BaseBackend requires health_check/get_system_info."""

    async def health_check(self) -> Dict[str, Any]:
        return {"status": "healthy"}

    async def get_system_info(self) -> Dict[str, Any]:
        return {}


class PrepareTrackingBackend(ConcreteInProcessBackend):
    """Subclass that overrides prepare_pipes to inject a marker, so tests can
    assert prepare_pipes ran before generation_manager.generate() was called."""

    def prepare_pipes(self, pipes):
        return [{**p, "prepared": True} for p in pipes]


def make_backend_config(engine="native"):
    config = Mock()
    config.id = "backend-1"
    config.name = "Test Backend"
    config.engine = engine
    config.enabled = True
    config.priority = 1
    config.timeout_seconds = 300
    return config


@pytest.fixture
def generation_manager():
    manager = Mock()
    manager.generate = Mock()
    manager.cancel = Mock()
    return manager


@pytest.fixture
def emit():
    return Mock()


class TestPreparePipes:
    """prepare_pipes is called before execution, and its output is what
    reaches generation_manager.generate."""

    @pytest.mark.asyncio
    async def test_prepare_pipes_output_reaches_generation_manager(
        self, generation_manager, emit
    ):
        backend = PrepareTrackingBackend(make_backend_config(), generation_manager)
        pipes = [{"name": "generator", "config": {}}]

        await backend.start_generation({"generation_id": "gen1", "pipes": pipes}, emit)

        # Let the background task (_run -> asyncio.to_thread) complete.
        await asyncio.sleep(0.05)

        generation_manager.generate.assert_called_once()
        called_pipes = generation_manager.generate.call_args[0][0]
        assert called_pipes == [{"name": "generator", "config": {}, "prepared": True}]

    @pytest.mark.asyncio
    async def test_default_prepare_pipes_is_identity(self, generation_manager, emit):
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager)
        pipes = [{"name": "generator", "config": {"steps": 20}}]

        result = backend.prepare_pipes(pipes)

        assert result == pipes
        assert result is pipes

    @pytest.mark.asyncio
    async def test_prepare_pipes_called_before_run_is_scheduled(
        self, generation_manager, emit
    ):
        """prepare_pipes runs synchronously inside start_generation, before the
        background _run task is even created."""
        call_order = []

        class OrderTrackingBackend(ConcreteInProcessBackend):
            def prepare_pipes(self, pipes):
                call_order.append("prepare_pipes")
                return pipes

        def track_generate(*args, **kwargs):
            call_order.append("generate")

        generation_manager.generate.side_effect = track_generate

        backend = OrderTrackingBackend(make_backend_config(), generation_manager)
        await backend.start_generation(
            {"generation_id": "gen1", "pipes": [{"name": "x", "config": {}}]}, emit
        )
        await asyncio.sleep(0.05)

        assert call_order == ["prepare_pipes", "generate"]


class TestEmitOnCompletion:
    """emit(None) is always called in the finally block, even on failure."""

    @pytest.mark.asyncio
    async def test_emit_none_called_on_success(self, generation_manager, emit):
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager)

        await backend.start_generation(
            {"generation_id": "gen1", "pipes": [{"name": "x", "config": {}}]}, emit
        )
        await asyncio.sleep(0.05)

        emit.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_emit_none_called_when_generate_raises(self, generation_manager, emit):
        generation_manager.generate.side_effect = RuntimeError("boom")
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager)

        await backend.start_generation(
            {"generation_id": "gen1", "pipes": [{"name": "x", "config": {}}]}, emit
        )
        await asyncio.sleep(0.05)

        emit.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_generation_id_removed_from_active_after_completion(
        self, generation_manager, emit
    ):
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager)

        await backend.start_generation(
            {"generation_id": "gen1", "pipes": [{"name": "x", "config": {}}]}, emit
        )
        assert "gen1" in backend._active

        await asyncio.sleep(0.05)

        assert "gen1" not in backend._active

    @pytest.mark.asyncio
    async def test_generation_id_removed_from_active_when_generate_raises(
        self, generation_manager, emit
    ):
        generation_manager.generate.side_effect = RuntimeError("boom")
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager)

        await backend.start_generation(
            {"generation_id": "gen1", "pipes": [{"name": "x", "config": {}}]}, emit
        )
        await asyncio.sleep(0.05)

        assert "gen1" not in backend._active


class TestCancelGeneration:
    @pytest.mark.asyncio
    async def test_cancel_unknown_generation_returns_false(self, generation_manager):
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager)

        result = await backend.cancel_generation("does-not-exist")

        assert result is False
        generation_manager.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_active_generation_returns_true(self, generation_manager, emit):
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager)

        # Make generate() block briefly so the generation stays "active" while we cancel.
        import time
        generation_manager.generate.side_effect = lambda *a, **k: time.sleep(0.2)

        await backend.start_generation(
            {"generation_id": "gen1", "pipes": [{"name": "x", "config": {}}]}, emit
        )
        assert "gen1" in backend._active

        result = await backend.cancel_generation("gen1")

        assert result is True
        generation_manager.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_generation_returns_false_on_manager_error(
        self, generation_manager, emit
    ):
        generation_manager.cancel.side_effect = RuntimeError("cancel failed")
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager)

        import time
        generation_manager.generate.side_effect = lambda *a, **k: time.sleep(0.2)

        await backend.start_generation(
            {"generation_id": "gen1", "pipes": [{"name": "x", "config": {}}]}, emit
        )

        result = await backend.cancel_generation("gen1")

        assert result is False


class TestStartGenerationValidation:
    @pytest.mark.asyncio
    async def test_missing_pipes_raises_value_error(self, generation_manager, emit):
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager)

        with pytest.raises(ValueError, match="No pipeline configuration"):
            await backend.start_generation({"generation_id": "gen1"}, emit)

    @pytest.mark.asyncio
    async def test_no_generation_manager_raises_runtime_error(self, emit):
        backend = ConcreteInProcessBackend(make_backend_config(), generation_manager=None)

        with pytest.raises(RuntimeError, match="GenerationManager not set"):
            await backend.start_generation(
                {"generation_id": "gen1", "pipes": [{"name": "x", "config": {}}]}, emit
            )
