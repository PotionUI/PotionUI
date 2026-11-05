import gc
import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

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
    prof.release_census("gen-a")
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
    prof.release_census("gen-walk")
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
    prof.release_census("gen-a")
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
    prof.release_census("gen-b")
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
    last = _rows(tmp_path / "a" / "profile.jsonl")[-1]
    assert (last["event"], last["reason"]) == ("census.skipped", "next_generation_started")
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


def test_the_census_counts_arena_pinned_weights_as_pinned(tmp_path, monkeypatch):
    import torch
    import torch.nn as nn

    from src.platform.runtime.native.memory import pinned_arena
    from src.platform.runtime.native.memory.pinned_arena import PinnedArena

    monkeypatch.setattr(pinned_arena, "_host_register", lambda ptr, nbytes: None)
    monkeypatch.setattr(pinned_arena, "_host_unregister", lambda ptr: None)
    monkeypatch.setattr(pinned_arena, "_synchronize", lambda device: None)
    weight = nn.Parameter(torch.zeros(512, 1024), requires_grad=False)
    arena = PinnedArena(segment_bytes=1 << 30)
    try:
        arena.pin([weight])
        assert not weight.data.is_pinned()

        monkeypatch.setattr(gc, "get_objects", lambda *args, **kwargs: [weight])
        rows = GenerationProfiler()._collect_tensor_census(("cpu",), budget_s=30.0)
        pinned_gb = sum(r["nbytes_gb"] for r in rows if r["kind"] == "census_group" and r["is_pinned"])
        assert pinned_gb >= 0.002
    finally:
        arena.release()


@pytest.mark.skipif(not __import__("torch").cuda.is_available(), reason="needs a CUDA device")
def test_the_census_counts_a_streamer_pinned_module_as_pinned(monkeypatch):
    import torch
    import torch.nn as nn

    from src.platform.runtime.native.memory.partial import ModuleStreamer, plan_residency_split
    from src.platform.runtime.native.kernels import disable_weight_init

    module = nn.Sequential(*(disable_weight_init.Linear(128, 128) for _ in range(4)))
    for p in module.parameters():
        p.requires_grad_(False)
    streamer = ModuleStreamer(module)
    streamer.apply("cuda:0", plan_residency_split(module, resident_budget_gb=0.0), pin=True)
    try:
        monkeypatch.setattr(gc, "get_objects", lambda *args, **kwargs: list(module.parameters()))
        rows = GenerationProfiler()._collect_tensor_census(("cpu",), budget_s=30.0)
        pinned_gb = sum(r["nbytes_gb"] for r in rows if r["kind"] == "census_group" and r["is_pinned"])
        assert pinned_gb >= 0.0002
    finally:
        streamer.teardown()


def test_the_census_waits_for_the_completion_release(tmp_path, monkeypatch):
    slow = _SlowCensus()
    slow.release.set()
    monkeypatch.setattr(GenerationProfiler, "_collect_tensor_census", slow)
    prof = GenerationProfiler()
    prof.start("gen-a", tmp_path)
    prof.stop("gen-a")

    assert not slow.started.wait(0.3)
    assert prof.wait_for_census(0.05) is False

    prof.release_census("gen-a")
    assert prof.wait_for_census(5.0) is True
    rows = _rows(tmp_path / "profile.jsonl")
    start = [r for r in rows if r.get("event") == "census.start"]
    assert len(start) == 1 and start[0]["trigger"] == "released"
    assert [r["owner"] for r in rows if r["kind"] == "census_group"] == ["fake"]


def test_a_release_that_lands_before_stop_is_kept(tmp_path, monkeypatch):
    slow = _SlowCensus()
    slow.release.set()
    monkeypatch.setattr(GenerationProfiler, "_collect_tensor_census", slow)
    prof = GenerationProfiler()
    prof.start("gen-a", tmp_path)
    prof.release_census("gen-a")
    prof.stop("gen-a")

    assert prof.wait_for_census(5.0) is True
    assert slow.started.is_set()


def test_a_busy_queue_skips_the_census_with_its_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(
        GenerationProfiler, "_collect_tensor_census",
        lambda *a, **k: pytest.fail("a skipped census must not walk the heap"),
    )
    prof = GenerationProfiler()
    prof.start("gen-a", tmp_path)
    prof.stop("gen-a")
    prof.skip_census("gen-a", "queue_busy")

    assert prof.wait_for_census(5.0) is True
    last = _rows(tmp_path / "profile.jsonl")[-1]
    assert (last["event"], last["reason"]) == ("census.skipped", "queue_busy")


def test_a_census_nobody_releases_runs_after_the_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(GenerationProfiler, "_CENSUS_RELEASE_TIMEOUT_S", 0.05)
    slow = _SlowCensus()
    slow.release.set()
    monkeypatch.setattr(GenerationProfiler, "_collect_tensor_census", slow)
    prof = GenerationProfiler()
    prof.start("gen-a", tmp_path)
    prof.stop("gen-a")

    assert prof.wait_for_census(5.0) is True
    start = [r for r in _rows(tmp_path / "profile.jsonl") if r.get("event") == "census.start"]
    assert start[0]["trigger"] == "release_timeout"


_OWNER_PROBE = """
import gc
import json
import torch
from src.platform.observability.profiling.profiler import GenerationProfiler

class _Holder:
    def __init__(self, weight):
        self.weight = weight

def no_referrers(*args, **kwargs):
    raise AssertionError("the census must not call gc.get_referrers")

holders = [_Holder(torch.zeros(4, 4, device="cpu") + i) for i in range(8)]
gc.get_referrers = no_referrers
rows = GenerationProfiler()._collect_tensor_census(("cpu",), budget_s=60.0)
print(json.dumps(sorted({r["owner"] for r in rows if r["kind"] == "census_group"})))
"""


def test_the_census_names_owners_without_a_heap_scan_per_tensor():
    repo_root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [sys.executable, "-c", _OWNER_PROBE], cwd=repo_root, capture_output=True, text=True, timeout=240,
    )

    assert result.returncode == 0, result.stderr
    assert "_Holder.weight" in json.loads(result.stdout.strip().splitlines()[-1])


def test_the_census_yields_the_gil_while_it_walks_the_heap(monkeypatch):
    ballast = [{"k": [i]} for i in range(200_000)]
    yields = []
    real_sleep = time.sleep

    def counting_sleep(seconds):
        if seconds == 0:
            yields.append(threading.get_ident())
        real_sleep(seconds)

    monkeypatch.setattr(time, "sleep", counting_sleep)
    GenerationProfiler()._collect_tensor_census(("cpu",), budget_s=600.0)

    assert len(yields) >= len(ballast) // GenerationProfiler._CENSUS_YIELD_EVERY
    del ballast


_ASYNCIO_PROBE = """
import _asyncio
import asyncio
import gc
import time
from src.platform.observability.profiling.profiler import GenerationProfiler


async def hop():
    await asyncio.sleep(0)


async def main():
    await asyncio.gather(hop(), hop())


asyncio.run(main())
index, truncated = GenerationProfiler()._scan_referrers(
    [_asyncio, _asyncio.__dict__], {id(_asyncio.__dict__): _asyncio.__dict__}, time.monotonic() + 60, None,
)
gc.collect()
print("collected", len(index[id(_asyncio.__dict__)]))
"""


def test_the_referrer_scan_leaves_the_asyncio_extension_module_alone():
    repo_root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [sys.executable, "-c", _ASYNCIO_PROBE], cwd=repo_root, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("collected 1")
