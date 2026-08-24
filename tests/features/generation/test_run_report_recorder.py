"""RunReportRecorder: in-memory accumulation, flush-on-terminal, and the
crashed-generation sweep. The repository collaborator is mocked - persistence
itself is covered by test_run_report_repository.py.
"""

from unittest.mock import Mock

import pytest

from src.features.generation.run_report_recorder import (
    RunReportRecorder,
    _ARTIFACTS_CAP,
    _STATUS_HISTORY_CAP,
)
from src.features.generation.run_report_repository import GenerationRunReportRepository


def _status_message(pipe_id, current_step, progress=0.0, message="working"):
    return {
        "type": "generation_status",
        "pipe_id": pipe_id,
        "pipe_name": f"pipe-{pipe_id}",
        "current_step": current_step,
        "message": message,
        "progress": progress,
    }


def _artifact_message(pipe_id, artifact_type, artifact_data):
    return {
        "type": "pipe_artifact",
        "pipe_id": pipe_id,
        "pipe_name": f"pipe-{pipe_id}",
        "artifact_type": artifact_type,
        "artifact_data": artifact_data,
    }


@pytest.fixture
def repository():
    return Mock(spec=GenerationRunReportRepository)


@pytest.fixture
def recorder(repository):
    return RunReportRecorder(repository)


class TestStatusHistoryBoundaries:
    def test_repeated_progress_on_the_same_step_collapses_to_one_entry(self, recorder):
        recorder.record_output("gen-1", _status_message(0, "sampling", progress=0.1))
        recorder.record_output("gen-1", _status_message(0, "sampling", progress=0.5))
        recorder.record_output("gen-1", _status_message(0, "sampling", progress=0.9))

        report = recorder.flush("gen-1", terminal_status="completed")

        # One boundary for "sampling" plus the terminal boundary appended at flush.
        assert len(report["status_history"]) == 2
        assert report["status_history"][0]["step"] == "sampling"
        assert report["status_history"][0]["progress"] == 0.1  # captured at the transition, not overwritten
        assert report["status_history"][1]["step"] == "completed"

    def test_a_step_or_pipe_change_opens_a_new_boundary(self, recorder):
        recorder.record_output("gen-1", _status_message(0, "loading"))
        recorder.record_output("gen-1", _status_message(0, "sampling"))
        recorder.record_output("gen-1", _status_message(1, "sampling"))  # same step, different pipe

        report = recorder.flush("gen-1", terminal_status="completed")

        steps = [(e["pipe_id"], e["step"]) for e in report["status_history"]]
        assert steps == [(0, "loading"), (0, "sampling"), (1, "sampling"), (None, "completed")]

    def test_status_history_caps_and_flags_truncation(self, recorder):
        for i in range(_STATUS_HISTORY_CAP + 5):
            recorder.record_output("gen-1", _status_message(0, f"step-{i}"))

        report = recorder.flush("gen-1", terminal_status="completed")

        assert len(report["status_history"]) == _STATUS_HISTORY_CAP
        assert report["status_history_truncated"] is True
        # Oldest dropped: step-0 must be gone, the most recent transitions survive.
        assert all(e["step"] != "step-0" for e in report["status_history"])
        assert report["status_history"][-2]["step"] == f"step-{_STATUS_HISTORY_CAP + 4}"


class TestPipeTimers:
    def test_first_and_subsequent_messages_set_started_and_ended(self, recorder):
        recorder.record_output("gen-1", _status_message(0, "loading"))
        recorder.record_output("gen-1", _status_message(0, "sampling"))

        report = recorder.flush("gen-1", terminal_status="completed")

        timer = report["pipe_timers"]["0"]
        assert timer["started_at"] is not None
        assert timer["ended_at"] is not None

    def test_separate_pipes_get_separate_timers(self, recorder):
        recorder.record_output("gen-1", _status_message(0, "loading"))
        recorder.record_output("gen-1", _status_message(1, "sampling"))

        report = recorder.flush("gen-1", terminal_status="completed")

        assert set(report["pipe_timers"].keys()) == {"0", "1"}


class TestArtifacts:
    def test_pipe_artifact_messages_are_captured_verbatim(self, recorder):
        recorder.record_output("gen-1", _artifact_message(0, "seed", {"seed": 42}))

        report = recorder.flush("gen-1", terminal_status="completed")

        assert len(report["artifacts"]) == 1
        artifact = report["artifacts"][0]
        assert artifact["pipe_id"] == 0
        assert artifact["artifact_type"] == "seed"
        assert artifact["artifact_data"] == {"seed": 42}

    def test_artifacts_cap_and_flag_truncation(self, recorder):
        for i in range(_ARTIFACTS_CAP + 3):
            recorder.record_output("gen-1", _artifact_message(0, "seed", {"seed": i}))

        report = recorder.flush("gen-1", terminal_status="completed")

        assert len(report["artifacts"]) == _ARTIFACTS_CAP
        assert report["artifacts_truncated"] is True
        assert all(a["artifact_data"]["seed"] != 0 for a in report["artifacts"])


class TestPluginOutputs:
    def test_unrecognized_message_type_is_captured_latest_wins(self, recorder):
        recorder.record_output("gen-1", {
            "type": "custom_plugin_update", "pipe_id": 0, "pipe_name": "my-plugin", "payload": "first",
        })
        recorder.record_output("gen-1", {
            "type": "custom_plugin_update", "pipe_id": 0, "pipe_name": "my-plugin", "payload": "second",
        })

        report = recorder.flush("gen-1", terminal_status="completed")

        assert set(report["plugin_outputs"].keys()) == {"custom_plugin_update"}
        entry = report["plugin_outputs"]["custom_plugin_update"]
        assert entry["plugin_id"] == "my-plugin"
        assert entry["message"]["payload"] == "second"

    @pytest.mark.parametrize("message_type", ["generation_status", "pipe_artifact", "workbench_update", "gallery_update"])
    def test_core_message_types_never_land_in_plugin_outputs(self, recorder, message_type):
        recorder.record_output("gen-1", {"type": message_type, "pipe_id": 0, "pipe_name": "core"})

        report = recorder.flush("gen-1", terminal_status="completed")

        assert report["plugin_outputs"] == {}


class TestFlush:
    def test_flush_persists_via_the_repository(self, recorder, repository):
        recorder.record_output("gen-1", _status_message(0, "sampling"))

        report = recorder.flush("gen-1", terminal_status="completed", terminal_message="done")

        repository.save.assert_called_once_with("gen-1", report)

    def test_flush_with_no_prior_output_still_produces_a_report(self, recorder, repository):
        report = recorder.flush("gen-1", terminal_status="failed", terminal_message="boom")

        assert report["status_history"] == [{
            "at": report["status_history"][0]["at"],
            "pipe_id": None,
            "step": "failed",
            "message": "boom",
            "progress": None,
        }]
        repository.save.assert_called_once()

    def test_flush_evicts_the_in_memory_accumulator(self, recorder):
        recorder.record_output("gen-1", _status_message(0, "sampling"))
        recorder.flush("gen-1", terminal_status="completed")

        assert "gen-1" not in recorder._accumulators

        # A second flush for the same id starts from a clean slate rather than
        # replaying the first run's history - proof the accumulator was popped,
        # not merely marked done.
        second_report = recorder.flush("gen-1", terminal_status="completed")
        assert len(second_report["status_history"]) == 1


class TestSweep:
    def test_sweep_evicts_stale_unflushed_accumulators_without_persisting(self, recorder, repository):
        recorder.record_output("gen-crashed", _status_message(0, "sampling"))
        recorder._accumulators["gen-crashed"].created_at -= 10_000  # simulate an old, abandoned run

        evicted = recorder.sweep(max_age_s=3600)

        assert evicted == 1
        assert "gen-crashed" not in recorder._accumulators
        repository.save.assert_not_called()

    def test_sweep_leaves_recent_accumulators_alone(self, recorder):
        recorder.record_output("gen-fresh", _status_message(0, "sampling"))

        evicted = recorder.sweep(max_age_s=3600)

        assert evicted == 0
        assert "gen-fresh" in recorder._accumulators

    def test_a_swept_generation_flushes_as_a_fresh_empty_report(self, recorder):
        recorder.record_output("gen-crashed", _status_message(0, "sampling"))
        recorder._accumulators["gen-crashed"].created_at -= 10_000
        recorder.sweep(max_age_s=3600)

        report = recorder.flush("gen-crashed", terminal_status="failed")

        assert len(report["status_history"]) == 1  # only the terminal boundary - nothing survived
        assert report["pipe_timers"] == {}
