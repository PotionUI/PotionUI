from pathlib import Path
from types import SimpleNamespace

import pynvml
import pytest

from src.platform.observability import system_probe
from src.platform.runtime import gpu as gpu_module
from src.platform.runtime import nvml_library

WINDIR = "C:\\Windows"
PROGRAM_FILES = "C:\\Program Files"


class _RecordingLogger:
    def __init__(self):
        self.infos = []

    def info(self, message):
        self.infos.append(message)


class _FakeDlls:
    def __init__(self, present):
        self.present = set(present)
        self.attempts = []

    def __call__(self, path: Path):
        self.attempts.append(path)
        if path not in self.present:
            raise OSError(f"cannot load {path}")
        return SimpleNamespace(path=path)


def _system32():
    return nvml_library.windows_nvml_candidates({"WINDIR": WINDIR, "ProgramFiles": PROGRAM_FILES})[0]


def _nvsmi():
    return nvml_library.windows_nvml_candidates({"WINDIR": WINDIR, "ProgramFiles": PROGRAM_FILES})[1]


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(nvml_library, "_running_on_windows", lambda: True)
    monkeypatch.setattr(pynvml, "nvmlLib", None)
    monkeypatch.setenv("WINDIR", WINDIR)
    monkeypatch.setenv("ProgramFiles", PROGRAM_FILES)
    log = _RecordingLogger()
    monkeypatch.setattr(nvml_library, "logger", log)
    return log


def _install_dlls(monkeypatch, present):
    dlls = _FakeDlls(present)
    monkeypatch.setattr(nvml_library, "_load_dll", dlls)
    return dlls


class TestCandidates:
    def test_system32_comes_before_the_nvsmi_folder(self):
        first, second = nvml_library.windows_nvml_candidates({"WINDIR": WINDIR, "ProgramFiles": PROGRAM_FILES})

        assert first == Path(WINDIR) / "System32" / "nvml.dll"
        assert second == Path(PROGRAM_FILES) / "NVIDIA Corporation" / "NVSMI" / "nvml.dll"

    def test_systemroot_stands_in_for_a_missing_windir(self):
        first, _ = nvml_library.windows_nvml_candidates({"SystemRoot": "D:\\Win"})

        assert first == Path("D:\\Win") / "System32" / "nvml.dll"


class TestEnsureNvmlLibrary:
    def test_loads_system32_first(self, monkeypatch, windows):
        dlls = _install_dlls(monkeypatch, {_system32(), _nvsmi()})

        loaded = nvml_library.ensure_nvml_library()

        assert loaded == _system32()
        assert dlls.attempts == [_system32()]
        assert pynvml.nvmlLib.path == _system32()

    def test_falls_back_to_the_nvsmi_folder(self, monkeypatch, windows):
        dlls = _install_dlls(monkeypatch, {_nvsmi()})

        loaded = nvml_library.ensure_nvml_library()

        assert loaded == _nvsmi()
        assert dlls.attempts == [_system32(), _nvsmi()]
        assert pynvml.nvmlLib.path == _nvsmi()

    def test_leaves_the_library_unloaded_when_neither_exists(self, monkeypatch, windows):
        _install_dlls(monkeypatch, set())

        assert nvml_library.ensure_nvml_library() is None
        assert pynvml.nvmlLib is None
        assert windows.infos == []

    def test_logs_the_loaded_path_once(self, monkeypatch, windows):
        dlls = _install_dlls(monkeypatch, {_system32()})

        nvml_library.ensure_nvml_library()
        nvml_library.ensure_nvml_library()

        assert dlls.attempts == [_system32()]
        assert len(windows.infos) == 1
        assert str(_system32()) in windows.infos[0]

    def test_keeps_a_library_pynvml_already_loaded(self, monkeypatch, windows):
        dlls = _install_dlls(monkeypatch, {_system32()})
        existing = object()
        monkeypatch.setattr(pynvml, "nvmlLib", existing)

        assert nvml_library.ensure_nvml_library() is None
        assert dlls.attempts == []
        assert pynvml.nvmlLib is existing

    def test_does_nothing_off_windows(self, monkeypatch):
        dlls = _install_dlls(monkeypatch, {_system32()})
        monkeypatch.setattr(nvml_library, "_running_on_windows", lambda: False)
        monkeypatch.setattr(pynvml, "nvmlLib", None)

        assert nvml_library.ensure_nvml_library() is None
        assert dlls.attempts == []
        assert pynvml.nvmlLib is None


@pytest.fixture
def monitors_on_fake_nvml(monkeypatch):
    libraries_at_init = []

    def nvml_init():
        libraries_at_init.append(getattr(pynvml.nvmlLib, "path", None))

    monkeypatch.setattr(gpu_module, "nvmlInit", nvml_init)
    monkeypatch.setattr(gpu_module, "nvmlDeviceGetHandleByIndex", lambda index: f"handle-{index}")
    monkeypatch.setattr(gpu_module, "nvmlDeviceGetUUID", lambda handle: "GPU-abc")
    monkeypatch.setattr(gpu_module, "nvmlDeviceGetPciInfo", lambda handle: SimpleNamespace(busId="0:1"))
    monkeypatch.setattr(gpu_module, "nvmlDeviceGetName", lambda handle: "Fake GPU")
    monkeypatch.setattr(
        gpu_module, "nvmlDeviceGetMemoryInfo",
        lambda handle: SimpleNamespace(total=24 << 30, free=17 << 30, used=7 << 30),
    )
    monkeypatch.setattr(system_probe, "nvmlInit", nvml_init)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetCount", lambda: 1)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetHandleByIndex", lambda index: f"handle-{index}")
    return libraries_at_init


class TestBothMonitorsShareTheLoader:
    @pytest.mark.parametrize("build", [gpu_module.GpuMonitor, system_probe.SystemMonitor], ids=["gpu", "system"])
    def test_each_monitor_loads_system32_before_initialising_nvml(self, monkeypatch, windows, monitors_on_fake_nvml, build):
        _install_dlls(monkeypatch, {_system32(), _nvsmi()})

        build()

        assert monitors_on_fake_nvml == [_system32()]

    def test_both_monitors_reuse_one_loaded_library(self, monkeypatch, windows, monitors_on_fake_nvml):
        dlls = _install_dlls(monkeypatch, {_system32(), _nvsmi()})

        gpu = gpu_module.GpuMonitor()
        monitor = system_probe.SystemMonitor()

        assert gpu.available is True
        assert monitor.gpu_available is True
        assert dlls.attempts == [_system32()]
        assert monitors_on_fake_nvml == [_system32(), _system32()]
        assert len(windows.infos) == 1
