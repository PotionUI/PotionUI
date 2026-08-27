"""What reaches the logs when a pipe fails the generation.

`GenerationExecutionError.detail` is the only place a backend's real cause lives
(ComfyUI node errors, a remote engine's stderr) - the Python traceback the
manager logs alongside it only shows where the failure was noticed, not what the
engine said. It was already forwarded to the frontend inside
ErrorGenerationOutput; these tests pin that an operator reading the server log
sees it too.
"""

from typing import Any, Dict
from unittest.mock import Mock, patch

import pytest

from src.features.generation.engine import GenerationEngine
from src.pipelines.contracts import IOType, PipeInput, PipeOutputSpec
from src.pipelines.outputs import ErrorGenerationOutput, GenerationExecutionError

DETAIL = "Node 12 (KSampler): CUDA error: an illegal memory access was encountered"
SUMMARY = "ComfyUI reported a failure"


class FailingPipe:
    name = "failing_pipe"

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
        raise GenerationExecutionError(SUMMARY, detail=DETAIL)


class PlainlyFailingPipe(FailingPipe):
    name = "plainly_failing_pipe"

    def process(self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled=None):
        raise RuntimeError("no detail attached")


@pytest.fixture
def mock_dependencies():
    return {
        'gpu': Mock(),
        'model_directories': Mock(),
        'pipe_catalog': Mock(),
        'settings': Mock(),
        'system_monitor': Mock(),
        'memory_advisor': Mock(),
        'llm_service': Mock(),
    }


PIPES = [{'name': 'failing_pipe', 'enabled': True, 'input': [], 'cache': [], 'config': {}}]


def _run(manager, pipe_class):
    """Run one failing pipe, returning (logged error lines, emitted outputs)."""
    manager.pipe_catalog.get_pipe.return_value = pipe_class

    outputs = []
    with patch('src.features.generation.engine.logger') as log:
        with pytest.raises(Exception):
            manager.generate(PIPES, lambda o: outputs.append(o), "gen_error_test")
        logged = [str(call.args[0]) for call in log.error.call_args_list]
    return logged, outputs


def test_the_attached_detail_is_logged(mock_dependencies):
    """The defect: operators saw the one-line summary and the local traceback,
    and never the backend's own error text."""
    manager = GenerationEngine(**mock_dependencies)

    logged, _ = _run(manager, FailingPipe)

    assert any(DETAIL in line for line in logged), logged


def test_the_summary_is_still_logged(mock_dependencies):
    manager = GenerationEngine(**mock_dependencies)

    logged, _ = _run(manager, FailingPipe)

    assert any(SUMMARY in line for line in logged), logged


def test_the_detail_still_reaches_the_frontend(mock_dependencies):
    """Logging it must not come at the cost of the notification body."""
    manager = GenerationEngine(**mock_dependencies)

    _, outputs = _run(manager, FailingPipe)

    errors = [o for o in outputs if isinstance(o, ErrorGenerationOutput)]
    assert len(errors) == 1
    assert errors[0].detail == DETAIL
    assert errors[0].error == SUMMARY


def test_an_exception_without_a_detail_logs_no_empty_detail_line(mock_dependencies):
    """A plain exception has nothing extra to say; the traceback still carries it."""
    manager = GenerationEngine(**mock_dependencies)
    pipes = [{'name': 'plainly_failing_pipe', 'enabled': True, 'input': [], 'cache': [], 'config': {}}]
    manager.pipe_catalog.get_pipe.return_value = PlainlyFailingPipe

    outputs = []
    with patch('src.features.generation.engine.logger') as log:
        with pytest.raises(RuntimeError):
            manager.generate(pipes, lambda o: outputs.append(o), "gen_error_test")
        logged = [str(call.args[0]) for call in log.error.call_args_list]

    assert not any("Generation error detail:" in line for line in logged), logged
    errors = [o for o in outputs if isinstance(o, ErrorGenerationOutput)]
    assert "Traceback" in errors[0].detail
