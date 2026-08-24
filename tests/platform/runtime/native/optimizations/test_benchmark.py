"""Tests for the attention benchmark. The attention module is monkeypatched
(fake backend list, fake dispatcher, fake torch.cuda) so no GPU is needed."""

from __future__ import annotations

import asyncio

import pytest
import torch

import src.platform.runtime.native.optimizations.benchmark as bench_mod
from src.platform.runtime.native import attention as attention_mod


class _FakeCudaEvents:
    """Records which backends were actually invoked, in call order."""

    def __init__(self):
        self.calls: list[str] = []


@pytest.fixture(autouse=True)
def _cuda_available(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "synchronize", lambda: None)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)


_real_randn = torch.randn


def _fake_tensor(*shape, **kwargs):
    # CPU tensor stands in for a CUDA one - the benchmark never inspects
    # .is_cuda itself, only passes tensors through to the (also faked) dispatcher.
    return _real_randn(*shape)


@pytest.fixture(autouse=True)
def _fake_randn(monkeypatch):
    monkeypatch.setattr(torch, "randn", _fake_tensor)


def _configure(monkeypatch, *, available_backends, per_backend_delay=None, raising=None):
    """available_backends: list of backend names available() should report.
    per_backend_delay: dict[name, seconds] time.perf_counter is advanced by
    for each call to that backend (simulates relative speed).
    raising: set of backend names whose calls raise.
    """
    per_backend_delay = per_backend_delay or {}
    raising = raising or set()
    calls = _FakeCudaEvents()

    monkeypatch.setattr(attention_mod, "available_backends", lambda: list(available_backends))
    monkeypatch.setattr(attention_mod, "get_attention_backend", lambda: available_backends[0])

    clock = {"t": 0.0}

    def _fake_attention(q, k, v, *, backend=None, **kwargs):
        calls.calls.append(backend)
        if backend in raising:
            raise RuntimeError(f"{backend} kernel exploded")
        clock["t"] += per_backend_delay.get(backend, 0.001)
        return q

    monkeypatch.setattr(attention_mod, "attention", _fake_attention)
    monkeypatch.setattr(bench_mod.time, "perf_counter", lambda: clock["t"])
    return calls


class TestBenchmarkAttention:
    def test_raises_without_cuda(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        with pytest.raises(RuntimeError, match="CUDA"):
            bench_mod.benchmark_attention()

    def test_results_include_sdpa_and_available_backends(self, monkeypatch):
        _configure(monkeypatch, available_backends=["sage2", "sdpa"])
        result = bench_mod.benchmark_attention(shape=(1, 2, 4, 8), iterations=2)

        backends = [r["backend"] for r in result["results"]]
        assert "sdpa" in backends
        assert "sage2" in backends

    def test_sdpa_is_first_in_results(self, monkeypatch):
        """sdpa is the baseline row - always first, regardless of BACKEND_PRIORITY order."""
        _configure(monkeypatch, available_backends=["sage2", "sage", "flash", "sdpa"])
        result = bench_mod.benchmark_attention(shape=(1, 2, 4, 8), iterations=2)
        assert result["results"][0]["backend"] == "sdpa"

    def test_response_shape(self, monkeypatch):
        _configure(monkeypatch, available_backends=["sdpa"])
        result = bench_mod.benchmark_attention(shape=(1, 2, 4, 8), dtype=torch.bfloat16, iterations=5)

        assert result["dtype"] == "bfloat16"
        assert result["shape"] == [1, 2, 4, 8]
        assert result["iterations"] == 5
        assert result["active_backend"] == "sdpa"
        assert isinstance(result["results"], list)
        row = result["results"][0]
        assert set(row.keys()) == {"backend", "ms", "speedup", "ok", "error"}

    def test_sdpa_speedup_is_one(self, monkeypatch):
        _configure(monkeypatch, available_backends=["sdpa"])
        result = bench_mod.benchmark_attention(shape=(1, 2, 4, 8), iterations=3)
        sdpa_row = next(r for r in result["results"] if r["backend"] == "sdpa")
        assert sdpa_row["ok"] is True
        assert sdpa_row["speedup"] == 1.0

    def test_faster_backend_has_speedup_greater_than_one(self, monkeypatch):
        # sage2 "takes" less simulated time per call than sdpa -> higher speedup.
        _configure(
            monkeypatch, available_backends=["sage2", "sdpa"],
            per_backend_delay={"sdpa": 0.01, "sage2": 0.001},
        )
        result = bench_mod.benchmark_attention(shape=(1, 2, 4, 8), iterations=1)
        by_backend = {r["backend"]: r for r in result["results"]}
        assert by_backend["sage2"]["ok"] is True
        assert by_backend["sage2"]["speedup"] > 1.0

    def test_failing_backend_is_isolated_others_still_measured(self, monkeypatch):
        calls = _configure(monkeypatch, available_backends=["sage2", "flash", "sdpa"], raising={"sage2"})
        result = bench_mod.benchmark_attention(shape=(1, 2, 4, 8), iterations=2)

        by_backend = {r["backend"]: r for r in result["results"]}
        assert by_backend["sage2"]["ok"] is False
        assert by_backend["sage2"]["ms"] is None
        assert by_backend["sage2"]["speedup"] is None
        assert "exploded" in by_backend["sage2"]["error"]

        # sdpa and flash must still have been measured (not short-circuited).
        assert by_backend["flash"]["ok"] is True
        assert by_backend["sdpa"]["ok"] is True
        assert "flash" in calls.calls
        assert "sdpa" in calls.calls

    def test_warmup_calls_happen_before_timed_calls(self, monkeypatch):
        calls = _configure(monkeypatch, available_backends=["sdpa"])
        bench_mod.benchmark_attention(shape=(1, 2, 4, 8), iterations=4)
        # warmup (3) + timed (4) calls for the single backend.
        assert calls.calls.count("sdpa") == bench_mod._WARMUP_ITERATIONS + 4

    def test_tensors_freed_and_cache_emptied_even_on_error(self, monkeypatch):
        emptied = {"called": False}
        monkeypatch.setattr(torch.cuda, "empty_cache", lambda: emptied.__setitem__("called", True))
        _configure(monkeypatch, available_backends=["sdpa"], raising={"sdpa"})

        # Even though every backend fails, benchmark_attention itself must not
        # raise (each backend's failure is caught) and cleanup must still run.
        result = bench_mod.benchmark_attention(shape=(1, 2, 4, 8), iterations=1)
        assert result["results"][0]["ok"] is False
        assert emptied["called"] is True


class TestRunBenchmarkLocking:
    @pytest.mark.asyncio
    async def test_run_benchmark_returns_result(self, monkeypatch):
        _configure(monkeypatch, available_backends=["sdpa"])
        result = await bench_mod.run_benchmark(shape=(1, 2, 4, 8), iterations=1)
        assert result["results"][0]["backend"] == "sdpa"

    @pytest.mark.asyncio
    async def test_concurrent_call_raises_while_one_is_running(self, monkeypatch):
        _configure(monkeypatch, available_backends=["sdpa"])

        release = asyncio.Event()

        def _slow_benchmark(*args, **kwargs):
            # Block the worker thread until the test releases it, simulating
            # a long-running benchmark on the real GPU.
            import time as real_time

            while not release.is_set():
                real_time.sleep(0.01)
            return {"results": [], "dtype": "bfloat16", "shape": [], "iterations": 1, "active_backend": "sdpa"}

        monkeypatch.setattr(bench_mod, "benchmark_attention", _slow_benchmark)

        first = asyncio.create_task(bench_mod.run_benchmark())
        await asyncio.sleep(0.05)  # let the first call actually acquire the lock

        with pytest.raises(RuntimeError, match="already running"):
            await bench_mod.run_benchmark()

        release.set()
        await first

    @pytest.mark.asyncio
    async def test_cuda_unavailable_propagates_as_runtime_error(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        with pytest.raises(RuntimeError, match="CUDA"):
            await bench_mod.run_benchmark()


@pytest.mark.requires_gpu
class TestBenchmarkAttentionGpuSmoke:
    """Optional integration smoke test: exercises the real dispatcher on an
    actual GPU with a tiny shape. Skipped everywhere without CUDA."""

    def test_real_gpu_benchmark_runs(self):
        result = bench_mod.benchmark_attention(shape=(1, 4, 256, 64), iterations=2)
        assert result["results"]
        assert any(r["backend"] == "sdpa" and r["ok"] for r in result["results"])
