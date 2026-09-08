"""Tests for BackendRegistry.select_backend_for_generation - the full backend
selection algorithm described in docs/backends.md:

  1. candidates = enabled backends whose engine == requested engine
  2. else: the engine's default backend, if it is a candidate
  3. else: highest-priority candidate
  4. else: NoBackendForEngineError
"""

from unittest.mock import Mock

import pytest

from src.features.backends.backend_registry import BackendRegistry, NoBackendForEngineError


def make_backend(backend_id, engine, priority=1, available=True):
    backend = Mock()
    backend.backend_id = backend_id
    backend.name = backend_id
    backend.engine = engine
    backend.is_available = Mock(return_value=available)
    backend.config = Mock()
    backend.config.priority = priority
    return backend


def make_registry(backends, default_config=None):
    """Construct a BackendRegistry without running its heavy __init__ (which
    touches plugins/db/generation manager); wire only what selection needs."""
    registry = BackendRegistry.__new__(BackendRegistry)
    registry._backends_cache = {b.backend_id: b for b in backends}
    registry.backend_config_store = Mock()
    registry.backend_config_store.get_default_backend = Mock(return_value=default_config)
    return registry


class TestEngineMatching:
    def test_selects_only_candidate_for_engine(self):
        native = make_backend("native", "native")
        comfy = make_backend("comfyui-1", "comfyui")
        registry = make_registry([native, comfy])

        selected = registry.select_backend_for_generation(engine="comfyui")

        assert selected is comfy

    def test_disabled_backend_is_not_a_candidate(self):
        comfy = make_backend("comfyui-1", "comfyui", available=False)
        registry = make_registry([comfy])

        with pytest.raises(NoBackendForEngineError):
            registry.select_backend_for_generation(engine="comfyui")


class TestDefaultPreference:
    def test_per_engine_default_preferred_over_priority(self):
        low_priority_default = make_backend("comfyui-default", "comfyui", priority=1)
        high_priority_non_default = make_backend("comfyui-other", "comfyui", priority=10)
        registry = make_registry(
            [low_priority_default, high_priority_non_default],
            default_config=Mock(id="comfyui-default"),
        )

        selected = registry.select_backend_for_generation(engine="comfyui")

        assert selected is low_priority_default

    def test_default_for_other_engine_is_ignored(self):
        """A default_config belonging to a backend outside this engine's
        candidates must not be selected - falls through to priority."""
        comfy = make_backend("comfyui-1", "comfyui", priority=1)
        registry = make_registry(
            [comfy],
            default_config=Mock(id="native"),  # not a comfyui candidate
        )

        selected = registry.select_backend_for_generation(engine="comfyui")

        assert selected is comfy


class TestPriorityFallback:
    def test_highest_priority_used_when_no_default(self):
        low = make_backend("comfyui-low", "comfyui", priority=1)
        high = make_backend("comfyui-high", "comfyui", priority=10)
        registry = make_registry([low, high], default_config=None)

        selected = registry.select_backend_for_generation(engine="comfyui")

        assert selected is high


class TestNoBackendForEngine:
    def test_raises_when_engine_has_no_candidates_at_all(self):
        native = make_backend("native", "native")
        registry = make_registry([native])

        with pytest.raises(NoBackendForEngineError, match="comfyui"):
            registry.select_backend_for_generation(engine="comfyui")

    def test_error_message_lists_available_engines(self):
        native = make_backend("native", "native")
        registry = make_registry([native])

        with pytest.raises(NoBackendForEngineError, match="native"):
            registry.select_backend_for_generation(engine="comfyui")
