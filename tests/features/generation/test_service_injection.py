"""Tests for built-in SERVICE input injection into pipes.

A pipe declares `PipeInputSpec("<NAME>", IOType.SERVICE, ...)` and
`GenerationManager` puts the collaborator in the pipe's input dict under that
key. Unknown names are only logged, and pipeline validation skips SERVICE
inputs entirely, so nothing else in the system catches a service that fails to
bind - which is why the binding is asserted here.
"""

from unittest.mock import Mock

import pytest

from src.features.generation.generation import GenerationManager
from src.pipelines.contracts import IOType, PipeInputSpec


def _pipe_class(*service_names):
    class _Pipe:
        name = "fake_pipe"

        @classmethod
        def inputs(cls):
            return [
                PipeInputSpec(name, IOType.SERVICE, False, "", is_array=False)
                for name in service_names
            ]

    return _Pipe


@pytest.fixture
def collaborators():
    return {
        "gpu": Mock(name="gpu"),
        "system_monitor": Mock(name="system"),
        "memory_manager": Mock(name="memory"),
        "llm_service": Mock(name="llm"),
        "models": Mock(name="models"),
        "assets": Mock(name="assets"),
    }


@pytest.fixture
def manager(collaborators):
    return GenerationManager(
        gpu=collaborators["gpu"],
        model_manager=Mock(),
        pipe_catalog=Mock(),
        settings_manager=Mock(),
        system_monitor=collaborators["system_monitor"],
        memory_manager=collaborators["memory_manager"],
        llm_service=collaborators["llm_service"],
        models=collaborators["models"],
        assets=collaborators["assets"],
    )


class TestAssetsService:
    def test_assets_service_is_injected(self, manager, collaborators):
        """The seam that lets a pipe fetch weights without importing
        `src.features.downloads` - which the layering guard forbids."""
        result = manager._inject_built_in_services(_pipe_class("ASSETS"), {})

        assert result["ASSETS"] is collaborators["assets"]

    def test_assets_is_none_when_not_wired(self):
        """Absent rather than exploding: every consumer guards on None, and a
        pipe that never fetches must not need the service at all."""
        manager = GenerationManager(
            gpu=Mock(), model_manager=Mock(), pipe_catalog=Mock(),
            settings_manager=Mock(), system_monitor=Mock(),
            memory_manager=Mock(), llm_service=Mock(),
        )

        result = manager._inject_built_in_services(_pipe_class("ASSETS"), {})

        assert result["ASSETS"] is None

    def test_assets_injection_does_not_disturb_other_services(self, manager, collaborators):
        result = manager._inject_built_in_services(
            _pipe_class("ASSETS", "MODELS", "GPU"), {}
        )

        assert result["ASSETS"] is collaborators["assets"]
        assert result["MODELS"] is collaborators["models"]
        assert result["GPU"] is collaborators["gpu"]

    def test_unknown_service_stays_unbound(self, manager):
        result = manager._inject_built_in_services(_pipe_class("NOPE"), {})

        assert "NOPE" not in result


class TestConvertedPipesDeclareAssets:
    """Each pipe that had a download bypass must actually ask for the service;
    the declaration is the only thing that triggers injection."""

    def test_sdxl_generator_declares_assets(self):
        from src.pipelines.pipes.generator.sdxl.main import GeneratorSDXLPipe

        spec = next(
            spec for spec in GeneratorSDXLPipe.inputs() if spec.name == "ASSETS"
        )
        assert spec.io_type == IOType.SERVICE
        assert spec.required is False

    def test_controlnet_preprocessor_declares_assets(self):
        from src.pipelines.pipes.controlnet_preprocessor.main import (
            ControlNetPreprocessorPipe,
        )

        spec = next(
            spec
            for spec in ControlNetPreprocessorPipe.inputs()
            if spec.name == "ASSETS"
        )
        assert spec.io_type == IOType.SERVICE

    def test_maya_model_loader_declares_assets(self):
        from src.pipelines.pipes.model_loader.maya.main import ModelLoaderMayaPipe

        spec = next(
            spec for spec in ModelLoaderMayaPipe.inputs() if spec.name == "ASSETS"
        )
        assert spec.io_type == IOType.SERVICE
