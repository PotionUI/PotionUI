from types import SimpleNamespace

import pytest
import torch

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.model_loader.z_image.main import ModelLoaderZImagePipe
from src.platform.runtime import gpu as gpu_module
from src.platform.runtime import vram_cap
from src.platform.runtime.native.engine import NativeEngineLoader
from src.platform.runtime.native.kernels import disable_weight_init, manual_cast

GB = 1024 ** 3
ZIMAGE_DIT_GB = 12.3


def _nvml_missing():
    raise RuntimeError("NVML Shared Library Not Found")


@pytest.fixture(autouse=True)
def _vram_cap_reset(monkeypatch):
    monkeypatch.delenv(vram_cap.VRAM_CAP_ENV_VAR, raising=False)
    vram_cap.reset_for_tests()
    yield
    vram_cap.reset_for_tests()


@pytest.fixture
def windows_without_nvml(monkeypatch):
    state = SimpleNamespace(free=17 * GB, total=24 * GB, reserved=int(0.06 * GB), allocated=0, initialized=True)
    monkeypatch.setattr(gpu_module, "nvmlInit", _nvml_missing)
    monkeypatch.setattr("torch.cuda.is_available", lambda: True)
    monkeypatch.setattr("torch.cuda.is_initialized", lambda: state.initialized)
    monkeypatch.setattr("torch.cuda.device_count", lambda: 1)
    monkeypatch.setattr("torch.cuda.mem_get_info", lambda index: (state.free, state.total))
    monkeypatch.setattr("torch.cuda.memory_reserved", lambda *a: state.reserved)
    monkeypatch.setattr("torch.cuda.memory_allocated", lambda *a: state.allocated)
    monkeypatch.setattr("torch.cuda.get_device_properties", lambda index: SimpleNamespace(uuid="abc"))
    return state


def _zimage_budget(gpu) -> float | None:
    loader = ModelLoaderZImagePipe(config={})
    return loader._vram_budget(PipeInput(input={"GPU": gpu}))


def _dit_ops(vram_gb):
    loader = NativeEngineLoader(device="cuda:0", vram_gb=vram_gb)
    weight = torch.zeros(1, dtype=torch.bfloat16)
    return loader._ops_for("dit", ZIMAGE_DIT_GB, torch.bfloat16, torch.bfloat16, None, {"w": weight})


class TestZImageLoaderBudget:
    def test_cuda_free_memory_reaches_the_loader_when_nvml_is_missing(self, windows_without_nvml):
        budget = _zimage_budget(gpu_module.GpuMonitor())

        assert budget == pytest.approx((17.0 + 0.06) * 0.97)

    def test_a_dit_that_fits_is_built_resident_not_for_streaming(self, windows_without_nvml):
        ops = _dit_ops(_zimage_budget(gpu_module.GpuMonitor()))

        assert ops is disable_weight_init

    def test_the_near_zero_budget_built_the_dit_for_streaming(self, windows_without_nvml, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "0.058")
        budget = _zimage_budget(gpu_module.GpuMonitor())

        assert budget == pytest.approx(0.06 * 0.97)
        assert _dit_ops(budget) is manual_cast

    def test_cuda_not_started_yet_reaches_the_loader_as_no_budget(self, windows_without_nvml):
        windows_without_nvml.initialized = False

        budget = _zimage_budget(gpu_module.GpuMonitor())

        assert budget is None
        assert _dit_ops(budget) is disable_weight_init

    def test_unknown_hardware_reaches_the_loader_as_no_budget(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _nvml_missing)
        monkeypatch.setattr("torch.cuda.is_available", lambda: False)

        budget = _zimage_budget(gpu_module.GpuMonitor())

        assert budget is None
        assert _dit_ops(budget) is disable_weight_init
