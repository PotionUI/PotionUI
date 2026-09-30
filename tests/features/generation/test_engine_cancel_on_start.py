from unittest.mock import Mock

from src.features.generation.engine import GenerationEngine
from src.pipelines.contracts import IOType, PipeInput, PipeOutput, PipeOutputSpec


class _CountingPipe:
    name = "counting_pipe"
    runs = 0

    def __init__(self, config):
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

    def process(self, pipe_input: PipeInput, generation_outputs, is_cancelled=None):
        _CountingPipe.runs += 1
        return PipeOutput(output={"output": "ok"})


def _engine():
    deps = {name: Mock() for name in (
        "gpu", "pipe_catalog", "settings", "system_monitor", "memory_advisor", "llm_service",
    )}
    deps["pipe_catalog"].get_pipe.return_value = _CountingPipe
    return GenerationEngine(**deps, plugin_registry=None)


def _pipes():
    return [{"name": "counting_pipe", "enabled": True, "input": [], "cache": [], "config": {}}]


def test_a_cancel_recorded_before_the_run_starts_is_honoured():
    engine = _engine()
    assert engine.cancel("gen-early") is False
    engine.cancel_on_start("gen-early")
    _CountingPipe.runs = 0

    engine.generate(_pipes(), lambda _o: None, "gen-early")

    assert _CountingPipe.runs == 0
    assert "gen-early" not in engine._cancel_on_start


def test_a_deferred_cancel_does_not_touch_a_different_run():
    engine = _engine()
    engine.cancel_on_start("gen-other")
    _CountingPipe.runs = 0

    engine.generate(_pipes(), lambda _o: None, "gen-mine")

    assert _CountingPipe.runs == 1


def test_a_discarded_deferred_cancel_is_forgotten():
    engine = _engine()
    engine.cancel_on_start("gen-1")
    engine.discard_cancel_on_start("gen-1")
    _CountingPipe.runs = 0

    engine.generate(_pipes(), lambda _o: None, "gen-1")

    assert _CountingPipe.runs == 1
