from types import SimpleNamespace

import pytest

from src.platform.runtime import gpu as gpu_module
from src.platform.runtime import vram_cap

GB = 1024 ** 3
MB = 1024 ** 2


def _nvml_missing():
    raise RuntimeError("NVML Shared Library Not Found")


@pytest.fixture(autouse=True)
def _no_vram_cap(monkeypatch):
    monkeypatch.delenv(vram_cap.VRAM_CAP_ENV_VAR, raising=False)
    vram_cap.reset_for_tests()
    yield
    vram_cap.reset_for_tests()


@pytest.fixture
def cuda(monkeypatch):
    state = SimpleNamespace(
        free=17 * GB, total=24 * GB, reserved=0, allocated=0, queried=[], uuid="abc-123", initialized=True,
    )

    def mem_get_info(index):
        state.queried.append(index)
        return state.free, state.total

    monkeypatch.setattr("torch.cuda.is_available", lambda: True)
    monkeypatch.setattr("torch.cuda.is_initialized", lambda: state.initialized)
    monkeypatch.setattr("torch.cuda.device_count", lambda: 2)
    monkeypatch.setattr("torch.cuda.mem_get_info", mem_get_info)
    monkeypatch.setattr("torch.cuda.memory_reserved", lambda *a: state.reserved)
    monkeypatch.setattr("torch.cuda.memory_allocated", lambda *a: state.allocated)
    monkeypatch.setattr("torch.cuda.get_device_properties", lambda index: SimpleNamespace(uuid=state.uuid))
    return state


@pytest.fixture
def no_nvml(monkeypatch):
    monkeypatch.setattr(gpu_module, "nvmlInit", _nvml_missing)


class TestTorchFallback:
    def test_cuda_without_nvml_is_still_a_known_gpu(self, cuda, no_nvml):
        g = gpu_module.GpuMonitor()

        assert g.available is True
        assert g.source == "torch"
        assert g.get_total_vram() == 24 * 1024
        assert g.get_free_vram() == 17 * 1024

    def test_budget_comes_from_cuda_free_memory_not_zero(self, cuda, no_nvml):
        cuda.reserved, cuda.allocated = 2 * GB, 1 * GB
        g = gpu_module.GpuMonitor()

        assert g.get_available_vram() == pytest.approx(18.0)
        assert g.get_vram_budget(safety_margin=0.97) == pytest.approx(18.0 * 0.97)

    def test_backend_cap_still_bounds_a_torch_reading(self, cuda, no_nvml):
        g = gpu_module.GpuMonitor()
        g.set_vram_cap_gb(8.0)

        assert g.get_vram_budget(safety_margin=1.0) == pytest.approx(8.0)

    def test_reads_the_monitored_device(self, cuda, no_nvml):
        g = gpu_module.GpuMonitor(device_index=1)
        g.get_free_vram()

        assert cuda.queried == [1]

    def test_identity_comes_from_torch_in_nvml_form(self, cuda, no_nvml):
        g = gpu_module.GpuMonitor()

        assert g.device_identity == gpu_module.DeviceIdentity(uuid="GPU-abc-123")

    def test_a_poll_before_cuda_starts_never_reads_torch_memory(self, cuda, no_nvml):
        cuda.initialized = False
        g = gpu_module.GpuMonitor()

        assert g.available is True
        assert g.get_free_vram() is None
        assert g.get_total_vram() is None
        assert g.get_vram_budget() is None
        assert cuda.queried == []

    def test_a_cap_is_the_budget_before_cuda_starts(self, cuda, no_nvml):
        cuda.initialized = False
        g = gpu_module.GpuMonitor()
        g.set_vram_cap_gb(8.0)

        assert g.get_vram_budget() == pytest.approx(8.0)
        assert cuda.queried == []

    def test_readings_start_once_cuda_is_initialised(self, cuda, no_nvml):
        cuda.initialized = False
        g = gpu_module.GpuMonitor()
        assert g.get_free_vram() is None

        cuda.initialized = True

        assert g.get_free_vram() == 17 * 1024
        assert cuda.queried == [0]

    def test_identity_waits_for_cuda_to_start(self, cuda, no_nvml, monkeypatch):
        cuda.initialized = False
        properties = []
        monkeypatch.setattr(
            "torch.cuda.get_device_properties",
            lambda index: properties.append(index) or SimpleNamespace(uuid=cuda.uuid),
        )
        g = gpu_module.GpuMonitor()

        assert g.device_identity is None
        assert properties == []

        cuda.initialized = True

        assert g.device_identity == gpu_module.DeviceIdentity(uuid="GPU-abc-123")

    def test_an_out_of_range_device_is_not_reported(self, cuda, no_nvml):
        g = gpu_module.GpuMonitor(device_index=2)

        assert g.available is False
        assert g.get_vram_budget() is None

    def test_rig_cap_applies_to_torch_readings(self, cuda, no_nvml, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "16")
        g = gpu_module.GpuMonitor()

        assert g.get_total_vram() == 16 * 1024
        assert g.get_free_vram() == 9 * 1024

    def test_teardown_never_shuts_down_an_nvml_it_did_not_start(self, cuda, no_nvml, monkeypatch):
        calls = []
        monkeypatch.setattr(gpu_module, "nvmlShutdown", lambda: calls.append("shutdown"))
        g = gpu_module.GpuMonitor()

        g.__del__()

        assert calls == []


class TestNvmlPreferred:
    def test_working_nvml_never_reads_torch_memory(self, cuda, monkeypatch):
        def fail(index):
            raise AssertionError("torch must not be read when NVML works")

        monkeypatch.setattr("torch.cuda.mem_get_info", fail)
        monkeypatch.setattr(gpu_module, "nvmlInit", lambda: None)
        monkeypatch.setattr(gpu_module, "nvmlDeviceGetHandleByIndex", lambda index: f"handle-{index}")
        monkeypatch.setattr(gpu_module, "nvmlDeviceGetUUID", lambda handle: b"GPU-from-nvml")
        monkeypatch.setattr(gpu_module, "nvmlDeviceGetPciInfo", lambda handle: SimpleNamespace(busId=b"0000:01:00.0"))
        monkeypatch.setattr(gpu_module, "nvmlDeviceGetName", lambda handle: b"Fake GPU")
        monkeypatch.setattr(
            gpu_module, "nvmlDeviceGetMemoryInfo",
            lambda handle: SimpleNamespace(total=32 * GB, free=30 * GB, used=2 * GB),
        )

        g = gpu_module.GpuMonitor()

        assert g.source == "nvml"
        assert g.get_free_vram() == 30 * 1024
        assert g.device_identity == gpu_module.DeviceIdentity(uuid="GPU-from-nvml", pci_bus_id="0000:01:00.0")


class TestNoGpuAtAll:
    @pytest.fixture
    def cpu_host(self, monkeypatch, no_nvml):
        monkeypatch.setattr("torch.cuda.is_available", lambda: False)

    def test_budget_is_unknown_not_zero(self, cpu_host):
        g = gpu_module.GpuMonitor()

        assert g.available is False
        assert g.get_free_vram() is None
        assert g.get_available_vram() is None
        assert g.get_vram_budget() is None

    def test_a_cap_or_hint_is_the_budget_when_hardware_is_unknown(self, cpu_host):
        g = gpu_module.GpuMonitor()
        g.set_vram_cap_gb(24.0)

        assert g.get_vram_budget() == pytest.approx(24.0)
        assert g.get_vram_budget(8.0) == pytest.approx(8.0)


class TestEffectiveBudgetWithUnknownHardware:
    def test_no_caps_and_unknown_hardware_is_unknown(self):
        assert gpu_module.effective_vram_budget_gb(None, None, None) is None

    def test_caps_compose_without_a_hardware_bound(self):
        assert gpu_module.effective_vram_budget_gb(24.0, 8.0, None) == pytest.approx(8.0)

    def test_known_hardware_still_bounds_the_caps(self):
        assert gpu_module.effective_vram_budget_gb(24.0, None, 4.0) == pytest.approx(4.0)
