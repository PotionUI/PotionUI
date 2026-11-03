from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.flux3.model import Flux3
from src.platform.runtime.native.arch.moss_image.model import MossImage
from src.platform.runtime.native.cancellation import cancellation_scope
from src.platform.runtime.native.engine import RunCache
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.kernels import pick_operations

from .test_anima_model import TINY as TINY_ANIMA
from .test_anima_model import _build_ready as _build_anima
from .test_cancel_between_blocks import _cancel_after_first_block, _Flag
from .test_flux_model import TINY_FLUX1, TINY_FLUX2
from .test_flux_model import _build_ready as _build_flux
from .test_krea2_model import TINY as TINY_KREA2
from .test_krea2_model import _build_ready as _build_krea2
from .test_moss_image_parity import CONFIG as TINY_MOSS
from .test_moss_image_parity import _inputs as _moss_inputs
from .test_qwen_image21_fun_control import _inputs as _qwen21_control_inputs
from .test_qwen_image21_fun_control import _pair as _qwen21_control_pair
from .test_qwen_image21_model import TINY as TINY_QWEN21
from .test_qwen_image21_model import _build_ready as _build_qwen21
from .test_qwen_image_model import TINY as TINY_QWEN
from .test_qwen_image_model import _build_ready as _build_qwen
from .test_z_image_model import _build as _build_z_image

_T = torch.tensor([0.5])

TINY_FLUX3 = {
    "image_model": "flux3", "hidden_size": 64, "context_in_dim": 32, "in_channels": 16,
    "num_heads": 2, "head_dim": 32, "depth": 1, "depth_single_blocks": 1,
    "mlp_hidden": 192, "axes_dim": [8, 8, 8, 8], "theta": 10000,
    "flow_map": False, "has_image_cond": True,
}


def _randomised(module):
    torch.manual_seed(0)
    state = {}
    for key, value in module.state_dict().items():
        if not value.is_floating_point():
            state[key] = value.clone()
        elif "norm" in key and (key.endswith(".weight") or key.endswith(".scale")):
            state[key] = torch.ones_like(value)
        else:
            state[key] = torch.randn_like(value) * 0.02
    module.load_state_dict(state)
    return module.eval()


def _flux_case(config, **kwargs):
    m = _build_flux(config)
    x, ctx = torch.randn(1, 16, 16, 16), torch.randn(1, 5, 32)
    return [m.double_blocks[0], m.single_blocks[0]], lambda: m(x, _T, ctx, **kwargs)


def _flux3_case():
    m = _randomised(Flux3.from_config(TINY_FLUX3, pick_operations(torch.float32, torch.float32)))
    x, ctx = torch.randn(1, 16, 4, 6), torch.randn(1, 7, 32)
    return [m.content_mode_blocks["image"][0], m.single_blocks[0]], lambda: m(x, _T, ctx)


def _krea2_case():
    m = _build_krea2(dict(TINY_KREA2, layers=2))
    x, ctx = torch.randn(1, 4, 8, 8), torch.randn(1, 5, 3, 16)
    mask = torch.ones(1, 5, dtype=torch.long)
    return m.blocks, lambda: m(x, _T, ctx, attention_mask=mask)


def _qwen_case():
    m = _build_qwen(TINY_QWEN)
    x, ctx = torch.randn(1, 4, 1, 8, 8), torch.randn(1, 5, 12)
    mask = torch.ones(1, 5, dtype=torch.long)
    return m.transformer_blocks, lambda: m(x, _T, ctx, attention_mask=mask)


def _qwen21_case():
    m = _build_qwen21(TINY_QWEN21)
    x, ctx = torch.randn(1, 8, 4, 4), torch.randn(1, 5, 12)
    return m.transformer_blocks, lambda: m(x, _T, ctx)


def _anima_case():
    m = _build_anima(TINY_ANIMA)
    x, ctx = torch.randn(1, 8, 1, 16, 16), torch.randn(1, 5, 16)
    ids, weights = torch.randint(0, 100, (1, 7)), torch.ones(1, 7)
    return m.blocks, lambda: m(x, torch.tensor([0.7]), ctx, t5xxl_ids=ids, t5xxl_weights=weights)


def _z_image_case():
    m = _build_z_image()
    x, ctx = torch.randn(1, 16, 16, 16), torch.randn(1, 4, 8)
    return m.layers, lambda: m(x, _T, ctx)


def _moss_image_case():
    m = _randomised(MossImage.from_config(TINY_MOSS, pick_operations(torch.float32, torch.float32)))
    x, ctx, mask = _moss_inputs(0, 4, 4, [5])
    return m.layers, lambda: m(x, _T, ctx, attention_mask=mask)


CASES = {
    "flux1": lambda: _flux_case(TINY_FLUX1, y=torch.randn(1, 768), guidance=torch.tensor([3.5])),
    "flux2-klein": lambda: _flux_case(TINY_FLUX2),
    "flux3": _flux3_case,
    "krea-2": _krea2_case,
    "qwen-image": _qwen_case,
    "qwen-image-2.1": _qwen21_case,
    "anima": _anima_case,
    "z-image": _z_image_case,
    "moss-image": _moss_image_case,
}


@pytest.mark.parametrize("family", list(CASES))
def test_a_cancel_seen_after_a_block_stops_the_dit_before_the_next_one(family):
    blocks, run = CASES[family]()
    flag = _Flag()
    calls = _cancel_after_first_block(blocks, flag)
    with torch.no_grad(), pytest.raises(SamplingCancelled) as excinfo:
        with cancellation_scope(flag, step_index=3):
            run()
    assert calls == {"first": 1, "second": 0}
    assert (excinfo.value.step_index, excinfo.value.block_index) == (3, 1)


@pytest.mark.parametrize("family", list(CASES))
def test_a_cancel_already_set_stops_the_dit_before_its_first_block(family):
    blocks, run = CASES[family]()
    calls = _cancel_after_first_block(blocks, _Flag())
    with torch.no_grad(), pytest.raises(SamplingCancelled) as excinfo:
        with cancellation_scope(lambda: True, step_index=2):
            run()
    assert calls == {"first": 0, "second": 0}
    assert excinfo.value.step_index == 2


def test_qwen_image21_stops_between_blocks_when_it_replays_a_cached_prefix():
    m = _build_qwen21(TINY_QWEN21)
    x, ctx = torch.randn(1, 8, 4, 4), torch.randn(1, 5, 12)
    cache = RunCache(lambda: 1)
    m.run_cache = cache
    flag = _Flag()
    try:
        with torch.no_grad():
            m(x, torch.tensor([0.9]), ctx)
            assert len(cache) == 1
            calls = _cancel_after_first_block(m.transformer_blocks, flag)
            with pytest.raises(SamplingCancelled) as excinfo:
                with cancellation_scope(flag, step_index=1):
                    m(x, torch.tensor([0.5]), ctx)
    finally:
        cache.clear()
        m.run_cache = None
    assert calls == {"first": 1, "second": 0}
    assert (excinfo.value.step_index, excinfo.value.block_index) == (1, 1)


def test_qwen_image21_stops_between_blocks_on_a_controlled_step():
    _, controlled = _qwen21_control_pair()
    x, t, context, control = _qwen21_control_inputs()
    flag = _Flag()
    calls = _cancel_after_first_block(controlled.transformer_blocks, flag)
    with torch.no_grad(), pytest.raises(SamplingCancelled):
        with cancellation_scope(flag):
            controlled(x, t, context, control_context=control)
    assert calls == {"first": 1, "second": 0}


@pytest.mark.parametrize("family", list(CASES))
def test_a_set_probe_outside_any_scope_leaves_the_forward_untouched(family):
    blocks, run = CASES[family]()
    flag = _Flag()
    calls = _cancel_after_first_block(blocks, flag)
    with torch.no_grad():
        run()
        run()
    assert calls == {"first": 2, "second": 2}


@pytest.mark.parametrize("family", list(CASES))
def test_an_open_scope_that_is_never_cancelled_gives_a_bit_identical_output(family):
    _, run = CASES[family]()
    with torch.no_grad():
        plain = run()
        with cancellation_scope(lambda: False, step_index=0):
            scoped = run()
    assert torch.equal(plain, scoped)
