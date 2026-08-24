"""StepHook dispatch tests: ordering, isolation, progress/preview behaviour."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.errors import SamplingNumericsError
from src.platform.runtime.native.sampling.hooks import (
    BaseStepHook,
    NumericsWatchdog,
    PreviewHook,
    ProgressHook,
    run_hooks,
    with_numerics_watchdog,
)


class RecordingHook(BaseStepHook):
    def __init__(self, name, log, priority=0):
        self.name = name
        self.log = log
        self.priority = priority

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
        self.log.append((self.name, step_index))


class RaisingHook(BaseStepHook):
    def __init__(self, log):
        self.log = log

    def on_step(self, *args):
        self.log.append(("raised",))
        raise RuntimeError("boom")


def test_priority_ordering():
    log = []
    hooks = [
        RecordingHook("low", log, priority=1),
        RecordingHook("high", log, priority=100),
        RecordingHook("mid", log, priority=50),
    ]
    run_hooks(hooks, "on_step", 0, 3, torch.zeros(1), 1.0, None)
    assert [name for name, _ in log] == ["high", "mid", "low"]


def test_exception_isolation():
    log = []
    hooks = [RaisingHook(log), RecordingHook("survivor", log)]
    # must not raise; survivor still runs
    run_hooks(hooks, "on_step", 2, 5, torch.zeros(1), 0.5, None)
    assert ("raised",) in log
    assert ("survivor", 2) in log


def test_progress_hook_reports_fraction():
    seen = []
    hook = ProgressHook(lambda frac, i, total: seen.append((frac, i, total)))
    hook.on_start(4)
    for i in range(4):
        hook.on_step(i, 4, torch.zeros(1), 0.0, None)
    assert seen[0] == (0.25, 0, 4)
    assert seen[-1] == (1.0, 3, 4)


def test_preview_hook_every_n_and_final():
    previews = []
    hook = PreviewHook(
        decode_fn=lambda x0: x0.sum().item(),
        every_n=2,
        callback=lambda preview, i: previews.append(i),
    )
    total = 5
    for i in range(total):
        hook.on_step(i, total, torch.zeros(1), 0.0, torch.full((1,), float(i)))
    # step 0 (first always previews so the workbench comes alive immediately),
    # steps 1,3 (every_n=2 on 1-based), plus final step 4.
    assert previews == [0, 1, 3, 4]


def test_preview_hook_skips_when_no_x0():
    previews = []
    hook = PreviewHook(lambda x0: 1, 1, lambda p, i: previews.append(i))
    hook.on_step(0, 1, torch.zeros(1), 0.0, None)
    assert previews == []


# -- NumericsWatchdog: x0-vs-x attribution, switch_step ----

def test_numerics_watchdog_reports_x0_when_x0_bad_but_x_finite():
    watchdog = NumericsWatchdog("unipc", interval=1)
    x = torch.zeros(1, 2)
    x0 = torch.full((1, 2), float("nan"))
    with pytest.raises(SamplingNumericsError) as exc:
        watchdog.on_step(0, 4, x, 0.5, x0)
    assert exc.value.tensor_name == "x0"


def test_numerics_watchdog_reports_x_when_only_x_bad():
    watchdog = NumericsWatchdog("unipc", interval=1)
    x = torch.full((1, 2), float("inf"))
    x0 = torch.zeros(1, 2)
    with pytest.raises(SamplingNumericsError) as exc:
        watchdog.on_step(0, 4, x, 0.5, x0)
    assert exc.value.tensor_name == "x"


def test_numerics_watchdog_clean_tensors_do_not_raise():
    watchdog = NumericsWatchdog("unipc", interval=1)
    watchdog.on_step(0, 4, torch.zeros(1, 2), 0.5, torch.zeros(1, 2))  # must not raise


def test_numerics_watchdog_switch_step_labels_active_expert():
    watchdog = NumericsWatchdog("unipc", interval=1, switch_step=2)
    bad = torch.full((1, 2), float("nan"))
    with pytest.raises(SamplingNumericsError) as exc:
        watchdog.on_step(1, 5, bad, 0.5, torch.zeros(1, 2))  # before the switch
    assert "expert=high" in str(exc.value)

    with pytest.raises(SamplingNumericsError) as exc:
        watchdog.on_step(3, 5, bad, 0.5, torch.zeros(1, 2))  # after the switch
    assert "expert=low" in str(exc.value)


def test_with_numerics_watchdog_derives_switch_step_from_discontinuity_steps():
    hooks = with_numerics_watchdog((), "unipc", {"discontinuity_steps": frozenset({3})})
    watchdog = hooks[-1]
    assert isinstance(watchdog, NumericsWatchdog)
    assert watchdog.switch_step == 3


def test_with_numerics_watchdog_no_discontinuity_steps_leaves_switch_step_none():
    hooks = with_numerics_watchdog((), "unipc", None)
    assert hooks[-1].switch_step is None
