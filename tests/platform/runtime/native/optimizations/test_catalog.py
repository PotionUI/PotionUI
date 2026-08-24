"""Tests for the optimization catalog: requirement truth tables and install specs."""

from __future__ import annotations

import pytest

from src.platform.runtime.native.optimizations.catalog import (
    CATALOG,
    CudaToolchain,
    FlashAttn,
    SageAttention2,
    SageAttention3,
    SpargeAttention,
    get_optimization,
)
from src.platform.runtime.native.optimizations.probe import SystemProbe


def _probe(**overrides) -> SystemProbe:
    base = SystemProbe()
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# A torch-matching nvcc (any CUDA major works - the check is major-only, never
# hardcoded); used wherever a test wants the "nvcc" requirement satisfied.
_MATCHING_NVCC = dict(nvcc_found=True, nvcc_version=(12, 4), nvcc_cuda_matches_torch=True, torch_cuda_version="12.4")


class TestCatalogLookup:
    def test_get_optimization_known_ids(self):
        assert get_optimization("sageattention2") is not None
        assert get_optimization("sageattention3") is not None
        assert get_optimization("sparge-attention") is not None
        assert get_optimization("flash-attn") is not None

    def test_get_optimization_unknown_returns_none(self):
        assert get_optimization("nonexistent") is None

    def test_catalog_ids_match_declared_ids(self):
        for key, opt in CATALOG.items():
            assert key == opt.id


class TestSageAttention2Requirements:
    def test_all_requirements_met_on_ampere_with_toolchain(self):
        opt = SageAttention2()
        probe = _probe(
            cuda_available=True, compute_capability=(8, 0), triton_version="3.0.0", **_MATCHING_NVCC,
        )
        reqs = opt.requirements(probe)
        assert all(r.met for r in reqs)
        status = opt.status(probe)
        assert status.installable is True

    def test_nvcc_found_but_wrong_major_points_at_cuda_toolchain_when_pip_supported(self):
        """The real motivating bug: nvcc is present, just the wrong CUDA
        major for torch - this must NOT read as met. torch cuda13.x is a
        series pip CAN provide an nvcc for, so the fix points at the
        one-click optimization."""
        opt = SageAttention2()
        probe = _probe(
            cuda_available=True, compute_capability=(9, 0), triton_version="3.0.0",
            nvcc_found=True, nvcc_version=(11, 8), nvcc_cuda_matches_torch=False,
            torch_cuda_version="13.0",
        )
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["nvcc"].met is False
        assert "11.8" in reqs["nvcc"].detail
        assert "13.0" in reqs["nvcc"].detail
        assert "CUDA Toolchain" in reqs["nvcc"].detail
        assert opt.status(probe).installable is False

    def test_nvcc_found_but_wrong_major_points_at_system_toolkit_when_pip_unsupported(self):
        """torch cuda12.x is a series pip CANNOT provide a working nvcc for
        (verified 2026-07-11) - the fix must be the honest system-toolkit
        hint, not a fake one-click promise."""
        opt = SageAttention2()
        probe = _probe(
            cuda_available=True, compute_capability=(9, 0), triton_version="3.0.0",
            nvcc_found=True, nvcc_version=(11, 8), nvcc_cuda_matches_torch=False,
            torch_cuda_version="12.4",
        )
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["nvcc"].met is False
        assert "CUDA Toolchain" not in reqs["nvcc"].detail
        assert "cuda-toolkit-12-4" in reqs["nvcc"].detail
        assert opt.status(probe).installable is False

    def test_capability_below_8_0_is_unmet(self):
        opt = SageAttention2()
        probe = _probe(
            cuda_available=True, compute_capability=(7, 5), nvcc_found=True,
            triton_version="3.0.0",
        )
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["compute_capability"].met is False
        assert opt.status(probe).installable is False

    def test_capability_exactly_8_0_is_met(self):
        opt = SageAttention2()
        probe = _probe(
            cuda_available=True, compute_capability=(8, 0), nvcc_found=True,
            triton_version="3.0.0",
        )
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["compute_capability"].met is True

    def test_capability_12_0_is_met(self):
        opt = SageAttention2()
        probe = _probe(
            cuda_available=True, compute_capability=(12, 0), nvcc_found=True,
            triton_version="3.0.0",
        )
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["compute_capability"].met is True

    def test_missing_nvcc_is_unmet_with_actionable_detail(self):
        opt = SageAttention2()
        probe = _probe(
            cuda_available=True, compute_capability=(9, 0), nvcc_found=False,
            triton_version="3.0.0", torch_cuda_version="12.4",
        )
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["nvcc"].met is False
        assert "12.4" in reqs["nvcc"].detail

    def test_missing_triton_is_unmet_and_does_not_auto_install_a_second_package(self):
        opt = SageAttention2()
        probe = _probe(
            cuda_available=True, compute_capability=(9, 0), nvcc_found=True,
            triton_version=None,
        )
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["triton"].met is False
        assert opt.status(probe).installable is False

    def test_no_cuda_is_unmet(self):
        opt = SageAttention2()
        probe = _probe(cuda_available=False, compute_capability=None)
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["cuda_available"].met is False

    def test_already_installed_is_not_installable_again(self):
        opt = SageAttention2()
        probe = _probe(
            cuda_available=True, compute_capability=(9, 0), nvcc_found=True,
            triton_version="3.0.0", sageattention_version="2.1.0",
        )
        status = opt.status(probe)
        assert status.installed is True
        assert status.installed_version == "2.1.0"
        assert status.installable is False

    def test_active_when_probe_backend_is_sage2_or_sage(self):
        opt = SageAttention2()
        probe = _probe(sageattention_version="2.1.0", active_backend="sage2")
        assert opt.status(probe).active is True

        probe.active_backend = "sage"
        assert opt.status(probe).active is True

        probe.active_backend = "sdpa"
        assert opt.status(probe).active is False

    def test_not_active_if_not_installed_even_if_backend_matches(self):
        opt = SageAttention2()
        probe = _probe(sageattention_version=None, active_backend="sage2")
        assert opt.status(probe).active is False


class TestSageAttention2InstallSpec:
    def test_install_spec_binds_arch_list_to_gpu_capability(self):
        opt = SageAttention2()
        probe = _probe(compute_capability=(9, 0))
        spec = opt.install_spec(probe)
        assert spec.env["TORCH_CUDA_ARCH_LIST"] == "9.0"
        assert any("SageAttention" in arg for arg in spec.pip_args)

    def test_install_spec_disables_build_isolation(self):
        # setup.py imports torch at build time; the isolated build env has no
        # torch, so the build must run against the venv itself.
        opt = SageAttention2()
        spec = opt.install_spec(_probe(compute_capability=(12, 0)))
        assert "--no-build-isolation" in spec.pip_args

    def test_install_spec_defaults_arch_when_capability_unknown(self):
        opt = SageAttention2()
        probe = _probe(compute_capability=None)
        spec = opt.install_spec(probe)
        assert spec.env["TORCH_CUDA_ARCH_LIST"] == "8.0"

    def test_install_spec_caps_max_jobs_at_8(self, monkeypatch):
        opt = SageAttention2()
        probe = _probe(compute_capability=(8, 0))
        monkeypatch.setattr("os.cpu_count", lambda: 64)
        spec = opt.install_spec(probe)
        assert spec.env["MAX_JOBS"] == "8"

    def test_install_spec_max_jobs_floor_is_1(self, monkeypatch):
        opt = SageAttention2()
        probe = _probe(compute_capability=(8, 0))
        monkeypatch.setattr("os.cpu_count", lambda: 1)
        spec = opt.install_spec(probe)
        assert spec.env["MAX_JOBS"] == "1"


class TestSageAttention3Requirements:
    """Mirrors TestSageAttention2Requirements' shape, per the task's ask that
    the catalog entry validate the same way — but the capability check here is
    EXACT membership in {(10,0),(12,0),(12,1)}, not a `>=` floor, and there's
    an additional CUDA-runtime-version gate SageAttention2 doesn't have."""

    _MATCHING = dict(
        cuda_available=True, compute_capability=(12, 0), torch_cuda_version="12.8", python_version=(3, 13),
        **{k: v for k, v in _MATCHING_NVCC.items() if k != "torch_cuda_version"},
    )

    def test_all_requirements_met_on_5090_with_toolchain(self):
        opt = SageAttention3()
        probe = _probe(**self._MATCHING)
        reqs = opt.requirements(probe)
        assert all(r.met for r in reqs)
        assert opt.status(probe).installable is True

    @pytest.mark.parametrize("cap", [(10, 0), (12, 0), (12, 1)])
    def test_every_supported_blackwell_capability_is_met(self, cap):
        opt = SageAttention3()
        probe = _probe(cuda_available=True, compute_capability=cap, torch_cuda_version="12.8")
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["compute_capability"].met is True

    @pytest.mark.parametrize("cap", [(9, 0), (11, 0), (12, 2), (13, 0), (8, 0)])
    def test_non_blackwell_or_mismatched_capability_is_unmet(self, cap):
        # exact-membership, not `>=` — 12.0+ capabilities like (12,2)/(13,0)
        # must NOT read as met even though they're numerically above sm120.
        opt = SageAttention3()
        probe = _probe(cuda_available=True, compute_capability=cap, torch_cuda_version="12.8")
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["compute_capability"].met is False
        assert opt.status(probe).installable is False

    def test_cuda_runtime_below_12_8_is_unmet(self):
        opt = SageAttention3()
        probe = _probe(cuda_available=True, compute_capability=(12, 0), torch_cuda_version="12.6")
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["cuda_runtime"].met is False
        assert opt.status(probe).installable is False

    def test_cuda_runtime_exactly_12_8_is_met(self):
        opt = SageAttention3()
        probe = _probe(cuda_available=True, compute_capability=(12, 0), torch_cuda_version="12.8")
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["cuda_runtime"].met is True

    def test_no_cuda_is_unmet(self):
        opt = SageAttention3()
        probe = _probe(cuda_available=False, compute_capability=None)
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["cuda_available"].met is False

    def test_missing_nvcc_is_unmet(self):
        opt = SageAttention3()
        probe = _probe(
            cuda_available=True, compute_capability=(12, 0), torch_cuda_version="12.8", nvcc_found=False,
        )
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["nvcc"].met is False

    def test_python_below_3_13_is_unmet_with_actionable_detail(self):
        # The real motivating bug: this project's venv is Python 3.12, and
        # setup.py's own `python_requires>=3.8` would let pip proceed anyway —
        # the README's documented `python>=3.13` floor must still block here.
        opt = SageAttention3()
        probe = _probe(**{**self._MATCHING, "python_version": (3, 12)})
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["python_version"].met is False
        assert "3.12" in reqs["python_version"].detail
        assert "3.13" in reqs["python_version"].detail
        assert opt.status(probe).installable is False

    def test_python_exactly_3_13_is_met(self):
        opt = SageAttention3()
        probe = _probe(**{**self._MATCHING, "python_version": (3, 13)})
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["python_version"].met is True

    def test_python_above_3_13_is_met(self):
        opt = SageAttention3()
        probe = _probe(**{**self._MATCHING, "python_version": (3, 14)})
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["python_version"].met is True

    def test_already_installed_is_not_installable_again(self):
        opt = SageAttention3()
        probe = _probe(**self._MATCHING, sageattn3_version="1.0.0")
        status = opt.status(probe)
        assert status.installed is True
        assert status.installed_version == "1.0.0"
        assert status.installable is False

    def test_active_when_probe_backend_is_sage3(self):
        opt = SageAttention3()
        probe = _probe(sageattn3_version="1.0.0", active_backend="sage3")
        assert opt.status(probe).active is True
        probe.active_backend = "sage2"
        assert opt.status(probe).active is False

    def test_not_active_if_not_installed_even_if_backend_matches(self):
        opt = SageAttention3()
        probe = _probe(sageattn3_version=None, active_backend="sage3")
        assert opt.status(probe).active is False


class TestSageAttention3InstallSpec:
    def test_install_spec_binds_arch_list_to_gpu_capability(self):
        opt = SageAttention3()
        probe = _probe(compute_capability=(12, 0))
        spec = opt.install_spec(probe)
        assert spec.env["TORCH_CUDA_ARCH_LIST"] == "12.0"

    def test_install_spec_targets_the_blackwell_subdirectory(self):
        opt = SageAttention3()
        spec = opt.install_spec(_probe(compute_capability=(12, 0)))
        assert any(
            "SageAttention" in arg and "subdirectory=sageattention3_blackwell" in arg
            for arg in spec.pip_args
        )
        assert "--no-build-isolation" in spec.pip_args

    def test_install_spec_defaults_arch_when_capability_unknown(self):
        opt = SageAttention3()
        spec = opt.install_spec(_probe(compute_capability=None))
        assert spec.env["TORCH_CUDA_ARCH_LIST"] == "12.0"


class TestSpargeAttentionRequirements:
    """Mirrors TestSageAttention3Requirements' shape. Capability gate is
    major-only membership in {8, 9} (Ampere/Ada/Hopper) — no exact-tuple or
    CUDA-runtime gate the way sage3 has (SpargeAttn compiles per-local-GPU)."""

    def test_all_requirements_met_on_ampere_with_toolchain(self):
        opt = SpargeAttention()
        probe = _probe(cuda_available=True, compute_capability=(8, 0), **_MATCHING_NVCC)
        reqs = opt.requirements(probe)
        assert all(r.met for r in reqs)
        assert opt.status(probe).installable is True

    @pytest.mark.parametrize("cap", [(8, 0), (8, 6), (8, 7), (8, 9), (9, 0)])
    def test_every_supported_capability_is_met(self, cap):
        opt = SpargeAttention()
        probe = _probe(cuda_available=True, compute_capability=cap)
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["compute_capability"].met is True

    @pytest.mark.parametrize("cap", [(7, 0), (7, 5), (10, 0), (12, 0), (12, 1)])
    def test_non_ampere_ada_hopper_capability_is_unmet(self, cap):
        # Notably includes Blackwell (10.0/12.0/12.1, sage3's own supported
        # set) — SpargeAttn's setup.py does not build for it as of this writing.
        opt = SpargeAttention()
        probe = _probe(cuda_available=True, compute_capability=cap)
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["compute_capability"].met is False
        assert opt.status(probe).installable is False

    def test_no_cuda_is_unmet(self):
        opt = SpargeAttention()
        probe = _probe(cuda_available=False, compute_capability=None)
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["cuda_available"].met is False

    def test_missing_nvcc_is_unmet(self):
        opt = SpargeAttention()
        probe = _probe(cuda_available=True, compute_capability=(8, 0), nvcc_found=False)
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["nvcc"].met is False

    def test_already_installed_is_not_installable_again(self):
        opt = SpargeAttention()
        probe = _probe(**{"cuda_available": True, "compute_capability": (8, 0), **_MATCHING_NVCC},
                        sparge_version="1.0.0")
        status = opt.status(probe)
        assert status.installed is True
        assert status.installed_version == "1.0.0"
        assert status.installable is False

    def test_active_when_probe_backend_is_sparge(self):
        opt = SpargeAttention()
        probe = _probe(sparge_version="1.0.0", active_backend="sparge")
        assert opt.status(probe).active is True
        probe.active_backend = "sage2"
        assert opt.status(probe).active is False

    def test_not_active_if_not_installed_even_if_backend_matches(self):
        opt = SpargeAttention()
        probe = _probe(sparge_version=None, active_backend="sparge")
        assert opt.status(probe).active is False


class TestSpargeAttentionInstallSpec:
    def test_install_spec_binds_arch_list_to_gpu_capability(self):
        opt = SpargeAttention()
        probe = _probe(compute_capability=(8, 6))
        spec = opt.install_spec(probe)
        assert spec.env["TORCH_CUDA_ARCH_LIST"] == "8.6"

    def test_install_spec_targets_the_spargeattn_repo(self):
        opt = SpargeAttention()
        spec = opt.install_spec(_probe(compute_capability=(8, 6)))
        assert any("SpargeAttn" in arg for arg in spec.pip_args)
        assert "--no-build-isolation" in spec.pip_args

    def test_install_spec_defaults_arch_when_capability_unknown(self):
        opt = SpargeAttention()
        spec = opt.install_spec(_probe(compute_capability=None))
        assert spec.env["TORCH_CUDA_ARCH_LIST"] == "8.0"


class TestFlashAttnRequirements:
    def test_all_requirements_met(self):
        opt = FlashAttn()
        probe = _probe(cuda_available=True, compute_capability=(8, 0), **_MATCHING_NVCC)
        assert opt.status(probe).installable is True

    def test_nvcc_found_but_wrong_major_is_unmet(self):
        opt = FlashAttn()
        probe = _probe(
            cuda_available=True, compute_capability=(8, 0),
            nvcc_found=True, nvcc_version=(11, 8), nvcc_cuda_matches_torch=False,
            torch_cuda_version="13.0",
        )
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["nvcc"].met is False
        assert opt.status(probe).installable is False

    def test_low_capability_unmet(self):
        opt = FlashAttn()
        probe = _probe(cuda_available=True, compute_capability=(7, 0), nvcc_found=True)
        assert opt.status(probe).installable is False

    def test_install_spec_uses_no_build_isolation(self):
        opt = FlashAttn()
        spec = opt.install_spec(_probe())
        assert spec.pip_args == ["flash-attn", "--no-build-isolation"]

    def test_active_when_backend_is_flash(self):
        opt = FlashAttn()
        probe = _probe(flash_attn_version="2.5.0", active_backend="flash")
        assert opt.status(probe).active is True
        probe.active_backend = "sdpa"
        assert opt.status(probe).active is False


class TestActivate:
    def test_sage_activate_resets_cache_and_returns_active_backend(self, monkeypatch):
        import src.platform.runtime.native.attention as attention

        called = {"reset": False}
        monkeypatch.setattr(attention, "reset_backend_cache", lambda: called.__setitem__("reset", True))
        monkeypatch.setattr(attention, "get_attention_backend", lambda: "sage2")

        result = SageAttention2().activate()
        assert called["reset"] is True
        assert result == {"active_backend": "sage2"}

    def test_sage3_activate_resets_cache_and_returns_active_backend(self, monkeypatch):
        import src.platform.runtime.native.attention as attention

        called = {"reset": False}
        monkeypatch.setattr(attention, "reset_backend_cache", lambda: called.__setitem__("reset", True))
        monkeypatch.setattr(attention, "get_attention_backend", lambda: "sage3")

        result = SageAttention3().activate()
        assert called["reset"] is True
        assert result == {"active_backend": "sage3"}

    def test_sparge_activate_resets_cache_and_returns_active_backend(self, monkeypatch):
        import src.platform.runtime.native.attention as attention

        called = {"reset": False}
        monkeypatch.setattr(attention, "reset_backend_cache", lambda: called.__setitem__("reset", True))
        monkeypatch.setattr(attention, "get_attention_backend", lambda: "sparge")

        result = SpargeAttention().activate()
        assert called["reset"] is True
        assert result == {"active_backend": "sparge"}

    def test_flash_activate_resets_cache_and_returns_active_backend(self, monkeypatch):
        import src.platform.runtime.native.attention as attention

        called = {"reset": False}
        monkeypatch.setattr(attention, "reset_backend_cache", lambda: called.__setitem__("reset", True))
        monkeypatch.setattr(attention, "get_attention_backend", lambda: "flash")

        result = FlashAttn().activate()
        assert called["reset"] is True
        assert result == {"active_backend": "flash"}


class TestCudaToolchainRequirements:
    """No literal CUDA major anywhere here or in the code under test except
    MIN_CUDA_MAJOR_WITH_PIP_NVCC itself (the verified capability threshold) -
    every case is parameterized so it exercises >= 2 distinct majors on each
    side of that threshold, plus the unknown-version case."""

    def test_catalog_registers_cuda_toolchain(self):
        assert get_optimization("cuda_toolchain") is not None
        assert CATALOG["cuda_toolchain"].id == "cuda_toolchain"

    @pytest.mark.parametrize("torch_cuda_version", ["13.0", "14.2", "20.1"])
    def test_installable_for_series_pip_can_provide_nvcc_for(self, torch_cuda_version):
        opt = CudaToolchain()
        probe = _probe(cuda_available=True, torch_cuda_version=torch_cuda_version)
        assert opt.status(probe).installable is True

    @pytest.mark.parametrize("torch_cuda_version", ["11.8", "12.4", "12.9"])
    def test_not_installable_for_series_pip_lacks_a_real_nvcc_for(self, torch_cuda_version):
        """Verified 2026-07-11: CUDA <=12 series' pip wheels don't ship a
        real nvcc - so this must be unmet with an honest, non-empty,
        actionable (system-toolkit) detail, not silently installable."""
        opt = CudaToolchain()
        probe = _probe(cuda_available=True, torch_cuda_version=torch_cuda_version)
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["pip_nvcc_available_for_series"].met is False
        assert reqs["pip_nvcc_available_for_series"].detail  # actionable, non-empty
        assert "apt install cuda-toolkit" in reqs["pip_nvcc_available_for_series"].detail
        assert opt.status(probe).installable is False

    def test_boundary_major_is_installable(self):
        """The gate is `>= MIN_CUDA_MAJOR_WITH_PIP_NVCC`, not `> `."""
        from src.platform.runtime.native.optimizations.catalog import MIN_CUDA_MAJOR_WITH_PIP_NVCC

        opt = CudaToolchain()
        probe = _probe(cuda_available=True, torch_cuda_version=f"{MIN_CUDA_MAJOR_WITH_PIP_NVCC}.0")
        assert opt.status(probe).installable is True

    def test_unknown_torch_cuda_version_is_unmet_and_not_installable(self):
        opt = CudaToolchain()
        probe = _probe(cuda_available=True, torch_cuda_version=None)
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["torch_cuda_version_known"].met is False
        assert reqs["torch_cuda_version_known"].detail  # actionable, non-empty
        # The series-support requirement shouldn't duplicate the same message.
        assert reqs["pip_nvcc_available_for_series"].met is False
        assert reqs["pip_nvcc_available_for_series"].detail == ""
        assert opt.status(probe).installable is False

    def test_no_cuda_available_is_unmet(self):
        opt = CudaToolchain()
        probe = _probe(cuda_available=False, torch_cuda_version="13.0")
        reqs = {r.id: r for r in opt.requirements(probe)}
        assert reqs["cuda_available"].met is False
        assert opt.status(probe).installable is False

    @pytest.mark.parametrize(
        "torch_cuda_version,nvcc_version,expect_installed",
        [
            ("13.0", (13, 0), True),
            ("13.0", (13, 9), True),  # same major, different minor - still a match
            ("14.0", (14, 2), True),
            ("13.0", (14, 0), False),  # wrong major
            ("13.0", None, False),  # nvcc_found True but no parsed version
            ("12.4", (12, 4), True),  # installed_version tracks reality regardless of the pip-support gate
        ],
    )
    def test_installed_version_tracks_matching_nvcc(self, torch_cuda_version, nvcc_version, expect_installed):
        opt = CudaToolchain()
        matches = nvcc_version is not None and nvcc_version[0] == int(torch_cuda_version.split(".")[0])
        probe = _probe(
            cuda_available=True, torch_cuda_version=torch_cuda_version,
            nvcc_found=nvcc_version is not None, nvcc_version=nvcc_version,
            nvcc_cuda_matches_torch=matches,
        )
        version = opt.installed_version(probe)
        assert (version is not None) is expect_installed
        if expect_installed:
            assert version == f"{nvcc_version[0]}.{nvcc_version[1]}"

    def test_not_installed_when_nvcc_missing_entirely(self):
        opt = CudaToolchain()
        probe = _probe(cuda_available=True, torch_cuda_version="13.0", nvcc_found=False)
        assert opt.installed_version(probe) is None
        assert opt.status(probe).installed is False

    def test_active_mirrors_installed_not_an_attention_backend(self):
        opt = CudaToolchain()
        probe = _probe(cuda_available=True, **_MATCHING_NVCC)
        status = opt.status(probe)
        assert status.installed is True
        assert status.active is True

        probe.nvcc_cuda_matches_torch = False
        status = opt.status(probe)
        assert status.installed is False
        assert status.active is False


class TestCudaToolchainInstallSpec:
    """The package name (and the >= gate) is derived purely from
    torch_cuda_version - no branch that special-cases one major over another."""

    @pytest.mark.parametrize("major", [13, 14, 22])
    def test_pip_args_and_fallback_derived_from_torch_major_only(self, major):
        opt = CudaToolchain()
        probe = _probe(torch_cuda_version=f"{major}.4")
        spec = opt.install_spec(probe)

        assert spec.pip_args == [f"nvidia-cuda-nvcc=={major}.*", f"nvidia-cuda-cccl=={major}.*"]
        assert spec.fallback_pip_args == [f"nvidia-cuda-nvcc=={major}.*"]

    @pytest.mark.parametrize("torch_cuda_version", ["11.8", "12.4", "12.9"])
    def test_install_spec_raises_for_series_below_the_pip_nvcc_threshold(self, torch_cuda_version):
        opt = CudaToolchain()
        probe = _probe(torch_cuda_version=torch_cuda_version)
        with pytest.raises(ValueError):
            opt.install_spec(probe)

    def test_install_spec_raises_without_known_torch_cuda_version(self):
        opt = CudaToolchain()
        probe = _probe(torch_cuda_version=None)
        with pytest.raises(ValueError):
            opt.install_spec(probe)


class TestVenvCudaBuildEnv:
    """Sage2/flash-attn's build env picks up CUDA_HOME/PATH/CPATH/LIBRARY_PATH
    only when the torch-matching nvcc actually lives in this venv."""

    def test_noop_when_nvcc_source_is_system(self):
        opt = SageAttention2()
        probe = _probe(compute_capability=(9, 0), nvcc_source="system", **_MATCHING_NVCC)
        spec = opt.install_spec(probe)
        assert "CUDA_HOME" not in spec.env

    def test_noop_when_no_matching_nvcc_at_all(self):
        opt = SageAttention2()
        probe = _probe(compute_capability=(9, 0), nvcc_source="venv", nvcc_found=False)
        spec = opt.install_spec(probe)
        assert "CUDA_HOME" not in spec.env

    def test_env_populated_when_matching_nvcc_lives_in_venv(self, monkeypatch, tmp_path):
        """Real layout verified 2026-07-11: nvidia-cuda-nvcc and its
        dependencies (nvvm/runtime/crt) all land under one shared
        `nvidia/cu{major}/` prefix - bin/, include/, and lib/ together."""
        cuda_home = tmp_path / "nvidia" / "cu13"
        (cuda_home / "bin").mkdir(parents=True)
        (cuda_home / "include").mkdir()
        (cuda_home / "lib").mkdir()

        class _Spec:
            submodule_search_locations = [str(cuda_home)]

        looked_up = {}

        def _fake_find_spec(name):
            looked_up["name"] = name
            return _Spec()

        import importlib.util as importlib_util

        monkeypatch.setattr(importlib_util, "find_spec", _fake_find_spec)

        opt = SageAttention2()
        probe = _probe(
            compute_capability=(9, 0), nvcc_source="venv",
            nvcc_found=True, nvcc_version=(13, 0), nvcc_cuda_matches_torch=True, torch_cuda_version="13.0",
        )
        spec = opt.install_spec(probe)

        assert looked_up["name"] == "nvidia.cu13"
        assert spec.env["CUDA_HOME"] == str(cuda_home)
        assert str(cuda_home / "bin") in spec.env["PATH"].split(__import__("os").pathsep)
        assert str(cuda_home / "include") in spec.env["CPATH"]
        assert str(cuda_home / "lib") in spec.env["LIBRARY_PATH"]

    def test_env_omits_cpath_library_path_when_dirs_dont_exist(self, monkeypatch, tmp_path):
        """CPATH/LIBRARY_PATH are only added if the include/lib dirs actually
        exist - checked at env-build time, never assumed."""
        cuda_home = tmp_path / "nvidia" / "cu13"
        (cuda_home / "bin").mkdir(parents=True)
        # No include/ or lib/ dirs created.

        class _Spec:
            submodule_search_locations = [str(cuda_home)]

        import importlib.util as importlib_util

        monkeypatch.setattr(importlib_util, "find_spec", lambda name: _Spec())

        opt = SageAttention2()
        probe = _probe(
            compute_capability=(9, 0), nvcc_source="venv",
            nvcc_found=True, nvcc_version=(13, 0), nvcc_cuda_matches_torch=True, torch_cuda_version="13.0",
        )
        spec = opt.install_spec(probe)

        assert spec.env["CUDA_HOME"] == str(cuda_home)
        assert "CPATH" not in spec.env
        assert "LIBRARY_PATH" not in spec.env

    def test_flash_attn_gets_the_same_env_treatment(self, monkeypatch, tmp_path):
        cuda_home = tmp_path / "nvidia" / "cu13"
        (cuda_home / "bin").mkdir(parents=True)
        (cuda_home / "include").mkdir()

        class _Spec:
            submodule_search_locations = [str(cuda_home)]

        def _fake_find_spec(name):
            return _Spec() if name == "nvidia.cu13" else None

        import importlib.util as importlib_util

        monkeypatch.setattr(importlib_util, "find_spec", _fake_find_spec)

        opt = FlashAttn()
        probe = _probe(
            nvcc_source="venv",
            nvcc_found=True, nvcc_version=(13, 0), nvcc_cuda_matches_torch=True, torch_cuda_version="13.0",
        )
        spec = opt.install_spec(probe)
        assert spec.env["CUDA_HOME"] == str(cuda_home)
