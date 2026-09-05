"""GpuMonitor() must never raise when no NVIDIA driver/GPU is present (a CPU-only
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
        gpu_module.GpuMonitor()  # must not raise

    def test_available_is_false(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuMonitor()
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
        g = gpu_module.GpuMonitor()
        assert g.get_total_vram() == 0
        assert g.get_free_vram() == 0
        assert g.get_used_vram() == 0
        assert g.get_available_vram() == 0.0
        assert g.get_vram_budget() == 0.0

    def test_temperature_is_zero_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuMonitor()
        assert g.get_temperature() == 0

    def test_can_fit_in_vram_reports_false_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuMonitor()
        assert g.can_fit_in_vram(1.0) is False

    def test_log_vram_status_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuMonitor()
        g.log_vram_status("test")  # must not raise

    def test_del_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuMonitor()
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
        g = gpu_module.GpuMonitor()
        assert g.available is False
        assert g.get_total_vram() == 0


def _stub_working_driver(monkeypatch, *, uuid=None, pci_bus_id=None, uuid_raises=False, pci_raises=False):
    """A driver present with a real handle at whatever index is asked for -
    never real NVML. `nvmlDeviceGetName`/`_get_memory_info` are left alone
    (already exercised by the tests above); only the identity-resolution
    calls are controlled here."""
    monkeypatch.setattr(gpu_module, "nvmlInit", lambda: None)
    monkeypatch.setattr(gpu_module, "nvmlDeviceGetHandleByIndex", lambda index: f"handle-{index}")
    monkeypatch.setattr(gpu_module, "nvmlDeviceGetName", lambda handle: "Fake GPU")
    monkeypatch.setattr(
        gpu_module, "nvmlDeviceGetMemoryInfo",
        lambda handle: gpu_module._CappedMemInfo(total=0, free=0, used=0),
    )

    def _get_uuid(handle):
        if uuid_raises:
            raise RuntimeError("NVML UUID query failed")
        return uuid

    def _get_pci_info(handle):
        if pci_raises:
            raise RuntimeError("NVML PCI query failed")

        class _Pci:
            busId = pci_bus_id

        return _Pci()

    monkeypatch.setattr(gpu_module, "nvmlDeviceGetUUID", _get_uuid)
    monkeypatch.setattr(gpu_module, "nvmlDeviceGetPciInfo", _get_pci_info)


class TestDeviceIdentity:
    """`GpuMonitor.device_identity` - a stable UUID, resolved once at init,
    that a preset requirement check (`vram_min_gb`) uses to confirm a
    backend's own resolved device is THIS exact physical card, never just
    an index match (see `src.features.backends.base_backend.DeviceIdentity`
    - re-exported from `src.platform.runtime.gpu`)."""

    def test_no_driver_leaves_identity_none(self, monkeypatch):
        monkeypatch.setattr(gpu_module, "nvmlInit", _broken_nvml_init)
        g = gpu_module.GpuMonitor()
        assert g.device_identity is None

    def test_working_driver_resolves_uuid_and_pci_bus_id(self, monkeypatch):
        _stub_working_driver(monkeypatch, uuid="GPU-abc123", pci_bus_id="00000000:01:00.0")
        g = gpu_module.GpuMonitor()
        assert g.device_identity == gpu_module.DeviceIdentity(uuid="GPU-abc123", pci_bus_id="00000000:01:00.0")

    def test_uuid_query_failure_leaves_identity_none_not_raising(self, monkeypatch):
        _stub_working_driver(monkeypatch, uuid_raises=True)
        g = gpu_module.GpuMonitor()  # must not raise
        assert g.device_identity is None

    def test_pci_query_failure_still_yields_an_identity_from_the_uuid_alone(self, monkeypatch):
        """PCI location is a secondary, informational signal
        (`DeviceIdentity.pci_bus_id` is `compare=False`) - its absence must
        not withhold the UUID, which is the only thing correspondence
        checks actually need."""
        _stub_working_driver(monkeypatch, uuid="GPU-abc123", pci_raises=True)
        g = gpu_module.GpuMonitor()  # must not raise
        assert g.device_identity == gpu_module.DeviceIdentity(uuid="GPU-abc123")
        assert g.device_identity.pci_bus_id is None

    def test_device_identity_is_bound_to_the_requested_index(self, monkeypatch):
        """Two `DeviceIdentity` values naming DIFFERENT physical cards must
        never compare equal even if some other signal (an index) matches -
        the whole reason this type exists."""
        _stub_working_driver(monkeypatch, uuid="GPU-gpu0")
        g0 = gpu_module.GpuMonitor(device_index=0)

        _stub_working_driver(monkeypatch, uuid="GPU-gpu1")
        g1 = gpu_module.GpuMonitor(device_index=1)

        assert g0.device_identity != g1.device_identity

    def test_pci_bus_id_does_not_affect_equality(self, monkeypatch):
        a = gpu_module.DeviceIdentity(uuid="GPU-abc123", pci_bus_id="00000000:01:00.0")
        b = gpu_module.DeviceIdentity(uuid="GPU-abc123", pci_bus_id="00000000:02:00.0")
        assert a == b
