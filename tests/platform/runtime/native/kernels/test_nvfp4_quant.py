from __future__ import annotations

import logging
import sys
import types

import pytest
import torch

from src.platform.runtime.native import kernels
from src.platform.runtime.native.io.quant_formats import quantize_nvfp4_dynamic
from src.platform.runtime.native.kernels import nvfp4_quant, nvfp4_quant_disabled_reason, quantize_nvfp4_activation
from tests.fixtures.kernel_capabilities import ada, ampere, blackwell, fake_capabilities, fake_device, installed_capabilities

TRITON_MODULE = "src.platform.runtime.native.kernels.nvfp4_quant_triton"
EAGER = ("eager-packed", "eager-block", "eager-scale")


class FakeCudaTensor:
    def __init__(self, rows=4, cols=32, dtype=torch.bfloat16, col_stride=1, device="cuda:0", lead=()):
        self.shape = (*lead, rows, cols)
        self.dtype = dtype
        self.device = torch.device(device)
        self._col_stride = col_stride

    def dim(self):
        return len(self.shape)

    def numel(self):
        return torch.Size(self.shape).numel()

    def stride(self, dim):
        return self._col_stride if dim in (len(self.shape) - 1, -1) else self.shape[-1]

    def detach(self):
        return self


@pytest.fixture(autouse=True)
def _fresh_state():
    nvfp4_quant.reset_nvfp4_quant_state()
    yield
    nvfp4_quant.reset_nvfp4_quant_state()


@pytest.fixture
def triton_kernel(monkeypatch):
    calls = []
    fake = types.ModuleType(TRITON_MODULE)

    def quantize(x, tensor_amax):
        calls.append((x, tensor_amax))
        if fake.error is not None:
            raise fake.error
        return "triton-packed", "triton-block", "triton-scale"

    fake.quantize = quantize
    fake.error = None
    fake.calls = calls
    monkeypatch.setitem(sys.modules, TRITON_MODULE, fake)
    monkeypatch.setattr(kernels, "nvfp4_quant_triton", fake, raising=False)
    return fake


@pytest.fixture
def eager(monkeypatch):
    calls = []

    def quantize(x2d, tensor_amax=None):
        calls.append((x2d, tensor_amax))
        return EAGER

    monkeypatch.setattr(nvfp4_quant, "quantize_nvfp4_dynamic", quantize)
    return calls


@pytest.fixture
def amax(monkeypatch):
    calls = []

    def fake_amax(x):
        calls.append(x)
        return torch.tensor(5376.0)

    monkeypatch.setattr(nvfp4_quant, "nvfp4_activation_amax", fake_amax)
    return calls


def _on(*devices, triton=True):
    return installed_capabilities(fake_capabilities(*devices, triton=triton))


def test_a_cpu_tensor_is_quantized_by_the_eager_code_bit_for_bit(triton_kernel):
    x = torch.randn(7, 64, generator=torch.Generator().manual_seed(0)).to(torch.bfloat16)
    with _on(blackwell()):
        packed, block, scale = quantize_nvfp4_activation(x)
    want_packed, want_block, want_scale = quantize_nvfp4_dynamic(x)
    assert torch.equal(packed, want_packed)
    assert torch.equal(block.view(torch.uint8), want_block.view(torch.uint8))
    assert torch.equal(scale, want_scale)
    assert triton_kernel.calls == []


def test_a_blackwell_gpu_with_triton_runs_the_kernel_on_the_whole_tensor_amax(triton_kernel, eager, amax):
    x = FakeCudaTensor()
    with _on(blackwell()):
        assert quantize_nvfp4_activation(x) == ("triton-packed", "triton-block", "triton-scale")
    assert eager == []
    assert amax == [x]
    assert triton_kernel.calls[0][0] is x
    assert torch.equal(triton_kernel.calls[0][1], torch.tensor(5376.0))


class FakeCudaAmax:
    device = torch.device("cuda:0")

    def detach(self):
        return self


def test_a_given_tensor_amax_skips_the_global_reduction(triton_kernel, eager, amax):
    given = FakeCudaAmax()
    with _on(ada()):
        quantize_nvfp4_activation(FakeCudaTensor(), tensor_amax=given)
    assert amax == []
    assert eager == []
    assert triton_kernel.calls[0][1] is given


@pytest.mark.parametrize(
    "case",
    [
        "no triton",
        "ampere",
        "hip",
        "k not a multiple of 16",
        "strided columns",
        "int8 input",
        "amax on another device",
        "no rows",
        "three dims",
    ],
)
def test_unsupported_inputs_and_machines_take_the_eager_path(case, triton_kernel, eager, amax):
    x = FakeCudaTensor()
    tensor_amax = None
    devices, triton = (blackwell(),), True
    if case == "no triton":
        triton = False
    elif case == "ampere":
        devices = (ampere(),)
    elif case == "hip":
        devices = (fake_device((9, 4), vendor="hip"),)
    elif case == "k not a multiple of 16":
        x = FakeCudaTensor(cols=40)
    elif case == "strided columns":
        x = FakeCudaTensor(col_stride=2)
    elif case == "int8 input":
        x = FakeCudaTensor(dtype=torch.int8)
    elif case == "amax on another device":
        tensor_amax = torch.tensor(3.0)
    elif case == "no rows":
        x = FakeCudaTensor(rows=0)
    elif case == "three dims":
        x = FakeCudaTensor(rows=16, cols=1, lead=(2,))
    with _on(*devices, triton=triton):
        assert quantize_nvfp4_activation(x, tensor_amax=tensor_amax) == EAGER
    assert triton_kernel.calls == []
    assert eager == [(x, tensor_amax)]


def test_a_kernel_failure_disables_triton_for_the_process_and_warns_once(triton_kernel, eager, amax, caplog):
    triton_kernel.error = RuntimeError("PTX assembly failed")
    x = FakeCudaTensor()
    with _on(blackwell()), caplog.at_level(logging.WARNING, logger=nvfp4_quant.__name__):
        assert quantize_nvfp4_activation(x) == EAGER
        assert quantize_nvfp4_activation(x) == EAGER
    assert len(triton_kernel.calls) == 1
    assert len(eager) == 2
    assert "PTX assembly failed" in nvfp4_quant_disabled_reason()
    assert len([r for r in caplog.records if "Triton kernel is off" in r.getMessage()]) == 1


def test_a_missing_triton_install_disables_instead_of_crashing(monkeypatch, eager, amax):
    monkeypatch.setitem(sys.modules, TRITON_MODULE, None)
    monkeypatch.delattr(kernels, "nvfp4_quant_triton", raising=False)
    with _on(blackwell()):
        assert quantize_nvfp4_activation(FakeCudaTensor()) == EAGER
    assert "ModuleNotFoundError" in nvfp4_quant_disabled_reason()


@pytest.mark.parametrize(
    "error",
    [torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 2 GiB"),
     RuntimeError("CUDA driver error: out of memory")],
)
def test_out_of_memory_propagates_without_disabling(error, triton_kernel, eager, amax):
    triton_kernel.error = error
    with _on(blackwell()), pytest.raises(type(error)):
        quantize_nvfp4_activation(FakeCudaTensor())
    assert nvfp4_quant_disabled_reason() is None
    assert eager == []
