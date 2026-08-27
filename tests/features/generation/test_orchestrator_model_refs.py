"""Availability narrowing and model-ref resolution at generation start.

The backend is selected before the row is created, so by the time form data is rewritten
we know exactly which engine instance will run it. See docs/models.md.
"""

import pytest
from unittest.mock import Mock, patch

from src.features.generation import orchestrator as orch
from src.features.models.form_refs import make_model_ref


class TestNarrowBackendsByAvailability:
    """`_narrow_backends_by_availability` decides whether to constrain selection."""

    def _orchestrator(self, engine_backend_ids):
        instance = object.__new__(orch.GenerationOrchestrator)
        registry = Mock()
        registry.get_backends_for_engine.return_value = [
            Mock(backend_id=bid) for bid in engine_backend_ids
        ]
        instance.backend_registry = registry
        return instance

    def test_form_without_model_refs_does_not_narrow(self):
        """Legacy path values carry no identity, so nothing can be constrained."""
        instance = self._orchestrator(["comfy_a"])
        form = {"checkpoint": "models/checkpoints/a.safetensors"}

        assert instance._narrow_backends_by_availability("comfyui", form) is None

    @patch("src.features.models.availability_repository.model_availability_repo")
    def test_unindexed_engine_skips_narrowing_rather_than_failing_everything(self, repo):
        """A configured-but-unindexed backend holds models; it has never been asked.

        Enforcing availability against an empty index would fail every generation on
        that engine instead of degrading to the previous behaviour.
        """
        repo.any_indexed.return_value = False
        instance = self._orchestrator(["comfy_a"])
        form = {"checkpoint": make_model_ref("m1")}

        assert instance._narrow_backends_by_availability("comfyui", form) is None

    @patch("src.features.models.availability.candidate_backends")
    @patch("src.features.models.availability_repository.model_availability_repo")
    def test_indexed_engine_narrows_to_backends_holding_every_model(self, repo, candidates):
        repo.any_indexed.return_value = True
        candidates.return_value = ["comfy_b"]
        instance = self._orchestrator(["comfy_a", "comfy_b"])
        form = {"checkpoint": make_model_ref("m1"), "loras": [{"model": make_model_ref("m2")}]}

        allowed = instance._narrow_backends_by_availability("comfyui", form)

        assert allowed == ["comfy_b"]
        assert candidates.call_args[0][1] == ["m1", "m2"]

    @patch("src.features.models.availability.candidate_backends")
    @patch("src.features.models.availability_repository.model_availability_repo")
    def test_no_backend_holds_everything_yields_empty_not_none(self, repo, candidates):
        """Empty means "narrow to nothing" and must reach selection as a failure;
        None would mean "do not narrow" and silently route anywhere."""
        repo.any_indexed.return_value = True
        candidates.return_value = []
        instance = self._orchestrator(["comfy_a"])

        allowed = instance._narrow_backends_by_availability(
            "comfyui", {"checkpoint": make_model_ref("m1")}
        )

        assert allowed == []
        assert allowed is not None


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
