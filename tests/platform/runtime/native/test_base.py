"""Tests for NativeArchModule contract and load_into_module integrity gate."""

from __future__ import annotations

import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native.base import NativeArchModule, load_into_module
from src.platform.runtime.native.detect.registry import ModelSpec
from src.platform.runtime.native.errors import NativeEngineLoadIntegrityError
from vendor.gpl.comfyui.ops import pick_operations


class _Tiny(NativeArchModule):
    def __init__(self, operations):
        super().__init__()
        self.lin = operations.Linear(4, 4, bias=False)
        self.register_buffer("inv_freq", torch.zeros(4), persistent=False)
        self.post_load_called = False

    @classmethod
    def from_config(cls, config, operations):
        return cls(operations)

    def post_load(self):
        # recompute the RoPE-style buffer that empty construction left as zeros.
        self.inv_freq = torch.arange(4, dtype=torch.float32) + 1.0
        self.post_load_called = True


def _spec(**kw):
    return ModelSpec(family="t", variant="v", signature={}, model_class="x:Y", **kw)


def _ops():
    return pick_operations(torch.bfloat16, torch.bfloat16)


def test_from_config_builds_via_operations():
    m = _Tiny.from_config({}, _ops())
    assert isinstance(m, NativeArchModule)
    assert m.lin.in_features == 4


def test_successful_load_calls_post_load():
    m = _Tiny.from_config({}, _ops())
    load_into_module(m, {"lin.weight": torch.randn(4, 4)}, _spec())
    assert m.post_load_called is True
    # buffer was recomputed, not left as garbage zeros.
    assert torch.equal(m.inv_freq, torch.arange(4, dtype=torch.float32) + 1.0)


def test_unexpected_key_outside_allowlist_raises():
    m = _Tiny.from_config({}, _ops())
    with pytest.raises(NativeEngineLoadIntegrityError) as exc:
        load_into_module(m, {"lin.weight": torch.randn(4, 4), "bogus.key": torch.zeros(1)}, _spec())
    assert "bogus.key" in str(exc.value)


def test_unexpected_key_allowlisted_passes():
    m = _Tiny.from_config({}, _ops())
    spec = _spec(expected_unexpected_keys={"bogus.*"})
    load_into_module(m, {"lin.weight": torch.randn(4, 4), "bogus.key": torch.zeros(1)}, spec)
    assert m.post_load_called


def test_missing_key_outside_allowlist_raises():
    m = _Tiny.from_config({}, _ops())
    with pytest.raises(NativeEngineLoadIntegrityError) as exc:
        load_into_module(m, {}, _spec())
    assert "lin.weight" in str(exc.value)


def test_missing_key_allowlisted_passes():
    m = _Tiny.from_config({}, _ops())
    # `operations.Linear` allocates empty and expects the state dict to fill it.
    # An allowlisted-missing weight is never written, so without this the
    # integrity gate scans whatever the allocator handed back and the test
    # passes or fails on heap contents left by earlier tests.
    with torch.no_grad():
        m.lin.weight.zero_()
    spec = _spec(expected_missing_keys={"lin.*"})
    load_into_module(m, {}, spec)
    assert m.post_load_called


def test_nan_in_weights_detected():
    m = _Tiny.from_config({}, _ops())
    bad = torch.randn(4, 4)
    bad[0, 0] = float("nan")
    with pytest.raises(NativeEngineLoadIntegrityError, match="NaN"):
        load_into_module(m, {"lin.weight": bad}, _spec())


def test_meta_device_buffer_detected():
    class _Meta(_Tiny):
        def post_load(self):
            # deliberately leave a buffer on meta to prove the guard fires.
            self.register_buffer("broken", torch.empty(2, device="meta"), persistent=False)
            self.post_load_called = True

    m = _Meta.from_config({}, _ops())
    with pytest.raises(NativeEngineLoadIntegrityError, match="meta device"):
        load_into_module(m, {"lin.weight": torch.randn(4, 4)}, _spec())


def test_native_arch_module_requires_abstract_methods():
    class _Incomplete(NativeArchModule):
        pass

    with pytest.raises(TypeError):
        _Incomplete()
