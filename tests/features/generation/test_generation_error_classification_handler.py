"""A raised torch.cuda.OutOfMemoryError (or refused host-RAM streaming) must
reach the frontend as an actionable generation_error, not a raw stack trace -
`generation_error` stays backward compatible: same `error`/`detail` fields,
enriched rather than replaced.
"""

from typing import Any, Dict
from unittest.mock import Mock, patch

import pytest
import torch

from src.features.generation.engine import GenerationEngine
from src.pipelines.contracts import IOType, PipeInput, PipeOutputSpec
from src.pipelines.outputs import ErrorGenerationOutput
from src.platform.runtime.native.errors import HostMemoryExhaustedError

CUDA_OOM_MESSAGE = "CUDA out of memory. Tried to allocate 2.00 GiB (GPU 0; 23.99 GiB total capacity)"
HOST_RAM_MESSAGE = "partial-residency streaming needs ~40.0GB pinned host RAM but only 8.0GB host RAM is free"


class CudaOomPipe:
    name = "cuda_oom_pipe"

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @staticmethod
    def get_default_config():
        return {}

    @staticmethod
    def inputs():
        return []

    @staticmethod
    def outputs():
        return [PipeOutputSpec(name="output", io_type=IOType.TEXT, is_array=False)]

    @staticmethod
    def configuration():
        return []

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
        raise torch.cuda.OutOfMemoryError(CUDA_OOM_MESSAGE)


class HostRamOomPipe(CudaOomPipe):
    name = "host_ram_oom_pipe"

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
        raise HostMemoryExhaustedError(HOST_RAM_MESSAGE)


@pytest.fixture
def mock_dependencies():
    gpu = Mock()
    gpu.get_free_vram.return_value = 512  # MB
    gpu.get_total_vram.return_value = 24576  # MB
    return {
        'gpu': gpu,
        'model_directories': Mock(),
        'pipe_catalog': Mock(),
        'settings': Mock(),
        'system_monitor': Mock(),
        'memory_advisor': Mock(),
        'llm_service': Mock(),
    }


def _run(manager, pipe_class, pipe_name):
    pipes = [{'name': pipe_name, 'enabled': True, 'input': [], 'cache': [], 'config': {}}]
    manager.pipe_catalog.get_pipe.return_value = pipe_class

    outputs = []
    with patch('src.features.generation.engine.logger'):
        with pytest.raises(Exception):
            manager.generate(pipes, lambda o: outputs.append(o), "gen_error_test")
    errors = [o for o in outputs if isinstance(o, ErrorGenerationOutput)]
    assert len(errors) == 1
    return errors[0]


def test_cuda_oom_produces_an_actionable_error_with_vram_numbers(mock_dependencies):
    manager = GenerationEngine(**mock_dependencies)

    error = _run(manager, CudaOomPipe, "cuda_oom_pipe")

    assert "GPU memory" in error.error or "VRAM" in error.error
    assert "0.5GB free of 24.0GB total VRAM" in error.error
    assert "resolution" in error.detail.lower()
    # the raw exception text is never discarded
    assert CUDA_OOM_MESSAGE in error.detail


def test_host_ram_oom_produces_an_actionable_error(mock_dependencies):
    manager = GenerationEngine(**mock_dependencies)

    error = _run(manager, HostRamOomPipe, "host_ram_oom_pipe")

    assert "host RAM" in error.error
    assert "smaller model variant" in error.detail.lower()
    assert HOST_RAM_MESSAGE in error.detail


def test_an_unrelated_exception_gets_the_neutral_fallback_headline(mock_dependencies):
    """A ValueError with no attached `.detail` would otherwise reach the
    frontend as raw exception text; it must get the neutral headline
    instead, with the original message preserved in the detail body."""
    class PlainFailure(CudaOomPipe):
        name = "plain_failure_pipe"

        def process(self, pipe_input, generation_outputs, is_cancelled=None):
            raise ValueError("preset form is missing a required field")

    manager = GenerationEngine(**mock_dependencies)

    error = _run(manager, PlainFailure, "plain_failure_pipe")

    assert error.error == "Something went wrong during generation."
    assert "preset form is missing a required field" in error.detail
