"""An in-process backend drives a real GenerationEngine through the
PipelineExecutor contract.

Every other backend test doubles the executor, so nothing else would notice if
the concrete manager stopped answering the calls the backend makes on it -
`generate` on the worker thread, `cancel` from the event loop. This wires the
same injection seam BackendRegistry does (a factory producing one executor per
backend) and runs a pipe end to end through it.
"""

import asyncio
from unittest.mock import Mock

import pytest

from src.features.backends.backend_config import NativeBackendConfig
from src.features.backends.native_backend import NativeBackend
from src.features.generation.engine import GenerationEngine
from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeInput,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import Icon, ProgressGenerationOutput


class EchoPipe(BasePipe):
    """A pipe with no inputs that emits one progress output and one result."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Emits a single output"

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec(name="text", io_type=IOType.TEXT)]

    @classmethod
    def configuration(cls):
        return []

    @classmethod
    def get_default_config(cls):
        return {}

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
        generation_outputs(
            ProgressGenerationOutput(icon=Icon("bolt", "beat"), state="echoing")
        )
        return PipeOutput(output={"text": "echoed"})


def make_executor() -> GenerationEngine:
    """What the composition root's factory closure produces, over doubles."""
    pipe_catalog = Mock()
    pipe_catalog.get_pipe.return_value = EchoPipe

    settings = Mock()
    settings.get_file_storage_directory.return_value = "storage"

    return GenerationEngine(
        gpu=Mock(),
        model_directories=Mock(),
        pipe_catalog=pipe_catalog,
        settings=settings,
        system_monitor=Mock(),
        memory_advisor=Mock(),
        llm_service=Mock(),
        models=Mock(),
    )


def make_backend() -> NativeBackend:
    backend = NativeBackend(
        backend_config=NativeBackendConfig(
            id="native-1", name="Native", enabled=True, priority=1
        )
    )
    backend.set_generation_engine(make_executor())
    return backend


PIPELINE = {
    "generation_id": "gen-executor-contract",
    "preset_id": "some-preset",
    "pipes": [{"name": "echo", "enabled": True, "input": [], "config": {}}],
}


@pytest.mark.asyncio
async def test_backend_runs_a_pipeline_on_the_concrete_manager():
    backend = make_backend()
    emitted = []

    generation_id = await backend.start_generation(PIPELINE, emitted.append)
    assert generation_id == "gen-executor-contract"

    # start_generation schedules the run; wait for the terminating None sentinel.
    for _ in range(200):
        await asyncio.sleep(0.01)
        if emitted and emitted[-1] is None:
            break

    assert emitted[-1] is None, "the run never finished"
    assert any(
        isinstance(o, ProgressGenerationOutput) and o.state == "echoing"
        for o in emitted
    ), f"the pipe's output never reached the emit callback: {emitted}"


@pytest.mark.asyncio
async def test_cancel_reaches_the_concrete_manager():
    backend = make_backend()

    # Nothing in flight: the backend answers without consulting the executor.
    assert await backend.cancel_generation("gen-executor-contract") is False

    # In flight: the executor is asked, and it owns this id.
    backend._active.add("gen-executor-contract")
    backend.generation_engine._running_generation_id = "gen-executor-contract"

    assert await backend.cancel_generation("gen-executor-contract") is True
    assert backend.generation_engine._cancelled is True


def test_native_backend_caps_vram_through_the_executors_gpu_monitor():
    """prepare_pipes reaches past the execution contract for the GPU manager."""
    backend = make_backend()
    backend.config.gpu_max_vram = 24

    backend.prepare_pipes([{"name": "echo", "config": {}}])

    backend.generation_engine.gpu_monitor.set_vram_cap_gb.assert_called_once_with(24)
