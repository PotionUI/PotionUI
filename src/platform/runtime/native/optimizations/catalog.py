"""Declarative catalog of native-engine acceleration libraries.

Each :class:`Optimization` describes one installable acceleration library:
what it needs to build (``requirements``), how to install it (``install_spec``),
and how to flip it on live once installed (``activate``). The catalog is meant
to grow — a plugin-free, in-core list for now, kept small and explicit rather
than over-engineered into a registry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .probe import SystemProbe, cuda_major_of

# Verified 2026-07-11 by downloading and inspecting the actual wheels: CUDA
# <=12 series' pip packages (the `-cu{major}`-suffixed ones, e.g.
# `nvidia-cuda-nvcc-cu12`) ship only `ptxas` + libNVVM (Numba's JIT backend),
# never a real `nvcc` compiler driver. Starting with the CUDA 13.x unsuffixed
# repackaging (`nvidia-cuda-nvcc`, pinned via a version specifier), NVIDIA
# ships the full compiler (nvcc, cudafe++, fatbinary, nvlink, ...) under one
# `nvidia/cu{major}/` prefix. This is a capability *gate* (>=), not a
# hardcoded special-case for "13": a correctly-packaged CUDA 14/15/...
# series is picked up automatically once torch reports that major.
MIN_CUDA_MAJOR_WITH_PIP_NVCC = 13


@dataclass
class Requirement:
    """One precondition for installing an optimization."""

    id: str
    label: str
    met: bool
    detail: str = ""


@dataclass
class InstallSpec:
    """What to hand the installer: pip arguments and extra environment.

    ``fallback_pip_args``: an alternate package-name spelling to try if the
    primary one resolves to nothing installable. NVIDIA has used more than one
    naming scheme for its per-CUDA-series pip packages over time (a
    ``-cu{major}`` suffix for some series, an unsuffixed name pinned via a
    version specifier for others); rather than hardcode which series uses
    which, the installer just tries the primary spelling first and falls back
    to this one.
    """

    pip_args: list[str]
    env: dict[str, str] = field(default_factory=dict)
    fallback_pip_args: Optional[list[str]] = None


@dataclass
class OptimizationStatus:
    """Serializable status of one optimization, given a probe snapshot."""

    opt_id: str
    name: str
    description: str
    benefit: str
    needs_restart: bool
    installed: bool
    installed_version: Optional[str]
    active: bool
    requirements: list[Requirement]
    installable: bool


class Optimization:
    """Base class for a catalog entry. Subclasses fill in the specifics."""

    id: str
    name: str
    description: str
    benefit: str
    needs_restart: bool = False

    def requirements(self, probe: SystemProbe) -> list[Requirement]:
        raise NotImplementedError

    def installed_version(self, probe: SystemProbe) -> Optional[str]:
        raise NotImplementedError

    def install_spec(self, probe: SystemProbe) -> InstallSpec:
        raise NotImplementedError

    def activate(self) -> dict:
        """Make an already-installed optimization live. Returns activation info."""
        raise NotImplementedError

    def status(self, probe: SystemProbe) -> OptimizationStatus:
        requirements = self.requirements(probe)
        installed_version = self.installed_version(probe)
        installed = installed_version is not None
        installable = installed_version is None and all(r.met for r in requirements)
        active = installed and probe.active_backend in self._active_backend_names()

        return OptimizationStatus(
            opt_id=self.id,
            name=self.name,
            description=self.description,
            benefit=self.benefit,
            needs_restart=self.needs_restart,
            installed=installed,
            installed_version=installed_version,
            active=active,
            requirements=requirements,
            installable=installable,
        )

    def _active_backend_names(self) -> tuple[str, ...]:
        return ()


def _cap_at_least(probe: SystemProbe, major: int, minor: int = 0) -> bool:
    if probe.compute_capability is None:
        return False
    return probe.compute_capability >= (major, minor)


def _torch_cuda_major(probe: SystemProbe) -> Optional[int]:
    """The CUDA major version torch was built against, derived purely from
    ``probe.torch_cuda_version`` - never a hardcoded number, so this keeps
    working for whatever CUDA series torch ships next."""
    return cuda_major_of(probe.torch_cuda_version)


def _pip_nvcc_supported(probe: SystemProbe) -> bool:
    """Whether the one-click `cuda_toolchain` install can produce a working
    nvcc for torch's CUDA series - see MIN_CUDA_MAJOR_WITH_PIP_NVCC."""
    major = _torch_cuda_major(probe)
    return major is not None and major >= MIN_CUDA_MAJOR_WITH_PIP_NVCC


def _system_toolkit_hint(probe: SystemProbe) -> str:
    """Actionable "install it yourself" hint for CUDA series pip can't cover
    (<= MIN_CUDA_MAJOR_WITH_PIP_NVCC), derived from torch's own CUDA version -
    not a fixed distro assumption, just NVIDIA's common apt package naming.
    """
    if not probe.torch_cuda_version:
        return "Install a system CUDA toolkit matching your torch build."
    parts = probe.torch_cuda_version.split(".")
    pkg_suffix = "-".join(parts[:2]) if len(parts) >= 2 else parts[0]
    return (
        f"Install a system CUDA toolkit matching CUDA {probe.torch_cuda_version}, e.g. "
        f"`apt install cuda-toolkit-{pkg_suffix}` from NVIDIA's apt repo."
    )


def _nvcc_requirement(probe: SystemProbe) -> Requirement:
    """A from-source build needs an ``nvcc`` whose CUDA *major* matches torch's
    - not just any ``nvcc`` on PATH. Shared by SageAttention2 and FlashAttn so
    both point at the same fix with the same wording: the one-click
    ``cuda_toolchain`` optimization when pip can provide a working nvcc for
    this CUDA series, otherwise an honest system-toolkit install hint.
    """
    torch_cuda = probe.torch_cuda_version or "?"
    met = probe.nvcc_found and probe.nvcc_cuda_matches_torch
    pip_supported = _pip_nvcc_supported(probe)

    if met:
        detail = ""
    elif probe.nvcc_found and probe.nvcc_version:
        found = ".".join(str(part) for part in probe.nvcc_version)
        fix = (
            "Install the 'CUDA Toolchain (match torch)' optimization from this panel to align them."
            if pip_supported
            else _system_toolkit_hint(probe)
        )
        detail = f"Found nvcc {found}, but it doesn't match your torch build (cuda {torch_cuda}). {fix}"
    else:
        detail = (
            (
                f"Install the 'CUDA Toolchain (match torch)' optimization from this panel to get an nvcc "
                f"matching your torch build (cuda {torch_cuda})."
            )
            if pip_supported
            else _system_toolkit_hint(probe)
        )

    return Requirement(id="nvcc", label="CUDA toolkit (nvcc) matching torch", met=met, detail=detail)


def _venv_cuda_build_env(probe: SystemProbe) -> dict[str, str]:
    """Extra build env for compiling a CUDA source extension (SageAttention2,
    flash-attn) against a torch-matching ``nvcc`` that lives in this venv
    (pip-installed) rather than on the system PATH.

    A no-op unless the torch-matching nvcc's source is "venv" - torch's
    ``cpp_extension`` resolves ``CUDA_HOME``/``nvcc`` from the environment or
    ``which nvcc`` on its own for a system install, so nothing extra is needed
    there. Verified 2026-07-11: nvidia-cuda-nvcc and its dependencies
    (nvidia-nvvm, nvidia-cuda-runtime, nvidia-cuda-crt) all install into one
    shared ``nvidia/cu{major}/`` prefix (bin/ + include/ + lib/), so that
    prefix alone is CUDA_HOME - no need to locate each component separately.
    """
    if probe.nvcc_source != "venv" or not (probe.nvcc_found and probe.nvcc_cuda_matches_torch):
        return {}

    major = _torch_cuda_major(probe)
    if major is None:
        return {}

    import importlib.util

    spec = importlib.util.find_spec(f"nvidia.cu{major}")
    if spec is None or not spec.submodule_search_locations:
        return {}
    cuda_home = Path(spec.submodule_search_locations[0])

    env = {
        "CUDA_HOME": str(cuda_home),
        "PATH": os.pathsep.join([str(cuda_home / "bin"), os.environ.get("PATH", "")]),
    }

    include_dir = cuda_home / "include"
    if include_dir.is_dir():
        env["CPATH"] = os.pathsep.join([str(include_dir), os.environ.get("CPATH", "")])

    lib_dir = cuda_home / "lib"
    if lib_dir.is_dir():
        env["LIBRARY_PATH"] = os.pathsep.join([str(lib_dir), os.environ.get("LIBRARY_PATH", "")])

    return env


class SageAttention2(Optimization):
    """SageAttention 2.x — quantized attention kernel, ~30% faster per step on Ampere+."""

    id = "sageattention2"
    name = "SageAttention 2"
    description = (
        "Quantized attention kernel compiled from source (thu-ml/SageAttention). "
        "Requires the CUDA toolkit (nvcc) and Triton."
    )
    benefit = "~30% faster per diffusion step on Ampere+ GPUs (sm80+)."
    needs_restart = False

    def requirements(self, probe: SystemProbe) -> list[Requirement]:
        return [
            Requirement(
                id="cuda_available",
                label="CUDA GPU",
                met=probe.cuda_available,
                detail="" if probe.cuda_available else "No CUDA-capable GPU was detected.",
            ),
            Requirement(
                id="compute_capability",
                label="Compute capability >= 8.0 (Ampere+)",
                met=_cap_at_least(probe, 8, 0),
                detail=(
                    ""
                    if _cap_at_least(probe, 8, 0)
                    else f"GPU compute capability is {probe.compute_capability}; SageAttention 2 needs sm80+."
                ),
            ),
            _nvcc_requirement(probe),
            Requirement(
                id="triton",
                label="Triton installed",
                met=probe.triton_version is not None,
                detail="" if probe.triton_version else "Install triton first (pip install triton).",
            ),
        ]

    def installed_version(self, probe: SystemProbe) -> Optional[str]:
        return probe.sageattention_version

    def install_spec(self, probe: SystemProbe) -> InstallSpec:
        major = probe.compute_capability[0] if probe.compute_capability else 8
        minor = probe.compute_capability[1] if probe.compute_capability else 0
        cpu_count = os.cpu_count() or 1
        env = {
            "TORCH_CUDA_ARCH_LIST": f"{major}.{minor}",
            "MAX_JOBS": str(min(8, max(1, cpu_count - 1))),
        }
        env.update(_venv_cuda_build_env(probe))
        # SageAttention's setup.py imports torch at build time, so pip's
        # isolated build env (which has no torch) fails in
        # get_requires_for_build_wheel — build against the venv directly.
        return InstallSpec(
            pip_args=["git+https://github.com/thu-ml/SageAttention", "--no-build-isolation"],
            env=env,
        )

    def activate(self) -> dict:
        import importlib

        from src.platform.runtime.native import attention

        importlib.invalidate_caches()
        attention.reset_backend_cache()
        return {"active_backend": attention.get_attention_backend()}

    def _active_backend_names(self) -> tuple[str, ...]:
        return ("sage2", "sage")


def _cap_in(probe: SystemProbe, capabilities: frozenset[tuple[int, int]]) -> bool:
    return probe.compute_capability is not None and probe.compute_capability in capabilities


def _cap_major_in(probe: SystemProbe, majors: frozenset[int]) -> bool:
    return probe.compute_capability is not None and probe.compute_capability[0] in majors


def _cuda_at_least(probe: SystemProbe, major: int, minor: int = 0) -> bool:
    version = probe.torch_cuda_version
    if not version:
        return False
    parts = version.split(".")
    try:
        v_major = int(parts[0])
        v_minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return False
    return (v_major, v_minor) >= (major, minor)


class SageAttention3(Optimization):
    """SageAttention 3 — FP4 attention for Blackwell (sm100/sm120/sm121 only).

    arXiv:2505.11594; thu-ml/SageAttention's ``sageattention3_blackwell``
    subdirectory — a SEPARATE from-source package (module ``sageattn3``, not
    an extra mode of the ``sageattention`` package SageAttention2 installs).
    Its CUDA extension is built with family-specific (``...a``-suffixed) arch
    flags for EXACTLY three capabilities — (10,0)/(12,0)/(12,1) — not a
    ``>=`` range, so a card outside that exact set (including a hypothetical
    future sm130) cannot run it without its own rebuild. The user's dev GPU
    (RTX 5090) is sm120, matching. Also hard-requires CUDA runtime >= 12.8.

    Python: the README states ``python>=3.13`` as the required/tested
    interpreter. ``setup.py``'s own ``python_requires`` is the permissive
    ``>=3.8`` (verified 2026-07-13 by re-fetching both files directly) — that
    field only controls whether *pip itself* refuses the install, it does not
    mean the package actually works below the README's stated floor, and a
    research repo's ``setup.py`` boilerplate is far less trustworthy here than
    its own documented test matrix. The README is treated as authoritative:
    gating on Python >= 3.13 means a 3.12 venv (this project's current one)
    shows red before attempting a build that would likely install "successfully"
    per pip's own metadata check and then fail or misbehave at runtime.
    """

    id = "sageattention3"
    name = "SageAttention 3 (Blackwell FP4)"
    description = (
        "FP4 attention kernel for Blackwell GPUs only (RTX 50-series / B100 / B200), compiled from "
        "source (thu-ml/SageAttention, sageattention3_blackwell). Up to ~5x over FlashAttention2 on "
        "RTX 5090 per the paper's benchmarks — benchmark on your own workload before relying on it; "
        "unsupported GPUs cannot install or run this at all (no partial/degraded path). Also requires "
        "Python >= 3.13 per the upstream README (not just whatever pip's own metadata check allows)."
    )
    benefit = "Up to ~5x faster attention than FlashAttention2 on Blackwell (sm100/sm120/sm121) GPUs."
    needs_restart = False

    # See the class docstring: exact membership, never a `>=` range.
    _SUPPORTED_CAPABILITIES = frozenset({(10, 0), (12, 0), (12, 1)})
    _MIN_CUDA = (12, 8)
    _MIN_PYTHON = (3, 13)

    def requirements(self, probe: SystemProbe) -> list[Requirement]:
        cap_ok = _cap_in(probe, self._SUPPORTED_CAPABILITIES)
        cuda_ok = _cuda_at_least(probe, *self._MIN_CUDA)
        python_ok = probe.python_version >= self._MIN_PYTHON
        return [
            Requirement(
                id="cuda_available",
                label="CUDA GPU",
                met=probe.cuda_available,
                detail="" if probe.cuda_available else "No CUDA-capable GPU was detected.",
            ),
            Requirement(
                id="compute_capability",
                label="Blackwell GPU (sm100 / sm120 / sm121 exactly)",
                met=cap_ok,
                detail=(
                    ""
                    if cap_ok
                    else (
                        f"GPU compute capability is {probe.compute_capability}; SageAttention 3's "
                        "prebuilt kernel only targets Blackwell (10.0 / 12.0 / 12.1) — other GPUs "
                        "(including older or newer ones) cannot run it."
                    )
                ),
            ),
            Requirement(
                id="cuda_runtime",
                label=f"CUDA runtime >= {self._MIN_CUDA[0]}.{self._MIN_CUDA[1]}",
                met=cuda_ok,
                detail=(
                    ""
                    if cuda_ok
                    else f"torch CUDA build is {probe.torch_cuda_version or 'unknown'}; SageAttention 3 needs "
                    f"CUDA {self._MIN_CUDA[0]}.{self._MIN_CUDA[1]}+."
                ),
            ),
            Requirement(
                id="python_version",
                label=f"Python >= {self._MIN_PYTHON[0]}.{self._MIN_PYTHON[1]}",
                met=python_ok,
                detail=(
                    ""
                    if python_ok
                    else (
                        f"This venv is Python {probe.python_version[0]}.{probe.python_version[1]}; "
                        f"SageAttention 3 requires Python {self._MIN_PYTHON[0]}.{self._MIN_PYTHON[1]}+ "
                        "per the upstream README — this needs a venv rebuild on a newer Python, not just "
                        "a pip install here."
                    )
                ),
            ),
            _nvcc_requirement(probe),
        ]

    def installed_version(self, probe: SystemProbe) -> Optional[str]:
        return probe.sageattn3_version

    def install_spec(self, probe: SystemProbe) -> InstallSpec:
        major = probe.compute_capability[0] if probe.compute_capability else 12
        minor = probe.compute_capability[1] if probe.compute_capability else 0
        cpu_count = os.cpu_count() or 1
        env = {
            "TORCH_CUDA_ARCH_LIST": f"{major}.{minor}",
            "MAX_JOBS": str(min(8, max(1, cpu_count - 1))),
        }
        env.update(_venv_cuda_build_env(probe))
        # No PyPI package (source-only, see class docstring): clone the repo
        # and build the sageattention3_blackwell subdirectory specifically.
        # setup.py imports torch at build time (same reason as SageAttention2),
        # so this also needs --no-build-isolation against the venv directly.
        return InstallSpec(
            pip_args=[
                "git+https://github.com/thu-ml/SageAttention#subdirectory=sageattention3_blackwell",
                "--no-build-isolation",
            ],
            env=env,
        )

    def activate(self) -> dict:
        import importlib

        from src.platform.runtime.native import attention

        importlib.invalidate_caches()
        attention.reset_backend_cache()
        return {"active_backend": attention.get_attention_backend()}

    def _active_backend_names(self) -> tuple[str, ...]:
        return ("sage3",)


class SpargeAttention(Optimization):
    """SpargeAttn — training-free SPARSE attention (thu-ml/SpargeAttn,
    arXiv:2502.18137, ICML2025). Built on SageAttention2's kernels; a two-stage
    online filter predicts and skips low-contribution attention blocks.

    UNLIKE every other entry in this catalog, this is an APPROXIMATION, not a
    numerically near-lossless kernel swap — output quality depends on content
    and the sparsity/accuracy tradeoff (its tune-free default is ``topk=0.5``;
    see ``attention.py``'s ``_sparge`` wrapper). For that reason the dispatcher
    never auto-selects it (``attention.PIN_ONLY_BACKENDS``): installing it only
    makes it available as an explicit ``$NATIVE_ATTENTION=sparge`` / admin pin
    (this panel's activate control), never picked automatically no matter how
    available it is — see ``attention.get_attention_backend``.

    Compute capability: ``thu-ml/SpargeAttn``'s own ``setup.py``
    ``SUPPORTED_ARCHS`` lists 8.0/8.6/8.7/8.9/9.0 only (Ampere/Ada/Hopper) — no
    Blackwell entry as of this writing (verified 2026-07-13 by fetching
    setup.py directly), unlike sage2/sage3. Gate is major-in-{8,9}, not an
    exact tuple: SpargeAttn compiles per-local-GPU at install time (unlike
    sage3's prebuilt family-specific kernel), so any minor within a supported
    major works.
    """

    id = "sparge-attention"
    name = "SpargeAttention (sparse, approximate)"
    description = (
        "Training-free SPARSE attention compiled from source (thu-ml/SpargeAttn) that skips "
        "low-contribution attention blocks. Unlike SageAttention 2/3, this is APPROXIMATE — "
        "quality varies by content; benchmark and eyeball outputs on your own workload before "
        "adopting. Expect gains mainly on long-sequence video. Never auto-selected: pin it "
        "explicitly (this panel, or $NATIVE_ATTENTION=sparge) once installed."
    )
    benefit = "Paper reports ~4-7x on long sequences; content-dependent — verify on your own workload."
    needs_restart = False

    # See class docstring: major-only membership, not an exact (major, minor) tuple.
    _SUPPORTED_MAJORS = frozenset({8, 9})

    def requirements(self, probe: SystemProbe) -> list[Requirement]:
        cap_ok = _cap_major_in(probe, self._SUPPORTED_MAJORS)
        return [
            Requirement(
                id="cuda_available",
                label="CUDA GPU",
                met=probe.cuda_available,
                detail="" if probe.cuda_available else "No CUDA-capable GPU was detected.",
            ),
            Requirement(
                id="compute_capability",
                label="Ampere / Ada / Hopper GPU (sm80-sm90)",
                met=cap_ok,
                detail=(
                    ""
                    if cap_ok
                    else (
                        f"GPU compute capability is {probe.compute_capability}; SpargeAttn's "
                        "kernel (compiled at install time for this specific GPU) only targets "
                        "Ampere/Ada/Hopper (sm80-sm90) per its own setup.py — Blackwell is not "
                        "built at all as of this writing."
                    )
                ),
            ),
            _nvcc_requirement(probe),
        ]

    def installed_version(self, probe: SystemProbe) -> Optional[str]:
        return probe.sparge_version

    def install_spec(self, probe: SystemProbe) -> InstallSpec:
        major = probe.compute_capability[0] if probe.compute_capability else 8
        minor = probe.compute_capability[1] if probe.compute_capability else 0
        cpu_count = os.cpu_count() or 1
        env = {
            "TORCH_CUDA_ARCH_LIST": f"{major}.{minor}",
            "MAX_JOBS": str(min(8, max(1, cpu_count - 1))),
        }
        env.update(_venv_cuda_build_env(probe))
        # setup.py imports torch at build time (same reason as SageAttention2/3)
        # -- needs --no-build-isolation against the venv directly.
        return InstallSpec(
            pip_args=["git+https://github.com/thu-ml/SpargeAttn", "--no-build-isolation"],
            env=env,
        )

    def activate(self) -> dict:
        import importlib

        from src.platform.runtime.native import attention

        importlib.invalidate_caches()
        attention.reset_backend_cache()
        return {"active_backend": attention.get_attention_backend()}

    def _active_backend_names(self) -> tuple[str, ...]:
        return ("sparge",)


class FlashAttn(Optimization):
    """flash-attn — the reference FlashAttention CUDA kernel."""

    id = "flash-attn"
    name = "FlashAttention"
    description = "The reference FlashAttention CUDA kernel, installed with --no-build-isolation."
    benefit = "Faster attention than sdpa on supported GPUs; a fallback path when SageAttention isn't available."
    needs_restart = False

    def requirements(self, probe: SystemProbe) -> list[Requirement]:
        return [
            Requirement(
                id="cuda_available",
                label="CUDA GPU",
                met=probe.cuda_available,
                detail="" if probe.cuda_available else "No CUDA-capable GPU was detected.",
            ),
            Requirement(
                id="compute_capability",
                label="Compute capability >= 8.0",
                met=_cap_at_least(probe, 8, 0),
                detail=(
                    ""
                    if _cap_at_least(probe, 8, 0)
                    else f"GPU compute capability is {probe.compute_capability}; flash-attn needs sm80+."
                ),
            ),
            _nvcc_requirement(probe),
        ]

    def installed_version(self, probe: SystemProbe) -> Optional[str]:
        return probe.flash_attn_version

    def install_spec(self, probe: SystemProbe) -> InstallSpec:
        return InstallSpec(
            pip_args=["flash-attn", "--no-build-isolation"], env=_venv_cuda_build_env(probe),
        )

    def activate(self) -> dict:
        import importlib

        from src.platform.runtime.native import attention

        importlib.invalidate_caches()
        attention.reset_backend_cache()
        return {"active_backend": attention.get_attention_backend()}

    def _active_backend_names(self) -> tuple[str, ...]:
        return ("flash",)


class CudaToolchain(Optimization):
    """Pip-installable CUDA compiler (nvcc + cub/thrust headers), pinned to
    the same CUDA series as torch's own build - the "align nvcc with torch"
    fix for SageAttention2/flash-attn's nvcc requirement.

    Verified 2026-07-11 by downloading and inspecting the actual wheels:
    NVIDIA's unsuffixed ``nvidia-cuda-nvcc`` package (its own dependencies -
    nvidia-nvvm, nvidia-cuda-runtime, nvidia-cuda-crt - resolve automatically
    via pip) ships a genuinely complete compiler for CUDA series
    >= MIN_CUDA_MAJOR_WITH_PIP_NVCC: ``nvcc``, ``cudafe++``, ``fatbinary``,
    ``nvlink``, ``nvcc.profile``, all under one ``nvidia/cu{major}/`` prefix.
    Older, ``-cu{major}``-suffixed series (<= 12) only ship ``ptxas`` +
    libNVVM (Numba's JIT backend) - no real ``nvcc`` - so this optimization is
    simply not installable there; see the ``pip_nvcc_available_for_series``
    requirement below, which points at a system CUDA toolkit install instead.

    The package name (and the capability gate) is derived from
    ``probe.torch_cuda_version`` at call time - never hardcoded to one CUDA
    series - so a correctly-packaged CUDA 14/15/... series works with no code
    change. ``nvidia-cuda-cccl`` (cub/thrust headers, often needed by CUDA
    extension builds) is included best-effort: if it doesn't resolve for a
    given series, `InstallSpec.fallback_pip_args` retries with nvcc alone.
    """

    id = "cuda_toolchain"
    name = "CUDA Toolchain (match torch)"
    description = (
        "Installs a CUDA compiler (nvcc + cub/thrust headers) via pip, pinned to the same "
        "CUDA series as your torch build."
    )
    benefit = "Aligns the CUDA toolchain with torch's CUDA build for optimizations that compile from source."
    needs_restart = False

    def requirements(self, probe: SystemProbe) -> list[Requirement]:
        major = _torch_cuda_major(probe)
        supported = major is not None and major >= MIN_CUDA_MAJOR_WITH_PIP_NVCC
        return [
            Requirement(
                id="cuda_available",
                label="CUDA GPU",
                met=probe.cuda_available,
                detail="" if probe.cuda_available else "No CUDA-capable GPU was detected.",
            ),
            Requirement(
                id="torch_cuda_version_known",
                label="torch CUDA build detected",
                met=major is not None,
                detail=(
                    ""
                    if major is not None
                    else "Could not determine which CUDA version your torch build targets."
                ),
            ),
            Requirement(
                id="pip_nvcc_available_for_series",
                label=f"pip-installable nvcc available (CUDA >= {MIN_CUDA_MAJOR_WITH_PIP_NVCC})",
                # Unknown major is already reported by torch_cuda_version_known above;
                # avoid a second, redundant message for the same root cause.
                met=bool(supported),
                detail=(
                    ""
                    if major is None or supported
                    else (
                        f"NVIDIA's pip wheels for CUDA {major}.x don't include a working nvcc compiler "
                        f"(only CUDA {MIN_CUDA_MAJOR_WITH_PIP_NVCC}+ does, verified 2026-07-11). "
                        + _system_toolkit_hint(probe)
                    )
                ),
            ),
        ]

    def installed_version(self, probe: SystemProbe) -> Optional[str]:
        if not (probe.nvcc_found and probe.nvcc_cuda_matches_torch and probe.nvcc_version):
            return None
        return f"{probe.nvcc_version[0]}.{probe.nvcc_version[1]}"

    def install_spec(self, probe: SystemProbe) -> InstallSpec:
        major = _torch_cuda_major(probe)
        if major is None or major < MIN_CUDA_MAJOR_WITH_PIP_NVCC:
            # Callers check requirements()/status() first (the controller
            # short-circuits on unmet requirements before ever calling this);
            # this guards direct misuse rather than issuing an install that can't work.
            raise ValueError(
                "cuda_toolchain has no pip-installable nvcc for this torch CUDA build; "
                "requirements()/status() should already report it as unmet/not-installable"
            )

        return InstallSpec(
            pip_args=[f"nvidia-cuda-nvcc=={major}.*", f"nvidia-cuda-cccl=={major}.*"],
            # cccl isn't a hard nvcc dependency - if it doesn't resolve for
            # this series, nvcc alone is still a working compiler.
            fallback_pip_args=[f"nvidia-cuda-nvcc=={major}.*"],
        )

    def activate(self) -> dict:
        from .probe import probe_system

        new_probe = probe_system()
        return {
            "nvcc_found": new_probe.nvcc_found,
            "nvcc_version": new_probe.nvcc_version,
            "nvcc_cuda_matches_torch": new_probe.nvcc_cuda_matches_torch,
            "nvcc_source": new_probe.nvcc_source,
        }

    def _active_backend_names(self) -> tuple[str, ...]:
        return ()

    def status(self, probe: SystemProbe) -> OptimizationStatus:
        # Not an attention backend - "active" just means the toolchain
        # currently matches torch, same condition as `installed`.
        base = super().status(probe)
        base.active = base.installed
        return base


CATALOG: dict[str, Optimization] = {
    opt.id: opt
    for opt in (SageAttention2(), SageAttention3(), SpargeAttention(), FlashAttn(), CudaToolchain())
}


def get_optimization(opt_id: str) -> Optional[Optimization]:
    return CATALOG.get(opt_id)
