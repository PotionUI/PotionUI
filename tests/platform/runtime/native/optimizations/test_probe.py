"""Tests for SystemProbe / probe_system(). Every OS/hardware call is monkeypatched."""

from __future__ import annotations

import subprocess
import sys

import pytest
import torch

import src.platform.runtime.native.optimizations.probe as probe_mod
from src.platform.runtime.native.optimizations.probe import probe_system


class _FakeVersionInfo:
    """Minimal stand-in for sys.version_info — probe.py only reads .major/.minor."""

    def __init__(self, major, minor):
        self.major = major
        self.minor = minor


def _patch_common(monkeypatch, *, cuda, capability=None, torch_cuda_version="12.4"):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)
    if capability is not None:
        monkeypatch.setattr(torch.cuda, "get_device_capability", lambda *a, **k: capability)
    monkeypatch.setattr(torch.version, "cuda", torch_cuda_version, raising=False)


class TestGpuInfo:
    def test_no_cuda_reports_all_none(self, monkeypatch):
        _patch_common(monkeypatch, cuda=False)
        cuda_available, cap, name, vram = probe_mod._gpu_info()
        assert cuda_available is False
        assert cap is None
        assert name is None
        assert vram is None

    def test_cuda_available_reports_capability(self, monkeypatch):
        # gpu_name comes from GpuManager/NVML when available (real hardware on
        # this box), falling back to torch.cuda.get_device_name() otherwise -
        # so this only pins down capability + presence of a non-empty name.
        _patch_common(monkeypatch, cuda=True, capability=(9, 0))
        monkeypatch.setattr(torch.cuda, "get_device_name", lambda *a, **k: "RTX 5090")
        cuda_available, cap, name, vram = probe_mod._gpu_info()
        assert cuda_available is True
        assert cap == (9, 0)
        assert name  # non-empty, either NVML or the torch fallback

    def test_cuda_available_falls_back_to_torch_name_without_nvml(self, monkeypatch):
        _patch_common(monkeypatch, cuda=True, capability=(9, 0))
        monkeypatch.setattr(torch.cuda, "get_device_name", lambda *a, **k: "RTX 5090 (torch fallback)")

        def _raise(*a, **k):
            raise RuntimeError("no NVML")

        monkeypatch.setattr(probe_mod, "GpuManager", None, raising=False)
        # Force the GpuManager import inside _gpu_info to fail.
        import builtins

        real_import = builtins.__import__

        def _blocked_import(name, *a, **k):
            if name == "src.platform.runtime.gpu":
                raise ImportError("blocked for test")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _blocked_import)
        cuda_available, cap, name, vram = probe_mod._gpu_info()
        assert name == "RTX 5090 (torch fallback)"
        assert vram is None

    def test_capability_probe_exception_is_swallowed(self, monkeypatch):
        _patch_common(monkeypatch, cuda=True)

        def _raise():
            raise RuntimeError("driver hiccup")

        monkeypatch.setattr(torch.cuda, "get_device_capability", lambda *a, **k: _raise())
        cuda_available, cap, name, vram = probe_mod._gpu_info()
        assert cuda_available is True
        assert cap is None


def _stub_result(output: str):
    class _Result:
        stdout = output
        stderr = ""

    return _Result()


class TestNvccInfo:
    """`_nvcc_info` now checks both system PATH and a pip-installed venv nvcc,
    returns a 4-tuple (found, version, matches_torch, source), and matching is
    major-version only (never a hardcoded CUDA series)."""

    def test_nvcc_not_found_anywhere(self, monkeypatch):
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: None)
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: None)
        found, version, matches, source = probe_mod._nvcc_info("12.4")
        assert (found, version, matches, source) == (False, None, False, None)

    def test_system_nvcc_found_and_matches_torch_cuda(self, monkeypatch):
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: "/usr/bin/nvcc")
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: None)
        monkeypatch.setattr(
            probe_mod.subprocess, "run",
            lambda *a, **k: _stub_result("release 12.4, V12.4.131\n"),
        )
        found, version, matches, source = probe_mod._nvcc_info("12.4")
        assert found is True
        assert version == (12, 4)
        assert matches is True
        assert source == "system"

    def test_system_nvcc_found_but_cuda_major_mismatches_torch(self, monkeypatch):
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: "/usr/bin/nvcc")
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: None)
        monkeypatch.setattr(
            probe_mod.subprocess, "run", lambda *a, **k: _stub_result("release 11.8, V11.8.89\n"),
        )
        found, version, matches, source = probe_mod._nvcc_info("12.4")
        assert found is True
        assert version == (11, 8)
        assert matches is False
        assert source == "system"

    def test_nvcc_subprocess_raises_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: "/usr/bin/nvcc")
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: None)

        def _raise(*a, **k):
            raise subprocess.TimeoutExpired(cmd="nvcc", timeout=5)

        monkeypatch.setattr(probe_mod.subprocess, "run", _raise)
        found, version, matches, source = probe_mod._nvcc_info("12.4")
        assert found is True
        assert version is None
        assert matches is False
        assert source == "system"

    def test_nvcc_output_without_release_string(self, monkeypatch):
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: "/usr/bin/nvcc")
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: None)
        monkeypatch.setattr(probe_mod.subprocess, "run", lambda *a, **k: _stub_result("garbage output\n"))
        found, version, matches, source = probe_mod._nvcc_info("12.4")
        assert found is True
        assert version is None
        assert matches is False
        assert source == "system"

    def test_venv_nvcc_used_when_no_system_nvcc(self, monkeypatch):
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: None)
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: probe_mod.Path("/venv/nvidia/cu13/bin/nvcc"))
        monkeypatch.setattr(probe_mod.subprocess, "run", lambda *a, **k: _stub_result("release 13.0, V13.0.48\n"))
        found, version, matches, source = probe_mod._nvcc_info("13.0")
        assert found is True
        assert version == (13, 0)
        assert matches is True
        assert source == "venv"

    def test_matching_source_preferred_over_non_matching_system_nvcc(self, monkeypatch):
        """System nvcc exists but is the wrong major; a matching venv nvcc
        must win even though system is checked first."""
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: "/usr/bin/nvcc")
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: probe_mod.Path("/venv/nvidia/cu13/bin/nvcc"))

        def _fake_run(args, **kwargs):
            path = args[0]
            if path == "/usr/bin/nvcc":
                return _stub_result("release 11.8, V11.8.89\n")
            return _stub_result("release 13.0, V13.0.48\n")

        monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run)
        found, version, matches, source = probe_mod._nvcc_info("13.0")
        assert found is True
        assert version == (13, 0)
        assert matches is True
        assert source == "venv"

    def test_neither_matches_prefers_system_and_reports_mismatch(self, monkeypatch):
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: "/usr/bin/nvcc")
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: probe_mod.Path("/venv/nvidia/cu13/bin/nvcc"))

        def _fake_run(args, **kwargs):
            path = args[0]
            if path == "/usr/bin/nvcc":
                return _stub_result("release 11.8, V11.8.89\n")
            return _stub_result("release 12.1, V12.1.66\n")

        monkeypatch.setattr(probe_mod.subprocess, "run", _fake_run)
        found, version, matches, source = probe_mod._nvcc_info("13.0")
        assert found is True
        assert version == (11, 8)
        assert matches is False
        assert source == "system"

    def test_matching_works_for_multiple_cuda_majors(self, monkeypatch):
        """Major-version matching must generalize past any one hardcoded series."""
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: "/usr/bin/nvcc")
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: None)

        for torch_cuda, nvcc_release, expect_match in [
            ("11.8", "release 11.8, V11.8.89\n", True),
            ("12.4", "release 12.6, V12.6.20\n", True),  # same major, different minor
            ("13.0", "release 13.2, V13.2.51\n", True),
            ("14.0", "release 14.0, V14.0.1\n", True),
            ("12.4", "release 13.0, V13.0.48\n", False),
        ]:
            monkeypatch.setattr(probe_mod.subprocess, "run", lambda *a, r=nvcc_release, **k: _stub_result(r))
            found, version, matches, source = probe_mod._nvcc_info(torch_cuda)
            assert matches is expect_match, (torch_cuda, nvcc_release)


class TestFindVenvNvcc:
    """Real layout verified 2026-07-11: `nvidia-cuda-nvcc` (unsuffixed, CUDA
    13.x+) installs a working compiler under `nvidia/cu{major}/bin/nvcc`. The
    lookup module name is built from the major torch reports, never a literal
    "13"."""

    def test_no_major_returns_none_without_touching_importlib(self, monkeypatch):
        def _boom(name):
            raise AssertionError("should not probe importlib when major is unknown")

        monkeypatch.setattr(probe_mod.importlib.util, "find_spec", _boom)
        assert probe_mod._find_venv_nvcc(None) is None

    def test_no_nvidia_package_installed(self, monkeypatch):
        def _raise(name):
            raise ModuleNotFoundError("No module named 'nvidia'")

        monkeypatch.setattr(probe_mod.importlib.util, "find_spec", _raise)
        assert probe_mod._find_venv_nvcc(13) is None

    def test_package_installed_but_no_nvcc_binary(self, monkeypatch, tmp_path):
        """CUDA <=12 series reality: the -cuXX-suffixed wheel ships ptxas/libNVVM, not nvcc."""
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "ptxas").write_text("")

        class _Spec:
            submodule_search_locations = [str(tmp_path)]

        monkeypatch.setattr(probe_mod.importlib.util, "find_spec", lambda name: _Spec())
        assert probe_mod._find_venv_nvcc(12) is None

    def test_package_with_nvcc_binary_is_found(self, monkeypatch, tmp_path):
        """CUDA 13.x+ reality: nvidia-cuda-nvcc ships a real nvcc under nvidia/cu{major}/bin/."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "nvcc").write_text("")

        class _Spec:
            submodule_search_locations = [str(tmp_path)]

        looked_up = {}

        def _find_spec(name):
            looked_up["name"] = name
            return _Spec()

        monkeypatch.setattr(probe_mod.importlib.util, "find_spec", _find_spec)
        found = probe_mod._find_venv_nvcc(13)
        assert found == bin_dir / "nvcc"
        assert looked_up["name"] == "nvidia.cu13"

    def test_module_name_derived_from_major_not_hardcoded(self, monkeypatch, tmp_path):
        """Any major must resolve to its own `nvidia.cu{major}` module name -
        proves there's no literal "13" baked into the lookup."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "nvcc").write_text("")

        class _Spec:
            submodule_search_locations = [str(tmp_path)]

        for major in (13, 14, 20):
            looked_up = {}

            def _find_spec(name, _looked_up=looked_up):
                _looked_up["name"] = name
                return _Spec()

            monkeypatch.setattr(probe_mod.importlib.util, "find_spec", _find_spec)
            assert probe_mod._find_venv_nvcc(major) == bin_dir / "nvcc"
            assert looked_up["name"] == f"nvidia.cu{major}"

    def test_spec_with_no_search_locations_returns_none(self, monkeypatch):
        class _Spec:
            submodule_search_locations = None

        monkeypatch.setattr(probe_mod.importlib.util, "find_spec", lambda name: _Spec())
        assert probe_mod._find_venv_nvcc(13) is None

    def test_spec_none_returns_none(self, monkeypatch):
        monkeypatch.setattr(probe_mod.importlib.util, "find_spec", lambda name: None)
        assert probe_mod._find_venv_nvcc(13) is None


class TestPackageVersion:
    def test_installed_package_returns_version(self, monkeypatch):
        monkeypatch.setattr(probe_mod, "_pkg_version", lambda name: "2.1.0" if name == "triton" else None)
        assert probe_mod._package_version("triton") == "2.1.0"

    def test_missing_package_returns_none(self, monkeypatch):
        def _raise(name):
            raise probe_mod.PackageNotFoundError(name)

        monkeypatch.setattr(probe_mod, "_pkg_version", _raise)
        assert probe_mod._package_version("sageattention") is None


class TestPythonHFound:
    def test_found(self, monkeypatch, tmp_path):
        (tmp_path / "Python.h").write_text("")
        monkeypatch.setattr(
            probe_mod.sysconfig, "get_paths", lambda: {"include": str(tmp_path)}
        )
        assert probe_mod._python_h_found() is True

    def test_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            probe_mod.sysconfig, "get_paths", lambda: {"include": str(tmp_path)}
        )
        assert probe_mod._python_h_found() is False

    def test_no_include_path_reported(self, monkeypatch):
        monkeypatch.setattr(probe_mod.sysconfig, "get_paths", lambda: {})
        assert probe_mod._python_h_found() is False


class TestProbeSystem:
    def test_full_probe_assembles_all_fields(self, monkeypatch):
        _patch_common(monkeypatch, cuda=True, capability=(9, 0), torch_cuda_version="12.4")
        monkeypatch.setattr(probe_mod.sys, "version_info", _FakeVersionInfo(3, 12))
        monkeypatch.setattr(torch.cuda, "get_device_name", lambda *a, **k: "RTX 5090")
        monkeypatch.setattr(probe_mod, "_gpu_info", lambda: (True, (9, 0), "RTX 5090", 32.0))
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: "/usr/bin/" + name if name in ("nvcc", "gcc") else None)
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: None)

        class _Result:
            stdout = "release 12.4, V12.4.131\n"
            stderr = ""

        monkeypatch.setattr(probe_mod.subprocess, "run", lambda *a, **k: _Result())
        monkeypatch.setattr(probe_mod, "_python_h_found", lambda: True)

        versions = {"sageattention": "2.1.0", "sageattn3": "1.0.0", "spas_sage_attn": "0.1.0", "triton": "3.0.0"}
        monkeypatch.setattr(probe_mod, "_package_version", lambda name: versions.get(name))

        import src.platform.runtime.native.attention as attention

        monkeypatch.setattr(attention, "get_attention_backend", lambda: "sage2")
        monkeypatch.setattr(attention, "available_backends", lambda: ["sage2", "sage", "sdpa"])

        report = probe_system()

        assert report.cuda_available is True
        assert report.python_version == (3, 12)
        assert report.compute_capability == (9, 0)
        assert report.gpu_name == "RTX 5090"
        assert report.gpu_vram_gb == 32.0
        assert report.nvcc_found is True
        assert report.nvcc_version == (12, 4)
        assert report.nvcc_cuda_matches_torch is True
        assert report.nvcc_source == "system"
        assert report.gcc_found is True
        assert report.python_h_found is True
        assert report.sageattention_version == "2.1.0"
        assert report.sageattn3_version == "1.0.0"
        assert report.sparge_version == "0.1.0"
        assert report.triton_version == "3.0.0"
        assert report.flash_attn_version is None
        assert report.active_backend == "sage2"
        assert report.available_backends == ["sage2", "sage", "sdpa"]

    def test_probe_without_gpu_or_toolchain(self, monkeypatch):
        _patch_common(monkeypatch, cuda=False)
        monkeypatch.setattr(probe_mod, "_gpu_info", lambda: (False, None, None, None))
        monkeypatch.setattr(probe_mod.shutil, "which", lambda name: None)
        monkeypatch.setattr(probe_mod, "_find_venv_nvcc", lambda major: None)
        monkeypatch.setattr(probe_mod, "_package_version", lambda name: None)

        import src.platform.runtime.native.attention as attention

        monkeypatch.setattr(attention, "get_attention_backend", lambda: "sdpa")
        monkeypatch.setattr(attention, "available_backends", lambda: ["sdpa"])

        report = probe_system()

        assert report.cuda_available is False
        assert report.compute_capability is None
        assert report.nvcc_found is False
        assert report.nvcc_source is None
        assert report.sageattention_version is None
        assert report.sageattn3_version is None
        assert report.sparge_version is None
        assert report.active_backend == "sdpa"
        assert report.available_backends == ["sdpa"]
