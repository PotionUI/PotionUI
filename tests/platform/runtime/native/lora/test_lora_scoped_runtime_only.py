from __future__ import annotations

from unittest.mock import patch

import pytest
import torch

from src.platform.runtime.native.lora.apply import apply_loras, remove_loras, temporarily_applied_loras
from src.platform.runtime.native.kernels import pick_operations

from tests.platform.runtime.native.lora.test_lora import _build, _kohya_lora
from src.platform.settings.runtime_flags import runtime_flag_values


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


def _owns_storage(tensor) -> bool:
    return tensor.untyped_storage().nbytes() == tensor.numel() * tensor.element_size()


def test_scope_exit_frees_the_scoped_rows_fused_into_a_resident_delta(monkeypatch):
    monkeypatch.setitem(runtime_flag_values(), "native_lora_fused", True)
    m = _build()
    qkv = m.double_blocks[0].img_attn.qkv
    apply_loras(m, [(_kohya_lora(rank=4), 1.0)], runtime_only=True)
    resident = qkv.lora_deltas[0]
    x, t, ctx = _inputs()
    with torch.no_grad():
        base = m(x, t, ctx)

    with temporarily_applied_loras(m, [(_kohya_lora(rank=64, seed=9), 1.0)], runtime_only=True):
        with torch.no_grad():
            m(x, t, ctx)
        assert not _owns_storage(resident.down)
        assert not _owns_storage(resident.up)

    assert len(qkv.lora_deltas) == 1 and qkv.lora_deltas[0] is resident
    assert _owns_storage(resident.down)
    assert _owns_storage(resident.up)
    with torch.no_grad():
        after = m(x, t, ctx)
    torch.testing.assert_close(after, base)


def test_a_standard_layer_returns_to_its_plain_forward_when_the_scope_exits():
    m = _build()
    qkv = m.double_blocks[0].img_attn.qkv
    x, t, ctx = _inputs()
    with patch.object(qkv, "forward_comfy_cast_weights", wraps=qkv.forward_comfy_cast_weights) as cast:
        with temporarily_applied_loras(m, [(_kohya_lora(), 1.0)], runtime_only=True):
            with torch.no_grad():
                m(x, t, ctx)
        during = cast.call_count
        with torch.no_grad():
            m(x, t, ctx)
    assert during > 0
    assert cast.call_count == during
    assert qkv.comfy_cast_weights is False


def test_a_scoped_lora_that_matches_keys_logs_the_match_count_at_info(caplog):
    m = _build()
    with caplog.at_level("INFO", logger="src.platform.runtime.native.lora.apply"):
        with temporarily_applied_loras(m, [(_kohya_lora(), 1.0)], runtime_only=True):
            pass
    records = [r for r in caplog.records if "scoped" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelname == "INFO"
    assert "matched 1 weights" in records[0].getMessage()


def test_a_scoped_lora_that_matches_nothing_warns(caplog):
    m = _build()
    dead = _kohya_lora(stem="lora_unet_nothing_here_0_attn")
    with caplog.at_level("INFO", logger="src.platform.runtime.native.lora.apply"):
        with temporarily_applied_loras(m, [(dead, 1.0)], runtime_only=True):
            pass
    records = [r for r in caplog.records if "scoped" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    assert "matched 0 weights" in records[0].getMessage()
