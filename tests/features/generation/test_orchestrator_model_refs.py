"""Model-ref resolution at generation start.

The backend is selected before the row is created, so by the time form data is rewritten
we know exactly which engine instance will run it. See docs/models.md.

Availability-based backend narrowing itself now lives in
`src.features.generation.routing.rules.ModelAvailability` (see
`tests/features/generation/routing/test_model_availability_rule.py`) rather
than a private orchestrator method - this file keeps only
`BackendRegistry.select_backend_for_generation`'s own empty-candidate-set
behavior, which the routing rule delegates to for its final pick (see
docs/generation-routing.md).
"""

import pytest
from unittest.mock import Mock


class TestSelectionRejectsEmptyCandidateSet:
    def test_registry_raises_when_narrowed_to_nothing(self):
        from src.features.backends.backend_registry import BackendRegistry, NoBackendForEngineError

        registry = object.__new__(BackendRegistry)
        registry.get_backends_for_engine = Mock(return_value=[Mock(backend_id="comfy_a")])

        with pytest.raises(NoBackendForEngineError, match="every selected model"):
            registry.select_backend_for_generation("comfyui", allowed_backend_ids=[])

    def test_narrowing_keeps_only_allowed_backends(self):
        from src.features.backends.backend_registry import BackendRegistry

        a, b = Mock(backend_id="comfy_a"), Mock(backend_id="comfy_b")
        registry = object.__new__(BackendRegistry)
        registry.get_backends_for_engine = Mock(return_value=[a, b])
        registry.backend_config_store = Mock()
        registry.backend_config_store.get_default_backend.return_value = None

        selected = registry.select_backend_for_generation(
            "comfyui", allowed_backend_ids=["comfy_b"]
        )

        assert selected is b

    def test_none_means_do_not_narrow(self):
        from src.features.backends.backend_registry import BackendRegistry

        a = Mock(backend_id="comfy_a")
        registry = object.__new__(BackendRegistry)
        registry.get_backends_for_engine = Mock(return_value=[a])
        registry.backend_config_store = Mock()
        registry.backend_config_store.get_default_backend.return_value = None

        assert registry.select_backend_for_generation("comfyui", allowed_backend_ids=None) is a
