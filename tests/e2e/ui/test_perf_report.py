from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_UI = Path(__file__).resolve().parent
_HARNESS_DIR = _UI.parents[0] / "harness"
for path in (_UI, _HARNESS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_spec = importlib.util.spec_from_file_location("e2e_ui_run_perf", _UI / "run.py")
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)
perf_report = sys.modules["perf_report"]


def metric(median, low=None, high=None):
    low = median if low is None else low
    high = median if high is None else high
    return {"median": median, "min": low, "max": high, "runs": [low, median, high]}


def report(**scenarios):
    return {"meta": {"throttle": 1.0, "viewport": "1440x900"}, "scenarios": scenarios}


def ok(**metrics):
    return {"status": "ok", "runs": 3, "metrics": metrics}


def test_a_change_inside_five_percent_is_noise():
    assert perf_report.classify(metric(100, 98, 102), metric(104), "frame_p95_ms") == "noise"


def test_a_drop_beyond_the_noise_is_better_and_a_rise_is_worse():
    before = metric(100, 99, 101)
    assert perf_report.classify(before, metric(80), "script_ms") == "better"
    assert perf_report.classify(before, metric(130), "script_ms") == "worse"


def test_the_before_range_widens_the_noise_band():
    wide = metric(100, 70, 130)
    assert perf_report.classify(wide, metric(120), "script_ms") == "noise"
    assert perf_report.classify(wide, metric(140), "script_ms") == "worse"


def test_tiny_values_use_the_per_unit_floor():
    assert perf_report.classify(metric(0.2), metric(0.9), "frame_p95_ms") == "noise"
    assert perf_report.classify(metric(0.0), metric(0.4), "long_task_count") == "noise"
    assert perf_report.classify(metric(0.0), metric(3.0), "long_task_count") == "worse"


def test_percent_change_and_spread_handle_zero():
    assert perf_report.percent_change(0, 5) is None
    assert perf_report.percent_change(200, 150) == pytest.approx(-25.0)
    assert perf_report.spread_percent(metric(0, 0, 0)) is None
    assert perf_report.spread_percent(metric(100, 90, 110)) == pytest.approx(20.0)


def test_compare_prints_before_after_delta_and_flag():
    before = report(**{"slider-drag": ok(frame_p95_ms=metric(40, 39, 41))})
    after = report(**{"slider-drag": ok(frame_p95_ms=metric(20, 19, 21))})
    text = perf_report.compare(before, after)
    row = next(line for line in text.splitlines() if line.startswith("frame_p95_ms"))
    assert "40.00" in row and "20.00" in row and "-20.00" in row and "-50.0%" in row and row.rstrip().endswith("better")


def test_compare_names_a_skipped_scenario_instead_of_comparing_it():
    before = report(**{"video-scrub": {"status": "skipped", "reason": "no ffmpeg", "metrics": {}}})
    after = report(**{"video-scrub": ok(seek_p95_ms=metric(10))})
    assert "before=skipped after=ok" in perf_report.compare(before, after)


def test_compare_warns_when_throttle_or_viewport_differ():
    before = report(**{"a": ok(x_ms=metric(1))})
    after = report(**{"a": ok(x_ms=metric(1))})
    after["meta"]["throttle"] = 4.0
    assert "WARNING: throttle differs" in perf_report.compare(before, after)


def test_compare_lists_primary_metrics_first():
    before = report(**{"slider-drag": ok(aaa_ms=metric(1), frame_p95_ms=metric(2))})
    text = perf_report.compare(before, before)
    lines = [line.split()[0] for line in text.splitlines()[3:] if line.strip()]
    assert lines.index("frame_p95_ms") < lines.index("aaa_ms")


def test_merge_marks_a_scenario_without_a_result_file_as_failed(tmp_path):
    parts = tmp_path / "parts"
    parts.mkdir()
    (parts / "a.json").write_text(json.dumps(ok(x_ms=metric(1))))
    out = perf_report.merge_report(
        parts_dir=parts, perf_dir=tmp_path / "perf", expected=["a", "b"], meta={"run_id": "20260101T000000"}
    )
    merged = json.loads(out.read_text())
    assert out.name == "20260101T000000.json"
    assert merged["scenarios"]["a"]["status"] == "ok"
    assert merged["scenarios"]["b"]["status"] == "failed"


def test_latest_and_prev_resolve_by_filename_order(tmp_path):
    for name in ("20260101T000000", "20260102T000000", "20260103T000000"):
        (tmp_path / f"{name}.json").write_text("{}")
    assert perf_report.resolve_report("latest", tmp_path).stem == "20260103T000000"
    assert perf_report.resolve_report("prev", tmp_path).stem == "20260102T000000"
    with pytest.raises(FileNotFoundError):
        perf_report.resolve_report("missing.json", tmp_path)


def test_perf_filters_match_only_perf_files():
    pattern = re.compile(run.spec_filters(["slider-drag"], run.PERF_SUFFIX)[0])
    assert pattern.search("tests/perf/slider-drag.perf.ts")
    assert not pattern.search("tests/e2e/slider-drag.spec.ts")


def test_perf_discovery_globs_the_perf_dir_and_suffix(tmp_path, monkeypatch):
    (tmp_path / "alpha.perf.ts").write_text("")
    (tmp_path / "perfKit.ts").write_text("")
    (tmp_path / "beta.spec.ts").write_text("")
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    assert run.discover_specs(tmp_path, run.PERF_SUFFIX) == ["alpha"]


def test_perf_command_uses_the_perf_config():
    cmd = run.perf_playwright_cmd(["cold-generate"])
    assert cmd[cmd.index("--config") + 1] == "playwright.perf.config.ts"
    assert cmd[-1] == r"cold-generate\.perf\.ts"


def test_perf_env_exports_runs_throttle_and_parts_dir():
    args = argparse.Namespace(perf_runs=3, perf_throttle=4.0, perf_trace=True, perf_run_id="rid")
    env = run.perf_env(args)
    assert env["PERF_RUNS"] == "3" and env["PERF_CPU_THROTTLE"] == "4.0" and env["PERF_RUN_ID"] == "rid"
    assert env["PERF_TRACE"] == "1"
    assert env["PERF_OUT_DIR"].endswith(str(Path("perf") / ".parts"))


def test_compare_flag_needs_no_backend(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "ARTIFACTS_DIR", tmp_path)
    perf = tmp_path / "perf"
    perf.mkdir()
    data = report(**{"a": ok(x_ms=metric(5))})
    (perf / "20260101T000000.json").write_text(json.dumps(data))
    (perf / "20260102T000000.json").write_text(json.dumps(data))
    assert run.main(["--perf-compare", "prev", "latest"]) == run.EXIT_OK
    assert "[a]" in capsys.readouterr().out


def test_a_shard_writes_only_its_own_scenarios(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "ARTIFACTS_DIR", tmp_path)

    def fake_chunk(**kwargs):
        (run.perf_parts_dir() / "b.json").write_text(json.dumps(ok(x_ms=metric(1))))
        return 0

    monkeypatch.setattr(run, "run_chunk", fake_chunk)
    build = tmp_path / "build"
    (build / ".svelte-kit" / "output" / "client").mkdir(parents=True)
    args = argparse.Namespace(
        perf=True, skip_build=True, build_only=False, build_dir=build,
        perf_runs=1, perf_throttle=1.0, perf_run_id="20260101T000000", perf_trace=False,
    )
    run._run_locked(args, ["b"], [(2, ["b"])], [["a"], ["b"], ["c"]], 2, 3, 1)
    merged = json.loads((tmp_path / "perf" / "20260101T000000.json").read_text())
    assert list(merged["scenarios"]) == ["b"]
    assert merged["scenarios"]["b"]["status"] == "ok"


def loaded_report(load, cpus=8, **scenarios):
    data = report(**scenarios)
    data["meta"]["logical_cpus"] = cpus
    for scenario in data["scenarios"].values():
        scenario["load"] = {"start": load, "end": load}
    return data


def flag_of(text, name):
    return next(line for line in text.splitlines() if line.startswith(name)).split()[-1]


def test_a_busy_host_warns_and_marks_changes_unreliable():
    before = loaded_report(0.5, a=ok(frame_p95_ms=metric(40, 39, 41)))
    after = loaded_report(6.0, a=ok(frame_p95_ms=metric(80, 79, 81)))
    text = perf_report.compare(before, after)
    assert text.splitlines()[0].startswith("WARNING: the host was busy while the after report")
    assert "unreliable (load)" in text
    assert "worse" not in text and "better" not in text


def test_a_quiet_host_keeps_better_worse_flags_and_no_warning():
    before = loaded_report(0.5, a=ok(frame_p95_ms=metric(40, 39, 41)))
    after = loaded_report(1.0, a=ok(frame_p95_ms=metric(80, 79, 81)))
    text = perf_report.compare(before, after)
    assert "WARNING" not in text
    assert flag_of(text, "frame_p95_ms") == "worse"


def test_load_is_sampled_around_each_scenario_and_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(run, "sample_load", lambda: 1.25)

    def fake_chunk(**kwargs):
        (run.perf_parts_dir() / "b.json").write_text(json.dumps(ok(x_ms=metric(1))))
        return 0

    monkeypatch.setattr(run, "run_chunk", fake_chunk)
    build = tmp_path / "build"
    (build / ".svelte-kit" / "output" / "client").mkdir(parents=True)
    args = argparse.Namespace(
        perf=True, skip_build=True, build_only=False, build_dir=build,
        perf_runs=1, perf_throttle=1.0, perf_run_id="20260102T000000", perf_trace=False,
    )
    run._run_locked(args, ["b"], [(1, ["b"])], [["b"]], 1, 1, 1)
    merged = json.loads((tmp_path / "perf" / "20260102T000000.json").read_text())
    assert merged["scenarios"]["b"]["load"] == {"start": 1.25, "end": 1.25}
    assert merged["meta"]["logical_cpus"] >= 1
