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

    assert error.error_code == "cuda_oom"
    assert "VRAM" in error.message
    assert "0.5GB free of 24.0GB total VRAM" in error.message
    assert any("resolution" in hint.lower() for hint in error.hints)
    assert CUDA_OOM_MESSAGE in error.error
    assert CUDA_OOM_MESSAGE not in error.message
    assert "Traceback" in error.detail


def test_host_ram_oom_produces_an_actionable_error(mock_dependencies):
    manager = GenerationEngine(**mock_dependencies)

    error = _run(manager, HostRamOomPipe, "host_ram_oom_pipe")

    assert error.error_code == "host_ram_oom"
    assert "host RAM" in error.message
    assert any("smaller model variant" in hint.lower() for hint in error.hints)
    assert HOST_RAM_MESSAGE in error.error
    assert HOST_RAM_MESSAGE not in error.message


def test_an_unrelated_exception_gets_the_neutral_fallback_headline(mock_dependencies):
    class PlainFailure(CudaOomPipe):
        name = "plain_failure_pipe"

        def process(self, pipe_input, generation_outputs, is_cancelled=None):
            raise ValueError("preset form is missing a required field")

    manager = GenerationEngine(**mock_dependencies)

    error = _run(manager, PlainFailure, "plain_failure_pipe")

    assert error.error_code == "unclassified"
    assert error.message == "Something went wrong while generating."
    assert error.error == "ValueError: preset form is missing a required field"
    assert "preset form is missing a required field" in error.detail


def test_the_error_names_the_failing_pipe_and_its_last_step(mock_dependencies):
    from src.pipelines.outputs import Progress, ProgressGenerationOutput

    class SteppingFailure(CudaOomPipe):
        name = "stepping_pipe"
        display_title = None

        def process(self, pipe_input, generation_outputs, is_cancelled=None):
            generation_outputs(ProgressGenerationOutput(state="Sampling", progress=Progress(current=3, max=8)))
            raise ValueError("/srv/models/private/unet.safetensors exploded")

    manager = GenerationEngine(**mock_dependencies)

    error = _run(manager, SteppingFailure, "stepping_pipe")

    assert error.pipe_id == 0
    assert error.pipe_name == "stepping_pipe"
    assert error.pipe_key == "stepping_pipe"
    assert error.failed_at_step == "Sampling 3/8"
    assert "/srv/" not in error.message
