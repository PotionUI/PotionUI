from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.lora.apply import apply_loras, remove_loras, temporarily_applied_loras
from vendor.gpl.comfyui.ops import pick_operations

from tests.platform.runtime.native.lora.test_lora import _build, _kohya_lora


def _weights(module) -> dict:
    return {name: p.detach().clone() for name, p in module.named_parameters()}


def _assert_bit_identical(before: dict, module) -> None:
    after = _weights(module)
    assert before.keys() == after.keys()
    for name, tensor in before.items():
        assert torch.equal(tensor, after[name]), name


def _inputs():
    return torch.randn(1, 16, 16, 16), torch.tensor([0.5]), torch.randn(1, 7, 32)


@pytest.mark.parametrize("ops", [
    pick_operations(torch.float32, torch.float32),
    pick_operations(torch.float16, torch.float32),
], ids=["standard", "manual_cast"])
def test_runtime_only_scoped_lora_leaves_weights_bit_identical_over_three_cycles(ops):
    m = _build(ops)
    qkv = m.double_blocks[0].img_attn.qkv
    before = _weights(m)
    x, t, ctx = _inputs()
    with torch.no_grad():
        base = m(x, t, ctx)

    for _ in range(3):
        with temporarily_applied_loras(m, [(_kohya_lora(), 1.0)], runtime_only=True):
            assert qkv.lora_deltas
            assert getattr(qkv, "_native_lora_inplace", None) is None
            with torch.no_grad():
                during = m(x, t, ctx)
            assert not torch.allclose(base, during)
        assert not qkv.lora_deltas
        _assert_bit_identical(before, m)

    with torch.no_grad():
        after = m(x, t, ctx)
    assert torch.equal(base, after)


def test_runtime_only_output_matches_the_baked_application():
    x, t, ctx = _inputs()
    torch.manual_seed(11)
    baked = _build()
    apply_loras(baked, [(_kohya_lora(), 1.0)])
    with torch.no_grad():
        expected = baked(x, t, ctx)

    torch.manual_seed(11)
    runtime = _build()
    with temporarily_applied_loras(runtime, [(_kohya_lora(), 1.0)], runtime_only=True):
        with torch.no_grad():
            got = runtime(x, t, ctx)
    assert torch.allclose(expected, got, atol=1e-4)


def test_default_scoped_apply_still_bakes_in_place_and_drifts_the_weight():
    m = _build()
    qkv = m.double_blocks[0].img_attn.qkv
    w0 = qkv.weight.detach().clone()
    with temporarily_applied_loras(m, [(_kohya_lora(), 1.0)]):
        assert not qkv.lora_deltas
        assert not torch.equal(w0, qkv.weight)


def test_runtime_only_leaves_a_preexisting_baked_stack_untouched():
    m = _build()
    apply_loras(m, [(_kohya_lora(seed=5), 1.0)])
    before = _weights(m)
    for _ in range(3):
        with temporarily_applied_loras(m, [(_kohya_lora(seed=6), 0.7)], runtime_only=True):
            pass
        _assert_bit_identical(before, m)
    remove_loras(m)
