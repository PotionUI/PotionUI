import json
from unittest.mock import Mock

from src.features.generation.handlers import artifact_handlers
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.output_types import output_type_registry
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.pipelines.outputs import TextArtifactAction, TextGenerationOutput

ABC = "X:1\nT:Round trip\nM:3/4\nL:1/8\nK:G\n\"G\"G2 B2 d2 | \"D7\"A4 F2 |]\n"


def _output(**overrides):
    fields = dict(
        pipe_id=3,
        pipe_name="generator",
        title="ABC transcription · full",
        text=ABC,
        index=0,
        mono=True,
        action=TextArtifactAction(label="Use as ABC", field="abc", values={"cot": "full"}),
    )
    fields.update(overrides)
    return TextGenerationOutput(**fields)


def test_text_output_is_registered_as_a_pipe_artifact():
    spec = output_type_registry.spec_for(_output())
    assert spec.key == "text"
    assert spec.resolve_message_type(_output()) == "pipe_artifact"
    assert spec.handler_cls is artifact_handlers.TextGenerationOutputHandler


def test_serialized_message_survives_a_json_round_trip_with_the_exact_text():
    message = GenerationOutputSerializer(generation_id="gen-1").serialize_output(_output())
    decoded = json.loads(json.dumps(message))

    assert decoded["type"] == "pipe_artifact"
    assert decoded["artifact_type"] == "text"
    assert decoded["pipe_id"] == 3
    assert decoded["index"] == 0
    assert decoded["artifact_data"] == {
        "index": 0,
        "title": "ABC transcription · full",
        "text": ABC,
        "mono": True,
        "action": {"label": "Use as ABC", "field": "abc", "values": {"cot": "full"}},
    }


def test_output_without_an_action_serializes_a_null_action():
    message = GenerationOutputSerializer(generation_id="gen-1").serialize_output(
        _output(action=None, mono=False)
    )
    assert message["artifact_data"]["action"] is None
    assert message["artifact_data"]["mono"] is False


def test_run_report_persists_the_text_artifact_verbatim():
    recorder = RunReportRecorder(Mock(spec=GenerationRunReportRepository))
    message = GenerationOutputSerializer(generation_id="gen-1").serialize_output(_output())

    recorder.record_output("gen-1", message)
    report = recorder.flush("gen-1", terminal_status="completed")

    [entry] = report["artifacts"]
    assert entry["artifact_type"] == "text"
    assert entry["artifact_data"]["text"] == ABC
    assert entry["artifact_data"]["action"]["values"] == {"cot": "full"}
    assert "omitted" not in entry
