from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RUN = Path(__file__).resolve().parent / "run.py"
_HARNESS_DIR = _RUN.parents[1] / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

_spec = importlib.util.spec_from_file_location("e2e_ui_run", _RUN)
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


def _make(output: Path, dir_name: str, *files: str) -> None:
    test_dir = output / dir_name
    test_dir.mkdir(parents=True)
    for name in files:
        (test_dir / name).write_bytes(name.encode())


def test_copies_traces_and_pngs_under_the_journey(tmp_path):
    output = tmp_path / "out"
    artifacts = tmp_path / "artifacts"
    _make(output, "new-workspace-save-chromium", "trace.zip", "test-failed-1.png", "video.webm")
    _make(output, "new-workspace-save-chromium-retry1", "trace.zip")

    run.collect_failure_artifacts(["new-workspace"], output, artifacts)

    copied = sorted(p.name for p in (artifacts / "new-workspace").iterdir())
    assert copied == [
        "new-workspace-save-chromium-retry1-trace.zip",
        "new-workspace-save-chromium-test-failed-1.png",
        "new-workspace-save-chromium-trace.zip",
    ]


def test_longest_journey_name_wins_and_passing_tests_leave_nothing(tmp_path):
    output = tmp_path / "out"
    artifacts = tmp_path / "artifacts"
    _make(output, "session-tab-switch-x-chromium", "trace.zip")
    _make(output, "session-passing-chromium", "video.webm")

    run.collect_failure_artifacts(["session", "session-tab-switch"], output, artifacts)

    assert (artifacts / "session-tab-switch" / "session-tab-switch-x-chromium-trace.zip").is_file()
    assert not (artifacts / "session").exists()


def test_missing_output_dir_is_a_noop(tmp_path):
    run.collect_failure_artifacts(["x"], tmp_path / "absent", tmp_path / "artifacts")
    assert not (tmp_path / "artifacts").exists()
