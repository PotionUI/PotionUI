import asyncio
from types import SimpleNamespace

import pytest

from src.features.automation.nodes import actions
from src.features.automation.triggers import resource
from src.features.automation.triggers.resource import ResourceTrigger


class _Readings:
    def __init__(self, free_mb, total_mb=10000):
        self.free = list(free_mb) if isinstance(free_mb, list) else [free_mb]
        self.total_mb = total_mb

    def get_free_vram(self):
        return self.free.pop(0) if len(self.free) > 1 else self.free[0]

    def get_total_vram(self):
        return self.total_mb


def _run_trigger_polls(gpu, polls, monkeypatch, config=None):
    fired = []
    trigger = ResourceTrigger(
        automation_id="a", node_id="n",
        config=config or {"threshold_pct": 20, "direction": "below"},
        enqueue=lambda *event: fired.append(event), gpu_monitor=gpu,
    )
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= polls:
            trigger._stopped.set()

    monkeypatch.setattr(resource, "asyncio", SimpleNamespace(sleep=fake_sleep, CancelledError=asyncio.CancelledError))
    asyncio.run(trigger._loop())
    return fired, sleeps


@pytest.mark.parametrize("free_mb, total_mb", [(None, None), (1500, None), (None, 10000), (0, 0)])
def test_the_vram_trigger_skips_an_unknown_reading_without_firing_or_erroring(monkeypatch, free_mb, total_mb):
    errors = []
    monkeypatch.setattr(resource, "logger", SimpleNamespace(error=lambda *a, **k: errors.append(a)))

    fired, sleeps = _run_trigger_polls(_Readings(free_mb, total_mb), 3, monkeypatch)

    assert fired == []
    assert errors == []
    assert len(sleeps) == 3


def test_the_vram_trigger_fires_on_a_known_low_reading(monkeypatch):
    fired, _ = _run_trigger_polls(_Readings(1500, 10000), 3, monkeypatch)

    assert len(fired) == 1
    assert fired[0][2]["free_vram_pct"] == pytest.approx(15.0)


def test_the_vram_trigger_hold_restarts_after_an_unknown_reading(monkeypatch):
    clock = iter([0.0, 20.0])
    monkeypatch.setattr(resource, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    fired, _ = _run_trigger_polls(
        _Readings([1500, None, 1500]), 3, monkeypatch,
        config={"threshold_pct": 20, "direction": "below", "hold_s": 15.0},
    )

    assert fired == []


def _wait_for_gpu(gpu, threshold_mb, monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    ctx = SimpleNamespace(
        config={"threshold_mb": threshold_mb, "poll_interval_s": 1},
        services=SimpleNamespace(gpu_monitor=gpu),
        set_status=lambda status: None,
    )
    result = asyncio.run(actions._execute_wait_for_gpu(ctx))
    return result, sleeps


def test_the_wait_node_keeps_waiting_while_the_reading_is_unknown(monkeypatch):
    gpu = _Readings([None, None, 3000])

    result, sleeps = _wait_for_gpu(gpu, 2000, monkeypatch)

    assert len(sleeps) == 2
    assert result.output == {"free_vram_mb": 3000, "threshold_mb": 2000.0}


def test_the_wait_node_returns_at_once_when_enough_is_free(monkeypatch):
    result, sleeps = _wait_for_gpu(_Readings(3000), 2000, monkeypatch)

    assert sleeps == []
    assert result.output["free_vram_mb"] == 3000
