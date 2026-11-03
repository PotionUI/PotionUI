from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.cancellation import cancellation_scope
from src.platform.runtime.native.errors import SamplingCancelled

from .test_ltx_forward import TINY_19B, TINY_25, _av_inputs
from .test_ltx_forward import _build as _build_ltx
from .test_minimax_h3_model import TINY_FULL, _build_ready, _fbcache_forward, _fbcache_inputs, _tiny_layout
from .test_wan_model import TINY_T2V
from .test_wan_model import _build as _build_wan


class _Flag:
    def __init__(self) -> None:
        self.set = False

    def __call__(self) -> bool:
        return self.set


def _cancel_after_first_block(blocks, flag: _Flag) -> dict[str, int]:
    calls = {"first": 0, "second": 0}
    first, second = blocks[0], blocks[1]
    first_forward, second_forward = first.forward, second.forward

    def first_then_cancel(*args, **kwargs):
        calls["first"] += 1
        out = first_forward(*args, **kwargs)
        flag.set = True
        return out

    def counting(*args, **kwargs):
        calls["second"] += 1
        return second_forward(*args, **kwargs)

    first.forward = first_then_cancel
    second.forward = counting
    return calls


def _wan_case():
    m = _build_wan(TINY_T2V)
    x, ctx = torch.randn(1, 16, 4, 16, 16), torch.randn(1, 12, 32)
    return m, m.blocks, lambda: m(x, torch.tensor([0.5]), ctx)


def _ltx_case(cfg):
    m = _build_ltx(cfg)
    vx, ax, ctx, ts = _av_inputs(cfg)
    return m, m.transformer_blocks, lambda: m.forward([vx, ax], ts, ctx)


def _h3_case():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_FULL, layout)
    return m, m.blocks, lambda: _fbcache_forward(m, layout, inputs, torch.tensor([0.2, 0.9]))


CASES = {
    "wan": _wan_case,
    "ltx-2": lambda: _ltx_case(TINY_19B),
    "ltx-2.5": lambda: _ltx_case(TINY_25),
    "minimax-h3": _h3_case,
}


@pytest.mark.parametrize("family", list(CASES))
def test_a_cancel_seen_after_a_block_stops_the_dit_before_the_next_one(family):
    _, blocks, run = CASES[family]()
    flag = _Flag()
    calls = _cancel_after_first_block(blocks, flag)
    with torch.no_grad(), pytest.raises(SamplingCancelled) as excinfo:
        with cancellation_scope(flag, step_index=3):
            run()
    assert calls == {"first": 1, "second": 0}
    assert (excinfo.value.step_index, excinfo.value.block_index) == (3, 1)


@pytest.mark.parametrize("family", list(CASES))
def test_a_set_probe_outside_any_scope_leaves_the_forward_untouched(family):
    _, blocks, run = CASES[family]()
    flag = _Flag()
    calls = _cancel_after_first_block(blocks, flag)
    with torch.no_grad():
        run()
        run()
    assert calls == {"first": 2, "second": 2}
