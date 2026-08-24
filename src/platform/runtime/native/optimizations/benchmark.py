"""Attention-backend micro-benchmark for the Optimizations panel.

Answers "did that install actually help?" by timing the real dispatcher
(:func:`attention.attention`) against every available backend with the same
q/k/v tensors, so a user can compare sdpa vs sage2 etc. on their own GPU
without leaving the admin UI. One benchmark at a time (an ``asyncio.Lock``,
mirroring ``OptimizationInstaller``): the CUDA work runs in a worker thread via
``asyncio.to_thread`` so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import torch

from src.platform.runtime.native import attention

_DEFAULT_SHAPE = (1, 48, 8192, 128)
_DEFAULT_ITERATIONS = 20
_WARMUP_ITERATIONS = 3
_BENCHMARK_TIMEOUT_SECONDS = 120

_lock = asyncio.Lock()


def benchmark_attention(
    shape: tuple[int, int, int, int] = _DEFAULT_SHAPE,
    dtype: torch.dtype = torch.bfloat16,
    iterations: int = _DEFAULT_ITERATIONS,
) -> dict:
    """Time every available attention backend on identical q/k/v tensors.

    Runs synchronously (CUDA work) - call via :func:`run_benchmark` from async
    code so it doesn't block the event loop. Raises ``RuntimeError`` if no
    CUDA device is available.
    """
    if not torch.cuda.is_available():
        raise RuntimeError("Attention benchmark requires a CUDA device")

    q = k = v = None
    try:
        q = torch.randn(*shape, dtype=dtype, device="cuda")
        k = torch.randn(*shape, dtype=dtype, device="cuda")
        v = torch.randn(*shape, dtype=dtype, device="cuda")

        # sdpa is the baseline every speedup is measured against; benchmark it
        # first regardless of BACKEND_PRIORITY order, then the rest in
        # priority order (skipping sdpa, already done).
        backends = [attention.SDPA] + [
            b for b in attention.BACKEND_PRIORITY if b != attention.SDPA and b in attention.available_backends()
        ]

        raw: dict[str, Optional[float]] = {}
        errors: dict[str, str] = {}

        for name in backends:
            try:
                for _ in range(_WARMUP_ITERATIONS):
                    attention.attention(q, k, v, backend=name)
                torch.cuda.synchronize()

                start = time.perf_counter()
                for _ in range(iterations):
                    attention.attention(q, k, v, backend=name)
                torch.cuda.synchronize()
                elapsed = time.perf_counter() - start

                raw[name] = (elapsed / iterations) * 1000.0
            except Exception as e:  # noqa: BLE001 — one bad kernel must not sink the whole benchmark
                raw[name] = None
                errors[name] = str(e)

        sdpa_ms = raw.get(attention.SDPA)
        results = []
        for name in backends:
            ms = raw.get(name)
            ok = ms is not None
            speedup = None
            if ok and sdpa_ms:
                speedup = 1.0 if name == attention.SDPA else sdpa_ms / ms
            results.append({
                "backend": name,
                "ms": ms,
                "speedup": speedup,
                "ok": ok,
                "error": errors.get(name),
            })

        return {
            "dtype": str(dtype).rsplit(".", maxsplit=1)[-1],
            "shape": list(shape),
            "iterations": iterations,
            "active_backend": attention.get_attention_backend(),
            "results": results,
        }
    finally:
        del q, k, v
        torch.cuda.empty_cache()


async def run_benchmark(
    shape: tuple[int, int, int, int] = _DEFAULT_SHAPE,
    dtype: torch.dtype = torch.bfloat16,
    iterations: int = _DEFAULT_ITERATIONS,
) -> dict:
    """Async entry point: at most one benchmark at a time.

    Raises ``RuntimeError`` (-> 409 at the controller) if one is already
    running. The actual CUDA work happens in a worker thread via
    ``asyncio.to_thread`` - it must never run on the event loop thread.
    """
    if _lock.locked():
        raise RuntimeError("benchmark already running")

    async with _lock:
        return await asyncio.wait_for(
            asyncio.to_thread(benchmark_attention, shape, dtype, iterations),
            timeout=_BENCHMARK_TIMEOUT_SECONDS,
        )
