"""GpuManager() must never raise when no NVIDIA driver/GPU is present (a CPU-only
host is a supported configuration). Regression test for a real bug:
`__init__` used to call `nvmlInit()`/`nvmlDeviceGetHandleByIndex(0)` with no
guard, so `build_container()` (src/bootstrap/container.py) crashed on boot on
any CPU-only host - including the `--no-gpu` path of
`tests/e2e/harness/onboarding_e2e.py`, which only trims the recipe's
GPU-dependent steps and never touched whether the backend process itself
could start.
"""

from src.platform.runtime import gpu as gpu_module


def _broken_nvml_init():
    raise RuntimeError("NVML Shared Library Not Found")


class TestConstructionWithoutADriver:
    def test_init_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        gpu_module.GpuManager()  # must not raise

    def test_available_is_false(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuManager()
        assert g.available is False
        assert g.handle is None

    def test_vram_getters_report_zero_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        # A real CPU-only host has no CUDA either - torch.cuda.is_available()
        # would be False there too. This process may genuinely have a CUDA
        # device (e.g. these tests running on the maintainer's GPU box), so
        # pin it here to reproduce the true CPU-only case rather than a
        # partial (no-NVML, yes-CUDA) state that can't occur on real hardware.
        monkeypatch.setattr("torch.cuda.is_available", lambda: False)
        g = gpu_module.GpuManager()
        assert g.get_total_vram() == 0
        assert g.get_free_vram() == 0
        assert g.get_used_vram() == 0
        assert g.get_available_vram() == 0.0
        assert g.get_vram_budget() == 0.0

    def test_temperature_is_zero_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuManager()
        assert g.get_temperature() == 0

    def test_can_fit_in_vram_reports_false_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuManager()
        assert g.can_fit_in_vram(1.0) is False

    def test_log_vram_status_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuManager()
        g.log_vram_status("test")  # must not raise

    def test_del_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuManager()
        g.__del__()  # must not raise, and must not call nvmlShutdown


class TestConstructionWithAWorkingDriver:
    def test_handle_lookup_failure_after_init_also_degrades_gracefully(self, monkeypatch):
        """nvmlInit() can succeed while nvmlDeviceGetHandleByIndex(0) still fails
        (e.g. a driver present but no visible device) - the same guard must cover
        both calls."""
        monkeypatch.setattr(gpu_module, "nvmlInit", lambda: None)

        def _broken_handle(index):
            raise RuntimeError("No devices found")

        monkeypatch.setattr(gpu_module, "nvmlDeviceGetHandleByIndex", _broken_handle)
        g = gpu_module.GpuManager()
        assert g.available is False
        assert g.get_total_vram() == 0
