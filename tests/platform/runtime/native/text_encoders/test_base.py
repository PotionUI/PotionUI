"""``NativeTextEncoder`` base helpers: ``_module_unload``'s no-host-copy contract."""

from __future__ import annotations

import gc
import weakref

import pytest

torch = pytest.importorskip("torch")

from src.platform.runtime.native.text_encoders.base import _module_unload  # noqa: E402


def test_module_unload_releases_storage_in_place_without_a_host_copy():
    """``_module_unload`` backs every concrete encoder's ``unload()``
    override -- it must never call ``module.to("cpu")`` (a full host-RAM
    copy of a possibly GPU-resident encoder) and must instead drop each
    parameter's storage in place, in the same way as
    ``NativeModel.unload()``'s own release path."""
    module = torch.nn.Linear(4, 4, bias=False)
    original_data = module.weight.data

    def _forbidden_to(*_args, **_kwargs):
        raise AssertionError("_module_unload must not call module.to() -- that copies to host RAM")

    module.to = _forbidden_to  # instance-level: any device-move call fails the test

    param_ref = weakref.ref(module.weight)

    _module_unload(module)

    assert module.weight.data.numel() == 0
    assert module.weight.data.dtype == original_data.dtype
    assert module.weight.data.data_ptr() != original_data.data_ptr()

    del module
    gc.collect()
    assert param_ref() is None, "the parameter must become collectible once its only holder is dropped"


def test_module_unload_is_a_no_op_for_none():
    _module_unload(None)  # must not raise
