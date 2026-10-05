import gc
import json
import logging
import threading

import pytest

import src.platform.observability.profiling.profiler as profiler_module
from src.platform.observability.profiling.profiler import GenerationProfiler
from src.platform.settings import runtime_flags


@pytest.fixture(autouse=True)
def _profiling_on(monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "profiling_enabled", True)
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "profiling_census", True)
    monkeypatch.setattr(GenerationProfiler, "_SAMPLE_INTERVAL_S", 60.0)
    monkeypatch.setattr(GenerationProfiler, "_CENSUS_DELAY_S", 0.0)
    monkeypatch.setattr(profiler_module, "_read_rss_anon_file_split", lambda: None)


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _returns_while_census_is_blocked(call, slow):
    done = threading.Event()
    worker = threading.Thread(target=lambda: (call(), done.set()), daemon=True)
    worker.start()
    returned = done.wait(10.0)
    blocked = not slow.release.is_set()
    worker.join(10.0)
    return returned and blocked


class _SlowCensus:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def __call__(self, device_kinds, *, budget_s, cancel=None, **kwargs):
        self.calls.append((device_kinds, cancel))
        self.started.set()
        while not self.release.wait(0.01):
            if cancel is not None and cancel.is_set():
                return None
        return [{"kind": "census_group", "device": "cpu", "owner": "fake", "nbytes_gb": 1.0}]


def test_stop_returns_before_the_census_finishes_and_the_rows_land_afterwards(tmp_path, monkeypatch):
    slow = _SlowCensus()
    monkeypatch.setattr(GenerationProfiler, "_collect_tensor_census", slow)
    prof = GenerationProfiler()
    prof.start("gen-a", tmp_path)

    assert _returns_while_census_is_blocked(lambda: prof.stop("gen-a"), slow)
    assert slow.started.wait(5.0)
    assert prof.wait_for_census(0.05) is False
    events = [r.get("event") for r in _rows(tmp_path / "profile.jsonl")]
    assert "generation.end" in events
    assert not [r for r in _rows(tmp_path / "profile.jsonl") if r["kind"] == "census_group"]

    slow.release.set()
    assert prof.wait_for_census(5.0) is True
    census = [r for r in _rows(tmp_path / "profile.jsonl") if r["kind"] == "census_group"]
    assert census == [{"kind": "census_group", "device": "cpu", "owner": "fake", "nbytes_gb": 1.0}]
    assert slow.calls[0][0] == ("cpu", "cuda")


def test_the_census_walks_the_heap_once_for_both_devices(tmp_path, monkeypatch):
    calls = []
    real_get_objects = gc.get_objects

    def counting_get_objects(*args, **kwargs):
        calls.append(threading.current_thread().name)
        return real_get_objects(*args, **kwargs)

    monkeypatch.setattr(gc, "get_objects", counting_get_objects)
    prof = GenerationProfiler()
    prof.start("gen-walk", tmp_path)
    prof.stop("gen-walk")
    assert prof.wait_for_census(30.0)

    assert len(calls) == 1
    assert calls[0].startswith("gen-profiler-census-")


def test_a_new_generation_cancels_the_pending_census_without_waiting(tmp_path, monkeypatch, caplog):
    slow = _SlowCensus()
    monkeypatch.setattr(GenerationProfiler, "_collect_tensor_census", slow)
    prof = GenerationProfiler()
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    prof.start("gen-a", dir_a)
    prof.stop("gen-a")
    assert slow.started.wait(5.0)

    with caplog.at_level(logging.INFO, logger=profiler_module.logger.name):
        assert _returns_while_census_is_blocked(lambda: prof.start("gen-b", dir_b), slow)
    assert prof.wait_for_census(5.0) is True
    rows_a = _rows(dir_a / "profile.jsonl")
    assert [r.get("event") for r in rows_a if r["kind"] == "event"][-1] == "census.skipped"
    assert not [r for r in rows_a if r["kind"] == "census_group"]
    assert any("skipping the memory census for gen-a" in r.getMessage() for r in caplog.records)

    prof.mark("b.event")
    rows_b = _rows(dir_b / "profile.jsonl")
    assert {r.get("event") for r in rows_b if r["kind"] == "event"} >= {"generation.start", "b.event"}
    assert not [r for r in rows_b if r.get("event") == "census.skipped" or r["kind"] == "census_group"]
    slow.release.set()
    prof.stop("gen-b")
    assert prof.wait_for_census(5.0)


def test_a_census_still_in_its_start_delay_is_skipped_by_the_next_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(GenerationProfiler, "_CENSUS_DELAY_S", 30.0)
    called = []
    monkeypatch.setattr(GenerationProfiler, "_collect_tensor_census", lambda *a, **k: called.append(1) or [])
    prof = GenerationProfiler()
    prof.start("gen-a", tmp_path / "a")
    prof.stop("gen-a")
    prof.start("gen-b", tmp_path / "b")

    assert prof.wait_for_census(5.0) is True
    assert called == []
    assert _rows(tmp_path / "a" / "profile.jsonl")[-1]["event"] == "census.skipped"
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "profiling_census", False)
    prof.stop("gen-b")


def test_census_off_closes_the_profile_at_stop_with_no_thread(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "profiling_census", False)
    monkeypatch.setattr(
        GenerationProfiler, "_collect_tensor_census",
        lambda *a, **k: pytest.fail("census must not run when it is turned off"),
    )
    prof = GenerationProfiler()
    prof.start("gen-off", tmp_path)
    prof.census_now("mid-run")
    prof.stop("gen-off")

    assert prof._census_thread is None
    assert prof._fh is None
    assert "generation.end" in [r.get("event") for r in _rows(tmp_path / "profile.jsonl")]


def test_turning_profiling_on_takes_effect_on_the_next_generation(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "profiling_enabled", False)
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "profiling_census", False)
    prof = GenerationProfiler()
    prof.start("gen-1", tmp_path / "one")
    prof.stop("gen-1")
    assert not (tmp_path / "one" / "profile.jsonl").exists()

    runtime_flags.apply_saved_setting("profiling_enabled", True)
    prof.start("gen-2", tmp_path / "two")
    prof.stop("gen-2")
    assert (tmp_path / "two" / "profile.jsonl").exists()

    runtime_flags.apply_saved_setting("profiling_enabled", False)
    prof.start("gen-3", tmp_path / "three")
    prof.stop("gen-3")
    assert not (tmp_path / "three" / "profile.jsonl").exists()


def test_turning_profiling_off_mid_run_still_closes_that_run(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "profiling_census", False)
    prof = GenerationProfiler()
    prof.start("gen-mid", tmp_path)
    runtime_flags.runtime_flag_values()["profiling_enabled"] = False

    prof.stop("gen-mid")

    assert prof._fh is None
    assert prof._thread is None
    assert "generation.end" in [r.get("event") for r in _rows(tmp_path / "profile.jsonl")]
