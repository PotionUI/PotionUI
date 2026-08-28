import json
import logging
import threading
import time
import warnings
from types import SimpleNamespace

import pytest
import torch

import src.platform.observability.profiling.profiler as profiler_module
from src.platform.observability.profiling.profiler import (
    GenerationProfiler,
    read_process_rss_gb,
    reset_enabled_cache,
)


@pytest.fixture(autouse=True)
def _isolate_profiling_state(monkeypatch):
    """Every test gets a clean enabled/settings-manager cache and pinned-bytes
    counter, regardless of env vars set in the outer shell."""
    monkeypatch.delenv("POTIONUI_PROFILE", raising=False)
    profiler_module._settings = None
    reset_enabled_cache()
    profiler_module._pinned_bytes_cum = 0
    yield
    reset_enabled_cache()


def _enable(monkeypatch):
    monkeypatch.setenv("POTIONUI_PROFILE", "1")
    reset_enabled_cache()


def test_disabled_start_creates_no_file_and_no_thread(tmp_path):
    prof = GenerationProfiler()
    prof.start("gen-1", tmp_path)
    assert prof._thread is None
    assert prof._fh is None
    assert not (tmp_path / "profile.jsonl").exists()

    # mark()/stop() must also no-op cleanly
    prof.mark("some.event")
    prof.stop("gen-1")


def test_enabled_start_mark_stop_produces_valid_jsonl(tmp_path, monkeypatch):
    _enable(monkeypatch)
    prof = GenerationProfiler()
    prof.start("gen-2", tmp_path)
    assert prof._thread is not None and prof._thread.is_alive()

    prof.mark("pipe.start", pipe_id=0, pipe_name="checkpoint_loader")
    prof.mark("pipe.end", pipe_id=0, pipe_name="checkpoint_loader")
    prof.stop("gen-2")

    profile_path = tmp_path / "profile.jsonl"
    assert profile_path.exists()

    rows = [json.loads(line) for line in profile_path.read_text().splitlines() if line.strip()]
    assert len(rows) >= 3  # generation.start, pipe.start, pipe.end, generation.end (samples optional)

    events = [r for r in rows if r["kind"] == "event"]
    event_names = [r["event"] for r in events]
    assert event_names[0] == "generation.start"
    assert "pipe.start" in event_names
    assert "pipe.end" in event_names
    assert event_names[-1] == "generation.end"

    for row in rows:
        for key in ("t", "wall", "kind", "rss_gb", "avail_gb", "swap_gb", "cpu", "vram_alloc_gb", "vram_reserved_gb", "pinned_cum_gb"):
            assert key in row

    pipe_start = next(r for r in events if r["event"] == "pipe.start")
    assert pipe_start["pipe_id"] == 0
    assert pipe_start["pipe_name"] == "checkpoint_loader"


def test_snapshot_populates_vram_fields_when_cuda_available(monkeypatch):
    """Regression: profiler.py's module-level `import torch`
    was dropped to keep torch out of the boot path, but `_snapshot` still
    called `torch.cuda.is_available()` unguarded -- a `NameError` on every
    call, swallowed by the surrounding `except Exception`, silently leaving
    `vram_alloc_gb`/`vram_reserved_gb` as empty dicts on every row. Mocks the
    CUDA calls directly (this test environment has no GPU) so the snapshot's
    values are asserted, not just their keys' presence."""
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    monkeypatch.setattr(torch.cuda, "memory_allocated", lambda i=0: 2 * 1024 ** 3)
    monkeypatch.setattr(torch.cuda, "memory_reserved", lambda i=0: 3 * 1024 ** 3)

    prof = GenerationProfiler()
    row = prof._snapshot()

    assert row["vram_alloc_gb"] == {"0": 2.0}
    assert row["vram_reserved_gb"] == {"0": 3.0}


def test_mark_field_named_kind_does_not_clobber_row_discriminator(tmp_path, monkeypatch):
    """LTX RAM-ratchet follow-up: NativeModel.move_to/offload/unload
    (engine.py) call `mark("native.move_to", kind=self.kind, ...)` where
    `self.kind` is the component's OWN kind (e.g. "diffusion_model") -- a
    perfectly reasonable field name from the caller's side that used to
    silently overwrite this row's "kind": "event" discriminator, making the
    mark invisible to every `kind == "event"` filter (render_stage_table,
    the top-jumps last-event tracker) while still sitting in the file. The
    caller's value must survive under a renamed field instead of being lost
    OR corrupting the schema field."""
    _enable(monkeypatch)
    prof = GenerationProfiler()
    prof.start("gen-kind-collision", tmp_path)
    prof.mark("native.move_to", kind="diffusion_model", device="cuda")
    prof.stop("gen-kind-collision")

    rows = _read_rows(tmp_path)
    move_to_rows = [r for r in rows if r.get("event") == "native.move_to"]
    assert move_to_rows, "the native.move_to mark must still be findable by event name"
    row = move_to_rows[0]
    assert row["kind"] == "event", "row-type discriminator must never be clobbered by a caller field"
    assert row["component_kind"] == "diffusion_model", "the caller's value must survive, renamed"
    assert row["device"] == "cuda"


def test_sampler_thread_stops_on_stop(tmp_path, monkeypatch):
    _enable(monkeypatch)
    prof = GenerationProfiler()
    prof.start("gen-3", tmp_path)
    thread = prof._thread
    assert thread is not None
    prof.stop("gen-3")
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert prof._thread is None


def test_mark_never_raises_with_closed_writer(tmp_path, monkeypatch):
    _enable(monkeypatch)
    prof = GenerationProfiler()
    prof.start("gen-4", tmp_path)
    prof.stop("gen-4")

    # The writer is now closed / generation_id cleared; mark() must still be silent.
    prof.mark("late.mark", foo="bar")


def test_start_replaces_active_generation(tmp_path, monkeypatch):
    _enable(monkeypatch)
    prof = GenerationProfiler()
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    prof.start("gen-a", dir_a)
    prof.start("gen-b", dir_b)
    assert prof._generation_id == "gen-b"
    prof.stop("gen-b")

    assert (dir_a / "profile.jsonl").exists()
    assert (dir_b / "profile.jsonl").exists()


def test_profiling_enabled_env_var(monkeypatch):
    monkeypatch.setenv("POTIONUI_PROFILE", "true")
    reset_enabled_cache()
    assert profiler_module.profiling_enabled() is True


def test_read_process_rss_gb_matches_snapshot_rss(monkeypatch):
    """`read_process_rss_gb()` (the standalone helper a caller brackets a
    trim/reclaim operation with) must read the same live counter
    `_snapshot()` puts in every row's `rss_gb` -- not a second, divergent
    method of estimating RSS."""
    monkeypatch.setattr(
        profiler_module.psutil.Process, "memory_info",
        lambda self: SimpleNamespace(rss=2 * 1024 ** 3),
    )
    assert read_process_rss_gb() == pytest.approx(2.0)

    prof = GenerationProfiler()
    row = prof._snapshot()
    assert row["rss_gb"] == pytest.approx(2.0)


def test_read_process_rss_gb_fails_soft(monkeypatch):
    def _boom(self):
        raise OSError("no /proc")

    monkeypatch.setattr(profiler_module.psutil.Process, "memory_info", _boom)
    assert read_process_rss_gb() is None

    monkeypatch.setenv("POTIONUI_PROFILE", "0")
    reset_enabled_cache()
    assert profiler_module.profiling_enabled() is False


def test_profiling_enabled_settings_fallback(monkeypatch):
    monkeypatch.delenv("POTIONUI_PROFILE", raising=False)

    class FakeSettings:
        def get_setting(self, key, default=None, user_id=None):
            assert key == "profiling.enabled"
            return True

    profiler_module.configure_settings(FakeSettings())
    reset_enabled_cache()
    assert profiler_module.profiling_enabled() is True


def test_profiling_enabled_cached_after_first_call(monkeypatch):
    monkeypatch.setenv("POTIONUI_PROFILE", "1")
    reset_enabled_cache()
    assert profiler_module.profiling_enabled() is True

    # Flip the env var without resetting the cache -- decision must stick.
    monkeypatch.setenv("POTIONUI_PROFILE", "0")
    assert profiler_module.profiling_enabled() is True


def test_add_pinned_bytes_accumulates(monkeypatch):
    _enable(monkeypatch)
    profiler_module.add_pinned_bytes(1024 ** 3)
    profiler_module.add_pinned_bytes(2 * 1024 ** 3)
    assert profiler_module.pinned_cum_gb() == pytest.approx(3.0, abs=1e-6)


def test_add_pinned_bytes_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("POTIONUI_PROFILE", raising=False)
    reset_enabled_cache()
    profiler_module.add_pinned_bytes(1024 ** 3)
    assert profiler_module.pinned_cum_gb() == 0.0


# -- generation.log capture ------------------------------------------------


def test_log_handler_attached_captures_records(tmp_path, monkeypatch):
    _enable(monkeypatch)
    prof = GenerationProfiler()
    prof.start("gen-log-1", tmp_path)

    logging.getLogger("some.arbitrary.module").info("hello from a pipe")
    prof.stop("gen-log-1")

    log_path = tmp_path / profiler_module.LOG_FILENAME
    assert log_path.exists()
    content = log_path.read_text()
    assert "hello from a pipe" in content
    assert "some.arbitrary.module" in content
    assert "INFO" in content


def test_log_handler_detached_and_closed_on_stop(tmp_path, monkeypatch):
    _enable(monkeypatch)
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    prof = GenerationProfiler()
    prof.start("gen-log-2", tmp_path)
    assert len(root.handlers) == len(handlers_before) + 1

    prof.stop("gen-log-2")
    assert root.handlers == handlers_before

    # File must be complete/flushed and readable after stop().
    log_path = tmp_path / profiler_module.LOG_FILENAME
    assert log_path.exists()


def test_log_handler_no_leak_when_start_replaces_active(tmp_path, monkeypatch):
    _enable(monkeypatch)
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    prof = GenerationProfiler()
    prof.start("gen-log-a", tmp_path / "a")
    prof.start("gen-log-b", tmp_path / "b")  # replaces "a" -- must detach its handler first
    assert len(root.handlers) == len(handlers_before) + 1

    prof.stop("gen-log-b")
    assert root.handlers == handlers_before


def test_disabled_profiling_writes_no_log_file(tmp_path):
    prof = GenerationProfiler()
    prof.start("gen-log-off", tmp_path)
    prof.stop("gen-log-off")
    assert not (tmp_path / profiler_module.LOG_FILENAME).exists()


# -- CPU tensor census ------------------------------------------------------


class _TensorHolder:
    """Stand-in for a cached model bundle holding a big CPU tensor by attribute,
    so :meth:`GenerationProfiler._describe_owner` has something nameable to find."""

    def __init__(self, tensor):
        self.big_weight = tensor


def _read_rows(tmp_path):
    profile_path = tmp_path / "profile.jsonl"
    return [json.loads(line) for line in profile_path.read_text().splitlines() if line.strip()]


@pytest.mark.gc_sensitive
def test_census_row_for_big_cpu_tensor_with_owner(tmp_path, monkeypatch):
    _enable(monkeypatch)
    # > 64MB: 20M float32 elements = ~76.3MB.
    big = torch.zeros(20_000_000, dtype=torch.float32)
    holder = _TensorHolder(big)

    prof = GenerationProfiler()
    prof.start("gen-census-1", tmp_path)
    prof.stop("gen-census-1")

    rows = _read_rows(tmp_path)
    census_rows = [r for r in rows if r["kind"] == "census"]
    assert census_rows, "expected at least one census row for the big CPU tensor"

    big_row = next(
        r for r in census_rows
        if r["nbytes_gb"] * 1024 >= 64 and r["dtype"] == "torch.float32"
    )
    assert big_row["is_pinned"] is False
    assert "_TensorHolder.big_weight" in big_row["owner"]

    # keep references alive until after the assertions run
    del holder, big


def test_no_census_rows_when_profiling_disabled(tmp_path):
    big = torch.zeros(20_000_000, dtype=torch.float32)  # noqa: F841 -- kept alive, unused otherwise
    prof = GenerationProfiler()
    prof.start("gen-census-off", tmp_path)  # no-ops: profiling disabled
    prof.stop("gen-census-off")
    assert not (tmp_path / "profile.jsonl").exists()


def test_census_no_rows_for_small_cpu_tensor(tmp_path, monkeypatch):
    _enable(monkeypatch)
    small = torch.zeros(1024, dtype=torch.float32)  # noqa: F841 -- well under 64MB

    prof = GenerationProfiler()
    prof.start("gen-census-small", tmp_path)
    prof.stop("gen-census-small")

    rows = _read_rows(tmp_path)
    assert not [r for r in rows if r["kind"] == "census"]


def test_census_survives_meta_and_exotic_tensors(tmp_path, monkeypatch):
    """A meta-device tensor (no real storage) alive during the gc walk must be
    skipped, not crash it -- and a real big CPU tensor must still be found."""
    _enable(monkeypatch)
    meta = torch.empty((10_000, 10_000), device="meta")
    big = torch.zeros(20_000_000, dtype=torch.float32)
    holder = _TensorHolder(big)

    prof = GenerationProfiler()
    prof.start("gen-census-meta", tmp_path)
    prof.stop("gen-census-meta")

    rows = _read_rows(tmp_path)
    census_rows = [r for r in rows if r["kind"] == "census"]
    assert census_rows
    # The meta tensor has no real storage and must never be reported.
    assert all(r.get("shape") != list(meta.shape) for r in census_rows)
    assert any("_TensorHolder.big_weight" in r["owner"] for r in census_rows)

    del holder, big, meta


def test_census_row_carries_a_device_field(tmp_path, monkeypatch):
    """Census rows now say which device they scanned
    (`_write_cpu_tensor_census` vs `_write_cuda_tensor_census` share one row
    shape), so a profile.jsonl reader (or a human grepping it) can tell a
    stuck CPU tensor from a stuck CUDA one without guessing from context."""
    _enable(monkeypatch)
    big = torch.zeros(20_000_000, dtype=torch.float32)
    holder = _TensorHolder(big)

    prof = GenerationProfiler()
    prof.start("gen-census-device-field", tmp_path)
    prof.stop("gen-census-device-field")

    rows = _read_rows(tmp_path)
    census_rows = [r for r in rows if r["kind"] == "census"]
    assert census_rows
    assert all(r["device"] == "cpu" for r in census_rows)

    del holder, big


def test_cuda_tensor_census_runs_as_a_safe_noop_without_cuda(tmp_path, monkeypatch):
    """`stop()` now also calls `_write_cuda_tensor_census()` on every
    generation end. This sandbox has no CUDA device, so it must produce zero
    rows and never raise -- the GPU-side mirror of the CPU census (an eviction
    retry ladder found ~28GB still allocated by PyTorch on
    the GPU from an orphaned previous DiT, invisible to the CPU-only census)
    is meant to be a pure addition, never a new failure mode when CUDA simply
    isn't present."""
    _enable(monkeypatch)
    big = torch.zeros(20_000_000, dtype=torch.float32)  # noqa: F841 -- CPU only, on purpose

    prof = GenerationProfiler()
    prof.start("gen-cuda-census-noop", tmp_path)
    prof.stop("gen-cuda-census-noop")  # must not raise even though _write_cuda_tensor_census runs

    rows = _read_rows(tmp_path)
    assert not [r for r in rows if r["kind"] == "census" and r.get("device") == "cuda"]


@pytest.mark.gc_sensitive
def test_write_tensor_census_dispatches_on_device_kind(tmp_path, monkeypatch):
    """Direct unit test of the shared walk: calling it with `device_kind="cuda"`
    must filter by that device type, not silently fall back to scanning CPU
    tensors -- a regression here would make the new CUDA census meaningless
    (it would just re-report the same CPU tensors under a different label).

    gc_sensitive: the closing `any(device == "cpu")` assert depends on the
    census walk finding this test's tensor among whatever else the session's
    heap holds -- under xdist the co-resident tests (and thus the live heap)
    vary per run, and a crowded census can push it out."""
    _enable(monkeypatch)
    big = torch.zeros(20_000_000, dtype=torch.float32)
    holder = _TensorHolder(big)

    prof = GenerationProfiler()
    prof.start("gen-census-dispatch", tmp_path)
    prof._write_tensor_census(device_kind="cuda")
    prof.stop("gen-census-dispatch")

    rows = _read_rows(tmp_path)
    # The CPU tensor must NEVER show up under a "cuda" scan.
    assert not [r for r in rows if r["kind"] == "census" and r.get("device") == "cuda"]
    assert any(r.get("device") == "cpu" for r in rows if r["kind"] == "census")

    del holder, big


def test_census_walk_suppresses_future_warnings(tmp_path, monkeypatch):
    """The isinstance() scan over every live object must not spam FutureWarning
    (e.g. from deprecated torch aliases like torch.distributed.reduce_op)."""
    _enable(monkeypatch)
    big = torch.zeros(20_000_000, dtype=torch.float32)  # noqa: F841

    prof = GenerationProfiler()
    prof.start("gen-census-warn", tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        prof.stop("gen-census-warn")
    future_warnings = [w for w in caught if issubclass(w.category, FutureWarning)]
    assert not future_warnings


# -- grouped (aggregate) tensor census ---------------------------------------
#
# Motivated by the LTX RAM-ratchet investigation: a maintainer profile of one
# warm LTX 2.3 generation showed the OLD per-tensor >64MB census reporting
# ~1GB of CUDA tensors while VRAM actually held ~24GB (a resident fp8 DiT's
# individual Linear weights sit at/under the 64MiB detail floor almost across
# the board), and ~4.7GB of CPU tensors against a ~59GB RSS. The grouped
# section below (`kind: "census_group"`) has no size floor, so it must catch
# what the detail rows miss.


def test_census_group_row_includes_small_tensor(tmp_path, monkeypatch):
    """A tensor well under the 64MB detail floor must still show up in the
    aggregate `census_group` section -- that's the whole point of the fix."""
    _enable(monkeypatch)
    small = torch.zeros(1024, dtype=torch.float32)
    holder = _TensorHolder(small)

    prof = GenerationProfiler()
    prof.start("gen-census-group-small", tmp_path)
    prof.stop("gen-census-group-small")

    rows = _read_rows(tmp_path)
    group_rows = [r for r in rows if r["kind"] == "census_group" and r["device"] == "cpu"]
    assert group_rows, "small CPU tensor must be visible in the grouped section"
    assert any("_TensorHolder.big_weight" in r["owner"] for r in group_rows)
    # still true: the small tensor must NOT appear in the detail section.
    assert not [r for r in rows if r["kind"] == "census"]

    del holder, small


@pytest.mark.gc_sensitive
def test_census_group_dedups_views_of_one_storage(tmp_path, monkeypatch):
    """Two Python Tensor objects that share one underlying storage (a base
    tensor and a view of it) must be counted ONCE in the grouped section's
    `count`/`nbytes_gb`, not twice -- otherwise the aggregate double-counts
    every alias a real model creates (`.data`, narrow/view slices, etc.)."""
    _enable(monkeypatch)
    base = torch.zeros(2_000_000, dtype=torch.float32)  # ~7.6MB, one storage
    view = base.view(-1)
    holder = _TensorHolder([base, view])

    prof = GenerationProfiler()
    prof.start("gen-census-group-dedup", tmp_path)
    prof.stop("gen-census-group-dedup")

    rows = _read_rows(tmp_path)
    group_rows = [
        r for r in rows
        if r["kind"] == "census_group" and r["device"] == "cpu" and r["dtype"] == "torch.float32"
    ]
    assert group_rows
    expected_gb = round(base.numel() * 4 / (1024**3), 4)
    matching = [r for r in group_rows if abs(r["nbytes_gb"] - expected_gb) < 1e-4]
    assert matching, f"expected a group sized to one storage, got {group_rows}"
    assert matching[0]["count"] == 1

    del holder, base, view


@pytest.mark.gc_sensitive
def test_census_group_separates_pinned_from_unpinned(tmp_path, monkeypatch):
    """A pinned and a non-pinned CPU tensor must land in DIFFERENT groups even
    if dtype/owner otherwise match -- pinned vs. glibc-heap memory are
    released through different allocators (see model_lifecycle.lifecycle's
    trim_host_allocator/empty_pinned_host_cache), so collapsing them would
    hide exactly the distinction a pinned-memory leak hunt needs."""
    _enable(monkeypatch)
    plain = torch.zeros(2_000_000, dtype=torch.float32)
    pinned = torch.zeros(2_000_000, dtype=torch.float32).pin_memory()
    holder = _TensorHolder([plain, pinned])

    prof = GenerationProfiler()
    prof.start("gen-census-group-pinned", tmp_path)
    prof.stop("gen-census-group-pinned")

    rows = _read_rows(tmp_path)
    group_rows = [
        r for r in rows
        if r["kind"] == "census_group" and r["device"] == "cpu" and r["dtype"] == "torch.float32"
    ]
    pinned_flags = {r["is_pinned"] for r in group_rows}
    assert True in pinned_flags and False in pinned_flags

    del holder, plain, pinned


def test_census_group_row_carries_device_field(tmp_path, monkeypatch):
    _enable(monkeypatch)
    small = torch.zeros(1024, dtype=torch.float32)
    holder = _TensorHolder(small)

    prof = GenerationProfiler()
    prof.start("gen-census-group-device", tmp_path)
    prof.stop("gen-census-group-device")

    rows = _read_rows(tmp_path)
    group_rows = [r for r in rows if r["kind"] == "census_group"]
    assert group_rows
    assert all(r["device"] == "cpu" for r in group_rows)

    del holder, small


def test_census_group_rows_bounded_by_max_group_rows(tmp_path, monkeypatch):
    """The aggregate section must never grow unbounded: distinct groups beyond
    `_CENSUS_MAX_GROUP_ROWS` are dropped (largest-first), not all emitted."""
    _enable(monkeypatch)
    prof = GenerationProfiler()
    monkeypatch.setattr(prof, "_CENSUS_MAX_GROUP_ROWS", 2)
    # A different dtype per tensor forces a different group KEY (owner text
    # alone is class+attr name, not per-instance -- same owner string would
    # collapse same-dtype tensors into one group, which is correct grouping
    # behavior but wrong for this test's "force >2 distinct groups" setup).
    dtypes = [torch.float32, torch.float64, torch.int32, torch.int64, torch.bool]
    holders = [_TensorHolder(torch.zeros(1024, dtype=dt)) for dt in dtypes]

    prof.start("gen-census-group-bounded", tmp_path)
    prof.stop("gen-census-group-bounded")

    rows = _read_rows(tmp_path)
    group_rows = [r for r in rows if r["kind"] == "census_group" and r["device"] == "cpu"]
    assert len(group_rows) <= 2

    del holders


# -- census_now: ad-hoc mid-generation census -------------------------------
#
# The "34GB conditioning zombie" follow-up: a killed generation never
# reaches stop() (SIGKILL skips it entirely), so the one census that could
# name what's still resident right after a suspicious eviction never got
# written. census_now(tag) lets a caller fire that same walk from a specific,
# coarse point mid-generation -- see profiler.py's docstring on the method.


def test_census_now_writes_tagged_rows_distinct_from_stop(tmp_path, monkeypatch):
    """census_now() must use its OWN row kind (`census_group_now`/
    `census_now`), never the `census_group`/`census` kinds stop() writes --
    mixing point-in-time snapshots into one section would double-count any
    tensor still live at both moments."""
    _enable(monkeypatch)
    small = torch.zeros(1024, dtype=torch.float32)
    holder = _TensorHolder(small)

    prof = GenerationProfiler()
    prof.start("gen-census-now-1", tmp_path)
    prof.census_now("mid-run-checkpoint")
    prof.stop("gen-census-now-1")

    rows = _read_rows(tmp_path)
    now_group_rows = [r for r in rows if r["kind"] == "census_group_now"]
    assert now_group_rows, "expected at least one census_group_now row"
    assert all(r["tag"] == "mid-run-checkpoint" for r in now_group_rows)
    assert all(r["device"] == "cpu" for r in now_group_rows)  # no CUDA in this sandbox

    # census_now's rows must never leak into stop()'s own end-of-run kinds.
    assert not [r for r in rows if r["kind"] == "census_group" and r.get("tag")]

    del holder, small


def test_census_now_noop_when_profiling_disabled(tmp_path):
    prof = GenerationProfiler()
    prof.start("gen-census-now-off", tmp_path)  # no-op: disabled
    prof.census_now("whatever")  # must not raise, must not create a file
    prof.stop("gen-census-now-off")
    assert not (tmp_path / "profile.jsonl").exists()


def test_census_now_noop_before_start(tmp_path, monkeypatch):
    """Calling census_now() with no active generation (writer not open) must
    be a safe no-op, not raise."""
    _enable(monkeypatch)
    prof = GenerationProfiler()
    prof.census_now("no-file-yet")  # never started -- self._fh is None
    assert not (tmp_path / "profile.jsonl").exists()


def test_census_now_can_be_called_multiple_times_with_different_tags(tmp_path, monkeypatch):
    """Several census_now() calls at different phase boundaries in the same
    generation must each keep their own tag, not get merged together."""
    _enable(monkeypatch)
    small = torch.zeros(1024, dtype=torch.float32)
    holder = _TensorHolder(small)

    prof = GenerationProfiler()
    prof.start("gen-census-now-multi", tmp_path)
    prof.census_now("first")
    prof.census_now("second")
    prof.stop("gen-census-now-multi")

    rows = _read_rows(tmp_path)
    now_group_rows = [r for r in rows if r["kind"] == "census_group_now"]
    tags = {r["tag"] for r in now_group_rows}
    assert tags == {"first", "second"}

    del holder, small


# -- anon/file RSS split (on EVENT marks only) -------------------------------
#
# "This investigation burned two rounds unable to tell mmap pages from live
# heap -- the run where RSS grew +66GB but avail only dropped 45GB would have
# been diagnosed in one glance." See profiler.py's module comment above
# `_parse_rss_anon_file_split` for why this reads `/proc/self/status`'s
# `RssAnon`/`RssFile`/`RssShmem` lines rather than `/proc/self/smaps_rollup`
# (which does not carry those field names at all).

_FAKE_STATUS_TEXT = """\
Name:\tpython
Umask:\t0022
State:\tR (running)
VmRSS:\t   70000000 kB
RssAnon:\t   65536000 kB
RssFile:\t    4400000 kB
RssShmem:\t      64000 kB
VmSwap:\t          0 kB
"""


def test_parse_rss_anon_file_split_reads_the_three_fields():
    result = profiler_module._parse_rss_anon_file_split(_FAKE_STATUS_TEXT)
    assert result == {
        "rss_anon_gb": round(65536000 / (1024 ** 2), 4),
        "rss_file_gb": round(4400000 / (1024 ** 2), 4),
        "rss_shmem_gb": round(64000 / (1024 ** 2), 4),
    }


def test_parse_rss_anon_file_split_ignores_unrelated_lines():
    """Every other line in a real /proc/self/status (Name/State/VmRSS/VmSwap/
    etc.) must be ignored, not misparsed as one of the three target fields."""
    text = "Name:\tpython\nVmRSS:\t 999999 kB\nRssAnon:\t 1024 kB\n"
    result = profiler_module._parse_rss_anon_file_split(text)
    assert result == {"rss_anon_gb": round(1024 / (1024 ** 2), 4)}


def test_parse_rss_anon_file_split_returns_none_for_unrelated_text():
    """A malformed or unrelated blob (no Rss* lines at all -- e.g. a
    non-Linux /proc/self/status shape) must degrade to None, not an empty
    dict or a crash."""
    assert profiler_module._parse_rss_anon_file_split("nothing relevant here\n") is None
    assert profiler_module._parse_rss_anon_file_split("") is None


def test_parse_rss_anon_file_split_skips_unparseable_value():
    text = "RssAnon:\tnot-a-number kB\nRssFile:\t2048 kB\n"
    result = profiler_module._parse_rss_anon_file_split(text)
    assert result == {"rss_file_gb": round(2048 / (1024 ** 2), 4)}


def test_read_rss_anon_file_split_returns_none_when_file_missing(monkeypatch):
    monkeypatch.setattr(profiler_module, "_PROC_STATUS_PATH", "/nonexistent/path/for/this/test")
    assert profiler_module._read_rss_anon_file_split() is None


def test_read_rss_anon_file_split_reads_a_real_file(tmp_path, monkeypatch):
    fake_status = tmp_path / "status"
    fake_status.write_text(_FAKE_STATUS_TEXT)
    monkeypatch.setattr(profiler_module, "_PROC_STATUS_PATH", str(fake_status))

    result = profiler_module._read_rss_anon_file_split()

    assert result["rss_anon_gb"] == round(65536000 / (1024 ** 2), 4)
    assert result["rss_file_gb"] == round(4400000 / (1024 ** 2), 4)


def test_mark_writes_anon_file_split_fields(tmp_path, monkeypatch):
    """End to end: a real mark() call on an event row must carry
    rss_anon_gb/rss_file_gb when the (stubbed, so this test needs no real
    /proc access) split is available."""
    _enable(monkeypatch)
    monkeypatch.setattr(
        profiler_module, "_read_rss_anon_file_split",
        lambda: {"rss_anon_gb": 12.5, "rss_file_gb": 3.25, "rss_shmem_gb": 0.1},
    )

    prof = GenerationProfiler()
    prof.start("gen-anon-split", tmp_path)
    prof.mark("some.event")
    prof.stop("gen-anon-split")

    rows = _read_rows(tmp_path)
    event_row = next(r for r in rows if r.get("event") == "some.event")
    assert event_row["rss_anon_gb"] == 12.5
    assert event_row["rss_file_gb"] == 3.25
    assert event_row["rss_shmem_gb"] == 0.1


def test_mark_omits_anon_file_split_fields_when_unavailable(tmp_path, monkeypatch):
    """A non-Linux box / sandboxed process with no /proc/self/status must
    just omit the fields, never write nulls or raise."""
    _enable(monkeypatch)
    monkeypatch.setattr(profiler_module, "_read_rss_anon_file_split", lambda: None)

    prof = GenerationProfiler()
    prof.start("gen-anon-split-off", tmp_path)
    prof.mark("some.event")
    prof.stop("gen-anon-split-off")

    rows = _read_rows(tmp_path)
    event_row = next(r for r in rows if r.get("event") == "some.event")
    assert "rss_anon_gb" not in event_row
    assert "rss_file_gb" not in event_row


def test_sample_rows_never_carry_the_anon_file_split(tmp_path, monkeypatch):
    """The split must be EVENT-only -- the 250ms sample loop must never pay
    for it, even when it's available."""
    _enable(monkeypatch)
    monkeypatch.setattr(
        profiler_module, "_read_rss_anon_file_split",
        lambda: {"rss_anon_gb": 1.0, "rss_file_gb": 1.0},
    )

    prof = GenerationProfiler()
    prof.start("gen-anon-split-sample", tmp_path)
    time.sleep(prof._SAMPLE_INTERVAL_S * 2)
    prof.stop("gen-anon-split-sample")

    rows = _read_rows(tmp_path)
    sample_rows = [r for r in rows if r["kind"] == "sample"]
    assert sample_rows, "expected at least one sample row"
    assert all("rss_anon_gb" not in r for r in sample_rows)
