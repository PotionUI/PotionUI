from unittest.mock import Mock

from src.features.generation.engine import GenerationEngine
from src.pipelines.contracts import BasePipe, IOType, PipeInputSpec


class NeedsCloud(BasePipe):
    name = "needs_cloud"
    description = "asks for the per-run cloud service"

    def process(self, pipe_input, generation_outputs):
        return None

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return [PipeInputSpec("CLOUD", IOType.SERVICE, True), PipeInputSpec("GPU", IOType.SERVICE, False)]

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []


def build_engine():
    return GenerationEngine(
        gpu=Mock(name="gpu"),
        pipe_catalog=Mock(),
        settings=Mock(),
        system_monitor=Mock(),
        memory_advisor=Mock(),
        llm_service=Mock(),
    )


def test_a_service_handed_to_the_run_is_injected_into_pipes_that_ask_for_it():
    engine = build_engine()
    cloud = object()

    injected = engine._inject_built_in_services(NeedsCloud, {}, {"CLOUD": cloud})

    assert injected["CLOUD"] is cloud
    assert injected["GPU"] is engine.gpu_monitor


def test_without_run_services_an_unknown_service_is_left_out():
    injected = build_engine()._inject_built_in_services(NeedsCloud, {})

    assert "CLOUD" not in injected


def test_run_services_are_per_call_and_leave_no_trace_on_the_engine():
    engine = build_engine()

    engine._inject_built_in_services(NeedsCloud, {}, {"CLOUD": object()})

    assert "CLOUD" not in engine._inject_built_in_services(NeedsCloud, {})
