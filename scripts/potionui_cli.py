#!/usr/bin/env python3
"""PotionUI bootstrap CLI — the logic behind the `./potionui` entry point.

Invoked via the thin bash shim at the repo root (`./potionui`), which only
locates a Python 3.12+ interpreter and hands off here; everything else lives
in this module so it can be unit-tested with fakes instead of a live GPU box.

Subcommands
-----------
- ``doctor``       — run every environment check and report pass/fail/warn.
  ``--profile {local,hybrid,remote}`` selects the install preset (see
  "Install presets" below); ``--no-gpu`` is a compatibility alias for
  ``--profile remote``.
- ``start``        — run doctor's blocking checks, install anything missing,
  then launch backend + frontend as supervised child processes. Takes the
  same ``--profile``/``--no-gpu``.
- ``status``       — report whether a previously-started instance is alive.
- ``stop``         — stop a previously-started instance (safe no-op if none).
- ``start-docker`` — preflight + exec the containerized dev/simulation harness
  (``docker compose -f docker/docker-compose.yml up --build``). Requires an
  NVIDIA GPU and nvidia-container-toolkit; see ``docker/README.md``.
- ``worker doctor`` / ``worker start`` — the ``worker`` install preset: checks
  and launches a standalone Remote Native worker (``worker.py``) on a GPU box
  that serves another PotionUI instance. See "Install presets" below.

Doctor check registry
----------------------
Each check has a stable ``code`` used both in human output and ``--json``
rows. ``blocking=True`` means a failing check (severity ``error``) stops
``./potionui start`` outright — none of those are auto-installed, unlike
venv/backend-deps/frontend-deps, which `start` repairs itself.

====================  ========  ========  =========================================
code                  severity  blocking  what it checks
====================  ========  ========  =========================================
PY312                 error     yes       a Python 3.12+ interpreter is on PATH
VENV                  warning   no        ./venv exists (created by `start` if not)
BACKEND_DEPS          warning   no        fastapi/torch importable from ./venv
NODE                  error     yes       node on PATH, major version >= 18
NPM                   error     yes       npm on PATH
FRONTEND_DEPS         warning   no        frontend/node_modules/.bin/vite present
GPU                   warning   no        nvidia-smi present and reports a GPU
                                           (info, not warning, once the remote
                                           profile is active; error+blocking
                                           under ``worker doctor``)
DISK                  error     yes       free disk space on the repo's filesystem
PORT_8005             error     yes       backend port is free to bind
PORT_3001             error     yes       frontend port is free to bind
STORAGE               error     yes       ./storage exists and is writable
ENV_FILE              info      no        ./.env present (purely informational)
====================  ========  ========  =========================================

``worker doctor`` runs a different, shorter registry — no NODE/NPM/
FRONTEND_DEPS/ENV_FILE rows, since a worker ships no frontend:

====================  ========  ========  =========================================
code                  severity  blocking  what it checks
====================  ========  ========  =========================================
PY312                 error     yes       a Python 3.12+ interpreter is on PATH
VENV                  warning   no        ./venv exists (created by `worker start` if not)
BACKEND_DEPS          warning   no        fastapi/torch importable from ./venv (GPU profile)
GPU                   error     yes       nvidia-smi present and reports a GPU — a
                                           worker with no GPU can't execute anything
DISK                  error     yes       free disk space on the repo's filesystem
PORT_8100             error     yes       worker port is free to bind (``--port``)
WORKER_DIR            error     yes       ``POTIONUI_WORKER_DIR`` (default ./worker_data)
                                           exists and is writable
WORKER_TOKEN          error     yes       ``POTIONUI_WORKER_TOKEN`` is set (env or .env)
====================  ========  ========  =========================================

Each row also carries a concrete ``repair`` command/hint. ``--json`` emits the
same rows as ``[{"code", "severity", "message", "repair"}, ...]``.

Install presets
----------------
``./potionui doctor``/``start --profile {local,hybrid,remote}`` (default
``local``) picks what gets installed:

- ``local``  — today's default: the full CUDA stack
  (``pip install -r requirements.txt -c constraints.txt``). For a box with
  its own NVIDIA GPU.
- ``hybrid`` — identical dependency profile to ``local`` (a remote worker is
  core code, no extra packages to install); ``start`` prints one line after
  readiness pointing at Admin -> Backends -> Add backend -> Native (Remote
  Worker) to add a worker later. Doctor is identical to ``local``.
- ``remote`` — for hosts with no NVIDIA card (e.g. a VPS or laptop that
  dispatches to a remote worker) — NOT a CPU-generation mode. A plain
  ``pip install -r requirements.txt -c constraints.txt`` drags in the full
  CUDA 13 stack (see ``constraints.txt``'s header); ``remote`` instead
  installs ``requirements-cpu.txt`` (the same package surface minus
  ``xformers``, which has no CPU build) against PyTorch's CPU wheel index,
  and never touches ``constraints.txt`` (its ``nvidia-cu13-*``/``triton``
  pins are unsatisfiable without CUDA). The GPU doctor row is downgraded to
  informational under this profile.
- ``worker`` — not a ``--profile`` value; a separate ``./potionui worker
  doctor``/``worker start`` preset for the GPU box itself, when it only
  serves another PotionUI instance as a Remote Native worker (see
  ``docs/remote-native.md``). Installs the full CUDA stack, no frontend
  packages, and runs ``python worker.py`` in the foreground.

The chosen ``--profile``/``--no-gpu`` is persisted at
``.runtime/install_profile`` so a later plain ``./potionui start``/``doctor``
reuses it instead of drifting back to ``local``. ``--no-gpu`` is kept as a
compatibility alias for ``--profile remote`` — it is a documented public flag
of the released 0.0.2 — and a pre-existing ``.runtime/no_gpu_profile`` marker
from that release is read back as ``remote``.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = REPO_ROOT / ".runtime"
LOG_DIR = RUNTIME_DIR / "logs"
STATE_FILE = RUNTIME_DIR / "state.json"
NO_GPU_PROFILE_FILE = RUNTIME_DIR / "no_gpu_profile"  # legacy 0.0.2 marker, read-only
INSTALL_PROFILE_FILE = RUNTIME_DIR / "install_profile"

DEFAULT_BACKEND_PORT = 8005
DEFAULT_FRONTEND_PORT = 3001
DEFAULT_WORKER_PORT = 8100
DEFAULT_WORKER_HOST = "127.0.0.1"
DEFAULT_WORKER_DIR_NAME = "worker_data"
WORKER_TOKEN_ENV_VAR = "POTIONUI_WORKER_TOKEN"
CONSTRAINTS_FILE = "constraints.txt"
NO_GPU_REQUIREMENTS_FILE = "requirements-cpu.txt"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
DOCKER_COMPOSE_FILE = "docker/docker-compose.yml"
DEFAULT_START_TIMEOUT = 180.0
MIN_PYTHON = (3, 12)
GB = 1024 ** 3
DISK_FAIL_THRESHOLD_GB = 5
DISK_WARN_THRESHOLD_GB = 25
INSTALL_PROFILES = ("local", "hybrid", "remote")

# Numeric loopback address, not the DNS name "localhost": a host whose
# resolver returns ::1 ahead of 127.0.0.1 (common on modern Linux) makes Vite
# bind IPv6-only when handed the string "localhost", while a probe hard-coded
# to 127.0.0.1 then fails forever. Backend and frontend both bind here and
# both readiness probes target it, so bind and probe can never diverge.
FRONTEND_BIND_HOST = "127.0.0.1"
MIN_NODE_MAJOR = 18
PYTHON_CANDIDATES = ("python3.13", "python3.12", "python3")
BACKEND_MARKER_IMPORT = "import fastapi, torch"


# ---------------------------------------------------------------------------
# Severity + result types
# ---------------------------------------------------------------------------

class Severity:
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class CheckResult:
    code: str
    severity: str
    message: str
    repair: Optional[str] = None
    blocking: bool = False

    def to_row(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "repair": self.repair,
        }


# ---------------------------------------------------------------------------
# Probe: the only layer that touches the real OS. Checks take a `probe`
# object as their first argument so tests can pass a fake instead.
# ---------------------------------------------------------------------------

class RealProbe:
    """Real OS/subprocess access used in production."""

    def which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    def run(self, cmd: Sequence[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
        return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)

    def port_free(self, port: int, host: str = "127.0.0.1") -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return True
            except OSError:
                return False

    def path_exists(self, path: Path) -> bool:
        return Path(path).exists()

    def is_writable_dir(self, path: Path) -> bool:
        path = Path(path)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe_file = path / ".potionui_write_test"
            probe_file.write_text("ok")
            probe_file.unlink()
            return True
        except OSError:
            return False

    def disk_usage(self, path: Path):
        return shutil.disk_usage(path)


# ---------------------------------------------------------------------------
# Individual checks (pure aside from the probe calls they make)
# ---------------------------------------------------------------------------

def probe_python_candidates(probe) -> Optional[tuple[str, str]]:
    """Return (interpreter_path, "major.minor.micro") for the first
    PYTHON_CANDIDATES entry that resolves to a >= MIN_PYTHON interpreter."""
    for name in PYTHON_CANDIDATES:
        path = probe.which(name)
        if not path:
            continue
        try:
            result = probe.run(
                [path, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                timeout=10.0,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        version_str = result.stdout.strip()
        try:
            major, minor = (int(x) for x in version_str.split(".")[:2])
        except ValueError:
            continue
        if (major, minor) >= MIN_PYTHON:
            return path, version_str
    return None


def check_python(probe) -> CheckResult:
    found = probe_python_candidates(probe)
    if found:
        path, version_str = found
        return CheckResult("PY312", Severity.OK, f"Python {version_str} found at {path}.", blocking=True)
    return CheckResult(
        "PY312",
        Severity.ERROR,
        f"No Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ interpreter found on PATH.",
        repair=(
            "Install Python 3.12+ (e.g. `sudo apt install python3.12 python3.12-venv` on "
            "Debian/Ubuntu, or via pyenv/uv), then re-run `./potionui doctor`."
        ),
        blocking=True,
    )


def check_venv(probe, repo_root: Path) -> CheckResult:
    venv_python = repo_root / "venv" / "bin" / "python"
    if probe.path_exists(venv_python):
        return CheckResult("VENV", Severity.OK, f"Virtualenv present at {venv_python.parent.parent}.", blocking=False)
    return CheckResult(
        "VENV",
        Severity.WARNING,
        "No virtualenv found at ./venv.",
        repair="`./potionui start` creates it automatically, or manually: `python3.12 -m venv venv`.",
        blocking=False,
    )


def backend_pip_install_args(repo_root: Path) -> list[str]:
    """`pip install` args for the backend dependencies, adding the pinned
    transitive-version file (`constraints.txt`) when present so installs are
    reproducible. Falls back to an unconstrained install if the file is
    missing — never blocks `./potionui start` on it."""
    args = ["install", "-r", "requirements.txt"]
    if (repo_root / CONSTRAINTS_FILE).exists():
        args += ["-c", CONSTRAINTS_FILE]
    return args


def backend_pip_install_args_no_gpu() -> list[str]:
    """`pip install` args for the --no-gpu profile: the CPU-wheel requirement
    set against PyTorch's CPU index. Deliberately never passes
    `-c constraints.txt` — that file pins the CUDA 13 transitive closure
    (nvidia-cu13-*, GPU-build triton, ...), which has no CPU-only equivalent
    and would make the install unsatisfiable."""
    return ["install", "-r", NO_GPU_REQUIREMENTS_FILE, "--extra-index-url", PYTORCH_CPU_INDEX]


def check_backend_deps(probe, repo_root: Path, no_gpu: bool = False) -> CheckResult:
    pip_repair = (
        f"source venv/bin/activate && pip install -r {NO_GPU_REQUIREMENTS_FILE} --extra-index-url {PYTORCH_CPU_INDEX}"
        if no_gpu
        else "source venv/bin/activate && pip install -r requirements.txt -c constraints.txt"
    )
    venv_python = repo_root / "venv" / "bin" / "python"
    if not probe.path_exists(venv_python):
        return CheckResult(
            "BACKEND_DEPS",
            Severity.WARNING,
            "Virtualenv not created yet, so dependencies can't be checked.",
            repair="`./potionui start` creates the venv and installs dependencies.",
            blocking=False,
        )
    try:
        result = probe.run([str(venv_python), "-c", BACKEND_MARKER_IMPORT], timeout=20.0)
    except Exception as exc:
        return CheckResult(
            "BACKEND_DEPS",
            Severity.WARNING,
            f"Could not probe backend dependencies ({exc}).",
            repair=pip_repair,
            blocking=False,
        )
    if result.returncode == 0:
        return CheckResult("BACKEND_DEPS", Severity.OK, "Backend dependencies (fastapi, torch) importable from ./venv.", blocking=False)
    return CheckResult(
        "BACKEND_DEPS",
        Severity.WARNING,
        "Backend dependencies missing or incomplete in ./venv.",
        repair=pip_repair,
        blocking=False,
    )


def check_node(probe) -> CheckResult:
    path = probe.which("node")
    if not path:
        return CheckResult(
            "NODE",
            Severity.ERROR,
            "node not found on PATH.",
            repair="Install Node.js 18+ (https://nodejs.org, or via nvm) and re-run `./potionui doctor`.",
            blocking=True,
        )
    try:
        result = probe.run([path, "--version"], timeout=10.0)
    except Exception as exc:
        return CheckResult("NODE", Severity.ERROR, f"Failed to run `node --version` ({exc}).", repair="Reinstall Node.js 18+.", blocking=True)
    version_str = result.stdout.strip().lstrip("v")
    major_str = version_str.split(".")[0] if version_str else ""
    major = int(major_str) if major_str.isdigit() else 0
    if major >= MIN_NODE_MAJOR:
        return CheckResult("NODE", Severity.OK, f"node v{version_str} found at {path}.", blocking=True)
    return CheckResult(
        "NODE",
        Severity.ERROR,
        f"node v{version_str or '?'} found, but PotionUI needs Node.js {MIN_NODE_MAJOR}+.",
        repair=f"Upgrade Node.js to {MIN_NODE_MAJOR}+ (e.g. `nvm install {MIN_NODE_MAJOR}`).",
        blocking=True,
    )


def check_npm(probe) -> CheckResult:
    path = probe.which("npm")
    if not path:
        return CheckResult(
            "NPM",
            Severity.ERROR,
            "npm not found on PATH.",
            repair="Install Node.js 18+ (npm ships with it) and re-run `./potionui doctor`.",
            blocking=True,
        )
    try:
        result = probe.run([path, "--version"], timeout=10.0)
    except Exception as exc:
        return CheckResult("NPM", Severity.ERROR, f"Failed to run `npm --version` ({exc}).", repair="Reinstall Node.js/npm.", blocking=True)
    version_str = result.stdout.strip()
    return CheckResult("NPM", Severity.OK, f"npm {version_str} found at {path}.", blocking=True)


def check_frontend_deps(probe, repo_root: Path) -> CheckResult:
    node_modules = repo_root / "frontend" / "node_modules"
    vite_bin = node_modules / ".bin" / "vite"
    if probe.path_exists(vite_bin):
        return CheckResult("FRONTEND_DEPS", Severity.OK, "frontend/node_modules present (vite installed).", blocking=False)
    if probe.path_exists(node_modules):
        return CheckResult(
            "FRONTEND_DEPS",
            Severity.WARNING,
            "frontend/node_modules exists but looks incomplete (vite binary missing).",
            repair="cd frontend && npm install",
            blocking=False,
        )
    return CheckResult(
        "FRONTEND_DEPS",
        Severity.WARNING,
        "frontend/node_modules not installed.",
        repair="`./potionui start` runs `npm install` automatically, or manually: cd frontend && npm install",
        blocking=False,
    )


def check_gpu(probe, no_gpu: bool = False, required: bool = False) -> CheckResult:
    """`required=True` is the `worker doctor` variant: a worker with no GPU
    can't execute anything, so an absent/broken GPU is a blocking error there
    instead of the core install's non-blocking warning/info."""
    path = probe.which("nvidia-smi")
    if not path:
        if required:
            return CheckResult(
                "GPU",
                Severity.ERROR,
                "nvidia-smi not found — no NVIDIA GPU/driver detected. A worker with no "
                "GPU can't execute anything.",
                repair="Install NVIDIA drivers on this box; the worker preset needs a real GPU.",
                blocking=True,
            )
        if no_gpu:
            return CheckResult(
                "GPU",
                Severity.INFO,
                "No GPU detected — fine for the remote install profile with a remote worker.",
                blocking=False,
            )
        return CheckResult(
            "GPU",
            Severity.WARNING,
            "nvidia-smi not found — no NVIDIA GPU/driver detected.",
            repair=(
                "CPU-only is fine for setup/testing; for GPU generation install NVIDIA "
                "drivers (and a CUDA-matching PyTorch build), or run `./potionui start "
                "--profile remote` for remote-worker hosting."
            ),
            blocking=False,
        )
    try:
        result = probe.run([path, "--query-gpu=name,driver_version", "--format=csv,noheader"], timeout=10.0)
    except Exception as exc:
        severity = Severity.ERROR if required else Severity.WARNING
        return CheckResult(
            "GPU", severity, f"nvidia-smi present but failed to run ({exc}).",
            repair="Check the NVIDIA driver installation (`nvidia-smi`).", blocking=required,
        )
    if result.returncode != 0 or not result.stdout.strip():
        severity = Severity.ERROR if required else Severity.WARNING
        return CheckResult(
            "GPU", severity, "nvidia-smi present but reported no GPU.",
            repair="Check the NVIDIA driver installation (`nvidia-smi`).", blocking=required,
        )
    line = result.stdout.strip().splitlines()[0]
    return CheckResult("GPU", Severity.OK, f"GPU detected: {line}.", blocking=required)


def check_port(probe, port: int, code: str, label: str) -> CheckResult:
    if probe.port_free(port):
        return CheckResult(code, Severity.OK, f"Port {port} ({label}) is free.", blocking=True)
    return CheckResult(
        code,
        Severity.ERROR,
        f"Port {port} ({label}) is already in use.",
        repair=(
            f"Find and stop the process using it (`lsof -i :{port}` or `fuser {port}/tcp`), "
            "or if it's a previous PotionUI run: `./potionui stop`."
        ),
        blocking=True,
    )


def check_disk(probe, repo_root: Path) -> CheckResult:
    try:
        usage = probe.disk_usage(repo_root)
    except OSError as exc:
        return CheckResult(
            "DISK",
            Severity.WARNING,
            f"Could not determine free disk space on {repo_root} ({exc}).",
            repair=f"Check that {repo_root} is on a mounted, readable filesystem.",
            blocking=False,
        )
    free_gb = usage.free / GB
    if free_gb < DISK_FAIL_THRESHOLD_GB:
        return CheckResult(
            "DISK",
            Severity.ERROR,
            f"Only {free_gb:.1f} GB free on {repo_root} — not enough space to install dependencies.",
            repair="Free up disk space (at least 5 GB, ideally 25+) before running `./potionui start`.",
            blocking=True,
        )
    if free_gb < DISK_WARN_THRESHOLD_GB:
        return CheckResult(
            "DISK",
            Severity.WARNING,
            f"Only {free_gb:.1f} GB free on {repo_root} — dependency install plus a starter model "
            "checkpoint need roughly 20 GB.",
            repair="Free up disk space, or point model storage at a larger volume before downloading models.",
            blocking=False,
        )
    return CheckResult("DISK", Severity.OK, f"{free_gb:.1f} GB free on {repo_root}.", blocking=False)


def check_storage(probe, repo_root: Path) -> CheckResult:
    storage_dir = repo_root / "storage"
    if probe.is_writable_dir(storage_dir):
        return CheckResult("STORAGE", Severity.OK, f"{storage_dir} is writable.", blocking=True)
    return CheckResult(
        "STORAGE",
        Severity.ERROR,
        f"{storage_dir} does not exist or is not writable.",
        repair=f"mkdir -p {storage_dir} && chmod u+w {storage_dir} (or fix ownership if it already exists).",
        blocking=True,
    )


def check_worker_dir(probe, worker_dir: Path) -> CheckResult:
    if probe.is_writable_dir(worker_dir):
        return CheckResult("WORKER_DIR", Severity.OK, f"{worker_dir} is writable.", blocking=True)
    return CheckResult(
        "WORKER_DIR",
        Severity.ERROR,
        f"{worker_dir} does not exist or is not writable.",
        repair=f"mkdir -p {worker_dir} && chmod u+w {worker_dir} (or fix ownership if it already exists).",
        blocking=True,
    )


def _env_file_has_var(env_path: Path, var: str) -> bool:
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == var and value.strip():
            return True
    return False


def check_worker_token(repo_root: Path, env: Optional[dict] = None) -> CheckResult:
    env = env if env is not None else os.environ
    repair = (
        f"Generate one — `python -c \"import secrets; print(secrets.token_urlsafe(32))\"` — "
        f"then export {WORKER_TOKEN_ENV_VAR}=<value> or add it to .env."
    )
    if env.get(WORKER_TOKEN_ENV_VAR):
        return CheckResult("WORKER_TOKEN", Severity.OK, f"{WORKER_TOKEN_ENV_VAR} set in the environment.", blocking=True)
    if _env_file_has_var(repo_root / ".env", WORKER_TOKEN_ENV_VAR):
        return CheckResult("WORKER_TOKEN", Severity.OK, f"{WORKER_TOKEN_ENV_VAR} set in .env.", blocking=True)
    return CheckResult(
        "WORKER_TOKEN",
        Severity.ERROR,
        f"{WORKER_TOKEN_ENV_VAR} is not set — the worker refuses to start without it.",
        repair=repair,
        blocking=True,
    )


def check_env_file(probe, repo_root: Path) -> CheckResult:
    env_path = repo_root / ".env"
    if probe.path_exists(env_path):
        return CheckResult("ENV_FILE", Severity.INFO, f"{env_path} present.", blocking=False)
    return CheckResult(
        "ENV_FILE",
        Severity.INFO,
        f"{env_path} not found (optional).",
        repair=(
            "Create .env with e.g. `POTIONUI_AUTH_SECRET_KEY=<random-string>` if you need a "
            "custom auth secret; not required to run locally."
        ),
        blocking=False,
    )


# ---------------------------------------------------------------------------
# Docker preflight (start-docker only — not part of run_doctor's registry,
# since a Docker daemon/nvidia-container-toolkit are not required for the
# bare-metal `start` path)
# ---------------------------------------------------------------------------

def check_docker(probe) -> CheckResult:
    path = probe.which("docker")
    if not path:
        return CheckResult(
            "DOCKER",
            Severity.ERROR,
            "docker not found on PATH.",
            repair="Install Docker (https://docs.docker.com/engine/install/) and re-run `./potionui start-docker`.",
            blocking=True,
        )
    try:
        result = probe.run([path, "info"], timeout=10.0)
    except Exception as exc:
        return CheckResult(
            "DOCKER", Severity.ERROR, f"Failed to run `docker info` ({exc}).",
            repair="Check the Docker installation and that the daemon is running.", blocking=True,
        )
    if result.returncode != 0:
        return CheckResult(
            "DOCKER",
            Severity.ERROR,
            "docker found but the daemon is not responding (`docker info` failed).",
            repair="Start the Docker daemon (e.g. `sudo systemctl start docker`) and re-run.",
            blocking=True,
        )
    return CheckResult("DOCKER", Severity.OK, f"docker found at {path}, daemon responding.", blocking=True)


def check_docker_compose(probe) -> CheckResult:
    path = probe.which("docker")
    if not path:
        return CheckResult(
            "DOCKER_COMPOSE",
            Severity.ERROR,
            "docker not found on PATH (needed for `docker compose`).",
            repair="Install Docker (https://docs.docker.com/engine/install/); Compose v2 ships as a plugin.",
            blocking=True,
        )
    try:
        result = probe.run([path, "compose", "version"], timeout=10.0)
    except Exception as exc:
        return CheckResult(
            "DOCKER_COMPOSE", Severity.ERROR, f"Failed to run `docker compose version` ({exc}).",
            repair="Install the Docker Compose v2 plugin (https://docs.docker.com/compose/install/).",
            blocking=True,
        )
    if result.returncode != 0:
        return CheckResult(
            "DOCKER_COMPOSE",
            Severity.ERROR,
            "`docker compose` (v2) is not available.",
            repair="Install the Docker Compose v2 plugin (https://docs.docker.com/compose/install/).",
            blocking=True,
        )
    return CheckResult(
        "DOCKER_COMPOSE", Severity.OK, f"docker compose available: {result.stdout.strip()}", blocking=True
    )


def check_nvidia_container_toolkit(probe) -> CheckResult:
    repair = (
        "Install nvidia-container-toolkit (https://github.com/NVIDIA/nvidia-container-toolkit) and "
        "configure it as a Docker runtime (`sudo nvidia-ctk runtime configure --runtime=docker && "
        "sudo systemctl restart docker`), then re-run `./potionui start-docker`."
    )
    if probe.which("nvidia-container-runtime"):
        return CheckResult(
            "NVIDIA_CONTAINER_TOOLKIT", Severity.OK, "nvidia-container-runtime found on PATH.", blocking=True
        )
    docker_path = probe.which("docker")
    if docker_path:
        try:
            result = probe.run([docker_path, "info"], timeout=10.0)
        except Exception:
            result = None
        if result is not None and result.returncode == 0 and "nvidia" in result.stdout.lower():
            return CheckResult(
                "NVIDIA_CONTAINER_TOOLKIT", Severity.OK,
                "nvidia runtime registered with the Docker daemon.", blocking=True,
            )
    return CheckResult(
        "NVIDIA_CONTAINER_TOOLKIT",
        Severity.ERROR,
        "nvidia-container-toolkit not detected (no nvidia-container-runtime on PATH, no nvidia "
        "runtime registered with Docker).",
        repair=repair,
        blocking=True,
    )


def run_docker_preflight(probe) -> list[CheckResult]:
    return [check_docker(probe), check_docker_compose(probe), check_nvidia_container_toolkit(probe)]


def run_doctor(
    probe, repo_root: Path, backend_port: int, frontend_port: int, no_gpu: bool = False
) -> list[CheckResult]:
    return [
        check_python(probe),
        check_venv(probe, repo_root),
        check_backend_deps(probe, repo_root, no_gpu=no_gpu),
        check_node(probe),
        check_npm(probe),
        check_frontend_deps(probe, repo_root),
        check_gpu(probe, no_gpu=no_gpu),
        check_disk(probe, repo_root),
        check_port(probe, backend_port, "PORT_8005", "backend"),
        check_port(probe, frontend_port, "PORT_3001", "frontend"),
        check_storage(probe, repo_root),
        check_env_file(probe, repo_root),
    ]


def resolve_worker_dir(repo_root: Path) -> Path:
    """`POTIONUI_WORKER_DIR` if set (worker.py's own env var, see
    `src/features/remote_execution/worker/config.py`), else `./worker_data`
    relative to the repo root — mirrors worker.py's own default exactly, so
    `worker doctor` checks the directory the worker will actually use."""
    raw = os.environ.get("POTIONUI_WORKER_DIR", DEFAULT_WORKER_DIR_NAME)
    path = Path(raw)
    return path if path.is_absolute() else repo_root / path


def run_worker_doctor(probe, repo_root: Path, port: int, worker_dir: Path, env: Optional[dict] = None) -> list[CheckResult]:
    return [
        check_python(probe),
        check_venv(probe, repo_root),
        check_backend_deps(probe, repo_root, no_gpu=False),
        check_gpu(probe, required=True),
        check_disk(probe, repo_root),
        check_port(probe, port, "PORT_8100", "worker"),
        check_worker_dir(probe, worker_dir),
        check_worker_token(repo_root, env=env),
    ]


# ---------------------------------------------------------------------------
# Doctor output formatting
# ---------------------------------------------------------------------------

_SEVERITY_LABEL = {Severity.OK: "OK", Severity.INFO: "INFO", Severity.WARNING: "WARN", Severity.ERROR: "FAIL"}


def print_doctor_report(results: list[CheckResult], as_json: bool) -> None:
    if as_json:
        print(json.dumps([r.to_row() for r in results], indent=2))
        return
    width_code = max((len(r.code) for r in results), default=0)
    for r in results:
        label = _SEVERITY_LABEL[r.severity]
        print(f"[{label:>4}] {r.code:<{width_code}}  {r.message}")
        if r.repair and r.severity in (Severity.WARNING, Severity.ERROR):
            print(f"         -> {r.repair}")
    errors = [r for r in results if r.severity == Severity.ERROR]
    warnings = [r for r in results if r.severity == Severity.WARNING]
    print()
    if errors:
        print(f"{len(errors)} blocking issue(s), {len(warnings)} warning(s). Fix the FAIL rows above before `./potionui start`.")
    elif warnings:
        print(f"No blocking issues. {len(warnings)} warning(s) — `./potionui start` handles the auto-fixable ones.")
    else:
        print("All checks passed.")


# ---------------------------------------------------------------------------
# Streamed subprocess runner (venv creation, pip install, npm install)
# ---------------------------------------------------------------------------

FAILURE_PATTERNS = [
    re.compile(r"Could not find a version that satisfies the requirement (\S+)"),
    re.compile(r"No matching distribution found for (\S+)"),
    re.compile(r"Failed building wheel for (\S+)"),
    re.compile(r"ERROR: Failed to build installable wheels for (\S+)"),
    re.compile(r"npm error notarget No matching version found for (\S+)"),
    re.compile(r"npm ERR! 404\s+'?(\S+?)'?\s+is not in"),
    re.compile(r"npm error code (\S+)"),
]


def guess_failed_requirement(text: str) -> Optional[str]:
    for pattern in FAILURE_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def run_streamed(cmd: list[str], cwd: Path, env: dict, label: str) -> bool:
    """Run cmd with output streamed live to stdout. Returns True on success.
    On failure, prints the tail of output and a best-effort guess at the
    offending package/requirement — never swallows the failure."""
    print(f"$ {' '.join(cmd)}  (cwd={cwd})")
    tail: collections.deque = collections.deque(maxlen=60)
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="")
        tail.append(line)
    proc.wait()
    if proc.returncode != 0:
        print(f"\n{label} failed (exit code {proc.returncode}).")
        culprit = guess_failed_requirement("".join(tail))
        if culprit:
            print(f"Likely cause: {culprit}")
        return False
    return True


# ---------------------------------------------------------------------------
# Process supervision
# ---------------------------------------------------------------------------

def spawn_process(cmd: list[str], cwd: Path, env: dict, log_path: Path) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "ab", buffering=0)
    try:
        return subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # own process group -> we can kill the whole tree (npm spawns vite as a child)
        )
    finally:
        log_file.close()  # child holds its own dup'd fd


def start_supervised(name: str, cmd: list[str], cwd: Path, env: dict, port: int, spawn=spawn_process) -> dict:
    log_path = LOG_DIR / f"{name}.log"
    popen = spawn(cmd, cwd, env, log_path)
    return {
        "pid": popen.pid,
        "port": port,
        "cmd": cmd,
        "log": str(log_path),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours
    return True


def stop_process(pid: int, timeout: float = 10.0, sleeper: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> bool:
    """Terminate the process group led by `pid`. Returns True once it's confirmed gone."""
    if not pid_alive(pid):
        return True
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = clock() + timeout
    while clock() < deadline:
        if not pid_alive(pid):
            return True
        sleeper(0.2)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)
    return not pid_alive(pid)


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_for_ready(
    check_fn: Callable[[], bool],
    timeout: float,
    interval: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = clock() + timeout
    while clock() < deadline:
        if check_fn():
            return True
        sleeper(interval)
    return check_fn()


# ---------------------------------------------------------------------------
# State file (.runtime/state.json)
# ---------------------------------------------------------------------------

def load_state(state_file: Path = STATE_FILE) -> Optional[dict]:
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_state(state: dict, state_file: Path = STATE_FILE) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))


def clear_state(state_file: Path = STATE_FILE) -> None:
    with contextlib.suppress(FileNotFoundError):
        state_file.unlink()


def install_profile_active(
    marker_file: Path = INSTALL_PROFILE_FILE, legacy_marker_file: Path = NO_GPU_PROFILE_FILE
) -> Optional[str]:
    """The install profile a previous explicit `./potionui start --profile ...`
    (or its `--no-gpu` alias) persisted, so a later plain `start`/`doctor`
    reuses it instead of drifting back to the `local` default. Falls back to
    the legacy 0.0.2 `.runtime/no_gpu_profile` marker, which always means
    `remote`. Returns None when neither marker is present."""
    if marker_file.exists():
        try:
            data = json.loads(marker_file.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        profile = data.get("profile")
        if profile in INSTALL_PROFILES:
            return profile
    if legacy_marker_file.exists():
        return "remote"
    return None


def mark_install_profile(profile: str, marker_file: Path = INSTALL_PROFILE_FILE) -> None:
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text(
        json.dumps({"profile": profile, "marked_at": datetime.now(timezone.utc).isoformat()}, indent=2)
    )


def resolve_install_profile(args) -> tuple[str, bool]:
    """The install profile driving this invocation, and whether it was given
    explicitly on this invocation (`--profile`, or its `--no-gpu` alias)
    rather than picked up from a previous run's persisted choice or the
    `local` default. Callers persist the profile only when the second value
    is True, so a plain `./potionui start` never overwrites an earlier
    explicit choice."""
    explicit_profile = getattr(args, "profile", None)
    if explicit_profile:
        return explicit_profile, True
    if getattr(args, "no_gpu", False):
        return "remote", True
    persisted = install_profile_active()
    if persisted:
        return persisted, False
    return "local", False


def backend_pip_install_args_for_profile(profile: str, repo_root: Path) -> list[str]:
    if profile == "remote":
        return backend_pip_install_args_no_gpu()
    return backend_pip_install_args(repo_root)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def _print_status_lines(state: dict) -> None:
    for name in ("backend", "frontend"):
        info = state.get(name)
        if info:
            alive = "running" if pid_alive(info["pid"]) else "dead (stale)"
            print(f"  {name}: pid={info['pid']} port={info['port']} {alive} (log: {info['log']})")


def _print_canonical_url(port: int) -> None:
    print(f"\nOpen: http://localhost:{port}")


def _canonical_port(state: dict) -> int:
    """The port to show the user: the frontend's if one is running (two-process
    dev mode), otherwise the backend's (single-process mode - it serves the
    built SPA itself, see `frontend_build_is_fresh`)."""
    frontend = state.get("frontend")
    return frontend["port"] if frontend else state["backend"]["port"]


# Relative to the repo root, matching the backend's default
# `file_storage_directory` setting ("storage"). See
# src/platform/security/claim_token.py — the backend writes this file while
# the instance is unclaimed and deletes it once claimed; the CLI only peeks.
CLAIM_TOKEN_RELATIVE_PATH = Path("storage") / "setup_claim_token"


def _read_claim_token(repo_root: Path) -> Optional[str]:
    """Read-only peek at the one-time instance-claim token, if present.

    Never creates or validates the token — that's the backend's job. Returns
    None whenever the file is absent, unreadable, or empty (already claimed,
    non-default storage directory, etc.)."""
    try:
        token = (repo_root / CLAIM_TOKEN_RELATIVE_PATH).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def _print_claim_token_hint(repo_root: Path, port: int) -> None:
    token = _read_claim_token(repo_root)
    if token:
        print(
            f"First-time setup: open http://localhost:{port} and "
            f"use this claim code if asked: {token}"
        )


def _print_hybrid_profile_hint(profile: str) -> None:
    if profile != "hybrid":
        return
    print(
        "\nHybrid profile: add a remote worker any time from Admin -> Backends -> Add "
        "backend -> Native (Remote Worker) (its Infrastructure tab can provision one for you)."
    )


def frontend_build_is_fresh(repo_root: Path) -> bool:
    """True when `frontend/build/index.html` exists and is at least as new as
    every file under `frontend/src` — i.e. `./potionui build` produced the
    static build after the last source edit, so `start` can serve it directly
    instead of spawning Vite. A missing `frontend/src` can't stage anything
    newer than the build, so it doesn't invalidate one."""
    build_index = repo_root / "frontend" / "build" / "index.html"
    if not build_index.is_file():
        return False
    src_dir = repo_root / "frontend" / "src"
    if not src_dir.is_dir():
        return True
    build_mtime = build_index.stat().st_mtime
    return all(p.stat().st_mtime <= build_mtime for p in src_dir.rglob("*") if p.is_file())


def cmd_doctor(args) -> int:
    probe = RealProbe()
    profile, _explicit = resolve_install_profile(args)
    no_gpu = profile == "remote"
    results = run_doctor(probe, REPO_ROOT, args.backend_port, args.frontend_port, no_gpu=no_gpu)
    print_doctor_report(results, args.json)
    return 1 if any(r.severity == Severity.ERROR for r in results) else 0


def _existing_running_and_healthy(state: dict) -> bool:
    backend = state.get("backend")
    if not backend or not pid_alive(backend["pid"]) or not http_ok(f"http://127.0.0.1:{backend['port']}/health"):
        return False
    frontend = state.get("frontend")
    if frontend is None:
        return True  # single-process mode: no Vite dev server to check
    return pid_alive(frontend["pid"]) and http_ok(f"http://{FRONTEND_BIND_HOST}:{frontend['port']}/")


def _install_signal_forwarding(state: dict) -> None:
    def _handler(signum, _frame):
        print(f"\nReceived signal {signum}, stopping PotionUI...")
        for name in ("frontend", "backend"):
            info = state.get(name)
            if info:
                stop_process(info["pid"])
        clear_state()
        sys.exit(1)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _fail_start(name: str, info: dict, state: dict) -> int:
    print(f"\n{name} did not become ready within the timeout.")
    log_path = Path(info["log"])
    if log_path.exists():
        print(f"Last lines of {log_path}:")
        for line in log_path.read_text(errors="replace").splitlines()[-30:]:
            print(f"  {line}")
    code = "BACKEND_START_TIMEOUT" if name == "backend" else "FRONTEND_START_TIMEOUT"
    print(f"\ndoctor code: {code}")
    print("Stopping partially-started processes...")
    for n in ("frontend", "backend"):
        if state.get(n):
            stop_process(state[n]["pid"])
    clear_state()
    return 1


def cmd_start(args) -> int:
    probe = RealProbe()
    profile, explicit_profile = resolve_install_profile(args)
    no_gpu = profile == "remote"

    existing = load_state()
    if existing:
        if _existing_running_and_healthy(existing):
            print("PotionUI is already running.")
            _print_status_lines(existing)
            _print_canonical_url(_canonical_port(existing))
            return 0
        still_alive = any(
            pid_alive(existing[n]["pid"]) for n in ("backend", "frontend") if existing.get(n)
        )
        if still_alive:
            print("A stale or partially-running PotionUI instance was found (process alive but not responding).")
            print("Run `./potionui stop`, then `./potionui start` again.")
            return 1
        clear_state()

    single_process = not args.dev and frontend_build_is_fresh(REPO_ROOT)
    if args.dev:
        print("--dev: forcing the two-process flow (backend + Vite dev server).")
    elif single_process:
        print("frontend/build is up to date — starting the backend only (single-process mode).")
        print("(pass --dev to use the Vite dev server instead)")

    results = run_doctor(probe, REPO_ROOT, args.backend_port, args.frontend_port, no_gpu=no_gpu)
    if single_process:
        # No Vite dev server means nothing binds the frontend port — checking
        # it free would block a start for no reason.
        results = [r for r in results if r.code != "PORT_3001"]
    blocking_failures = [r for r in results if r.blocking and r.severity == Severity.ERROR]
    if blocking_failures:
        print("Cannot start — blocking issue(s) found:\n")
        print_doctor_report(results, as_json=False)
        return 1

    if explicit_profile and install_profile_active() != profile:
        mark_install_profile(profile)

    if profile == "remote":
        print(
            "Install profile: remote (CPU-only torch, no CUDA libraries, no xformers) — "
            "for hosts with no NVIDIA GPU that dispatch to a remote worker."
        )
    elif profile == "hybrid":
        print("Install profile: hybrid (full CUDA stack — local generation, with room to add remote workers).")
    else:
        print("Install profile: local (full CUDA stack, CUDA-pinned via constraints.txt).")

    python_found = probe_python_candidates(probe)
    assert python_found is not None  # doctor already confirmed PY312 passed
    python_bin, _ = python_found

    venv_python = REPO_ROOT / "venv" / "bin" / "python"
    if not venv_python.exists():
        print(f"Creating virtualenv with {python_bin} ...")
        if not run_streamed([python_bin, "-m", "venv", "venv"], REPO_ROOT, os.environ.copy(), "venv creation"):
            return 1

    try:
        deps_ok = probe.run([str(venv_python), "-c", BACKEND_MARKER_IMPORT], timeout=20.0).returncode == 0
    except Exception:
        deps_ok = False
    if not deps_ok:
        print("Installing backend dependencies (this can take a while)...")
        pip_bin = REPO_ROOT / "venv" / "bin" / "pip"
        pip_cmd = [str(pip_bin), *backend_pip_install_args_for_profile(profile, REPO_ROOT)]
        if not run_streamed(pip_cmd, REPO_ROOT, os.environ.copy(), "pip install"):
            print("Backend dependency install failed. Fix the issue above and re-run `./potionui start`.")
            return 1

    backend_env = os.environ.copy()

    backend_cmd = [
        str(venv_python), "-m", "uvicorn", "api:app",
        "--host", "127.0.0.1", "--port", str(args.backend_port),
        "--workers", "1", "--log-level", "info",
        "--limit-concurrency", "1000", "--limit-max-requests", "10000",
        "--timeout-keep-alive", "30", "--timeout-graceful-shutdown", "30",
    ]

    if single_process:
        # The browser talks to the backend directly (same origin serves both
        # the SPA and /api), so there's no cross-origin frontend to allow.
        print("\nStarting backend...")
        backend_info = start_supervised("backend", backend_cmd, REPO_ROOT, backend_env, args.backend_port)
        state = {"backend": backend_info}
        save_state(state)
        _install_signal_forwarding(state)

        print("Waiting for backend to become ready...")
        backend_url = f"http://127.0.0.1:{args.backend_port}/health"
        if not wait_for_ready(lambda: http_ok(backend_url), timeout=args.timeout):
            return _fail_start("backend", backend_info, state)

        print("\nPotionUI is up.")
        _print_canonical_url(args.backend_port)
        _print_claim_token_hint(REPO_ROOT, args.backend_port)
        _print_hybrid_profile_hint(profile)
        return 0

    vite_bin = REPO_ROOT / "frontend" / "node_modules" / ".bin" / "vite"
    if not vite_bin.exists():
        print("Installing frontend dependencies...")
        npm_bin = probe.which("npm") or "npm"
        if not run_streamed([npm_bin, "install"], REPO_ROOT / "frontend", os.environ.copy(), "npm install"):
            print("Frontend dependency install failed. Fix the issue above and re-run `./potionui start`.")
            return 1

    backend_env["ALLOWED_ORIGINS"] = f"http://localhost:{args.frontend_port},http://127.0.0.1:{args.frontend_port}"

    frontend_env = os.environ.copy()
    frontend_env["BACKEND_PORT"] = str(args.backend_port)
    frontend_env["FRONTEND_PORT"] = str(args.frontend_port)
    frontend_env["VITE_HOST"] = FRONTEND_BIND_HOST
    frontend_env["POTIONUI_PROFILE"] = "1"

    npm_bin = probe.which("npm") or "npm"
    frontend_cmd = [npm_bin, "run", "dev"]

    print("\nStarting backend and frontend...")
    backend_info = start_supervised("backend", backend_cmd, REPO_ROOT, backend_env, args.backend_port)
    frontend_info = start_supervised("frontend", frontend_cmd, REPO_ROOT / "frontend", frontend_env, args.frontend_port)
    state = {"backend": backend_info, "frontend": frontend_info}
    save_state(state)
    _install_signal_forwarding(state)

    print("Waiting for backend and frontend to become ready...")
    backend_url = f"http://127.0.0.1:{args.backend_port}/health"
    frontend_url = f"http://{FRONTEND_BIND_HOST}:{args.frontend_port}/"

    if not wait_for_ready(lambda: http_ok(backend_url), timeout=args.timeout):
        return _fail_start("backend", backend_info, state)
    if not wait_for_ready(lambda: http_ok(frontend_url), timeout=args.timeout):
        return _fail_start("frontend", frontend_info, state)

    print("\nPotionUI is up.")
    _print_canonical_url(args.frontend_port)
    _print_claim_token_hint(REPO_ROOT, args.frontend_port)
    _print_hybrid_profile_hint(profile)
    return 0


def cmd_build(args) -> int:
    """Run `npm run build` in frontend/ (installing frontend deps first if
    needed) so a later `./potionui start` can serve frontend/build directly
    instead of spawning the Vite dev server."""
    probe = RealProbe()
    npm_bin = probe.which("npm")
    if not npm_bin:
        print("npm not found on PATH. Install Node.js 18+ and re-run `./potionui build`.")
        return 1

    frontend_dir = REPO_ROOT / "frontend"
    vite_bin = frontend_dir / "node_modules" / ".bin" / "vite"
    if not vite_bin.exists():
        print("Installing frontend dependencies...")
        if not run_streamed([npm_bin, "install"], frontend_dir, os.environ.copy(), "npm install"):
            print("Frontend dependency install failed. Fix the issue above and re-run `./potionui build`.")
            return 1

    print("Building the frontend (npm run build)...")
    if not run_streamed([npm_bin, "run", "build"], frontend_dir, os.environ.copy(), "npm run build"):
        print("Frontend build failed. Fix the issue above and re-run `./potionui build`.")
        return 1

    print(
        "\nBuild complete: frontend/build is ready. `./potionui start` will now serve it "
        "from the backend as a single process (pass --dev to use the Vite dev server instead)."
    )
    return 0


def cmd_status(args) -> int:
    state = load_state()
    if not state:
        print("PotionUI is not running (no state file).")
        return 1
    _print_status_lines(state)
    all_alive = all(pid_alive(state[n]["pid"]) for n in ("backend", "frontend") if state.get(n))
    return 0 if all_alive else 1


def cmd_stop(args) -> int:
    state = load_state()
    if not state:
        print("PotionUI is not running.")
        return 0
    ok = True
    for name in ("frontend", "backend"):
        info = state.get(name)
        if not info:
            continue
        print(f"Stopping {name} (pid {info['pid']})...")
        if stop_process(info["pid"]):
            print(f"  {name} stopped.")
        else:
            print(f"  warning: could not confirm {name} stopped.")
            ok = False
    clear_state()
    return 0 if ok else 1


def run_foreground(cmd: list[str], cwd: Path, env: Optional[dict] = None) -> int:
    """Run cmd attached to the current terminal (inherited stdio), so a long
    `docker compose up --build` or `python worker.py` streams live and Ctrl-C
    reaches it directly. `env=None` inherits the current process environment,
    same as before this parameter existed."""
    return subprocess.call(cmd, cwd=str(cwd), env=env)


def cmd_start_docker(args) -> int:
    probe = RealProbe()
    results = run_docker_preflight(probe)
    print_doctor_report(results, as_json=False)
    if any(r.severity == Severity.ERROR for r in results):
        print("\nCannot start the Docker rig-simulation harness — fix the FAIL row(s) above.")
        return 1

    compose_file = REPO_ROOT / DOCKER_COMPOSE_FILE
    cmd = ["docker", "compose", "-f", str(compose_file), "up", "--build", *args.compose_args]
    print(f"\n$ {' '.join(cmd)}")
    return run_foreground(cmd, REPO_ROOT)


# ---------------------------------------------------------------------------
# `worker` preset: doctor + start for a standalone Remote Native worker
# (worker.py) on a GPU box that serves another PotionUI instance.
# ---------------------------------------------------------------------------

def cmd_worker_doctor(args) -> int:
    probe = RealProbe()
    worker_dir = resolve_worker_dir(REPO_ROOT)
    results = run_worker_doctor(probe, REPO_ROOT, args.port, worker_dir)
    print_doctor_report(results, args.json)
    return 1 if any(r.severity == Severity.ERROR for r in results) else 0


def cmd_worker_start(args) -> int:
    probe = RealProbe()
    worker_dir = resolve_worker_dir(REPO_ROOT)
    results = run_worker_doctor(probe, REPO_ROOT, args.port, worker_dir)
    blocking_failures = [r for r in results if r.blocking and r.severity == Severity.ERROR]
    if blocking_failures:
        print("Cannot start the worker — blocking issue(s) found:\n")
        print_doctor_report(results, as_json=False)
        return 1

    python_found = probe_python_candidates(probe)
    assert python_found is not None  # doctor already confirmed PY312 passed
    python_bin, _ = python_found

    venv_python = REPO_ROOT / "venv" / "bin" / "python"
    if not venv_python.exists():
        print(f"Creating virtualenv with {python_bin} ...")
        if not run_streamed([python_bin, "-m", "venv", "venv"], REPO_ROOT, os.environ.copy(), "venv creation"):
            return 1

    try:
        deps_ok = probe.run([str(venv_python), "-c", BACKEND_MARKER_IMPORT], timeout=20.0).returncode == 0
    except Exception:
        deps_ok = False
    if not deps_ok:
        print("Installing worker dependencies (full CUDA stack, this can take a while)...")
        pip_bin = REPO_ROOT / "venv" / "bin" / "pip"
        pip_cmd = [str(pip_bin), *backend_pip_install_args(REPO_ROOT)]
        if not run_streamed(pip_cmd, REPO_ROOT, os.environ.copy(), "pip install"):
            print("Worker dependency install failed. Fix the issue above and re-run `./potionui worker start`.")
            return 1

    worker_env = os.environ.copy()
    worker_env["POTIONUI_WORKER_HOST"] = args.host
    worker_env["POTIONUI_WORKER_PORT"] = str(args.port)

    worker_cmd = [str(venv_python), "worker.py"]
    print(f"\nStarting the Remote Native worker on http://{args.host}:{args.port} ...")
    print(
        "Put the same POTIONUI_WORKER_TOKEN into the Native (Remote Worker) backend on the "
        "PotionUI instance that will dispatch to this worker (Admin -> Backends -> Add backend "
        "-> Native (Remote Worker))."
    )
    return run_foreground(worker_cmd, REPO_ROOT, env=worker_env)


def cmd_worker(args) -> int:
    handlers = {"doctor": cmd_worker_doctor, "start": cmd_worker_start}
    handler = handlers.get(args.worker_command)
    if handler is None:
        return 1
    return handler(args)


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="potionui", description="PotionUI bootstrap CLI.")
    parser.add_argument(
        "--backend-port", type=int,
        default=int(os.environ.get("BACKEND_PORT", DEFAULT_BACKEND_PORT)),
        help=f"Backend port (default {DEFAULT_BACKEND_PORT}, env BACKEND_PORT).",
    )
    parser.add_argument(
        "--frontend-port", type=int,
        default=int(os.environ.get("FRONTEND_PORT", DEFAULT_FRONTEND_PORT)),
        help=f"Frontend port (default {DEFAULT_FRONTEND_PORT}, env FRONTEND_PORT).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    profile_help = (
        "Install preset: `local` (full CUDA stack, this box has the GPU), `hybrid` (same "
        "dependency profile as local, plus a readiness hint for adding a remote worker later), "
        "or `remote` (CPU-only torch, no CUDA libraries — this box has no GPU and dispatches to "
        "a remote worker; not a CPU-generation mode). Default: the previously persisted choice, "
        "or `local`. Persisted at .runtime/install_profile."
    )
    no_gpu_help = (
        "Alias for --profile remote. A documented public flag of the released 0.0.2 — kept for "
        "compatibility; prefer --profile remote."
    )

    doctor_p = sub.add_parser("doctor", help="Run environment checks.")
    doctor_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON rows.")
    doctor_p.add_argument("--profile", choices=INSTALL_PROFILES, default=None, help=profile_help)
    doctor_p.add_argument("--no-gpu", action="store_true", help=no_gpu_help)

    start_p = sub.add_parser(
        "start",
        help=(
            "Launch PotionUI. Single-process (backend only, serving frontend/build) if `./potionui "
            "build` has run and nothing in frontend/src changed since; otherwise backend + Vite dev "
            "server, same as before."
        ),
    )
    start_p.add_argument("--timeout", type=float, default=DEFAULT_START_TIMEOUT, help="Seconds to wait for readiness.")
    start_p.add_argument("--profile", choices=INSTALL_PROFILES, default=None, help=profile_help)
    start_p.add_argument("--no-gpu", action="store_true", help=no_gpu_help)
    start_p.add_argument(
        "--dev", action="store_true",
        help="Force the two-process dev flow (backend + Vite dev server) even if frontend/build is up to date.",
    )

    sub.add_parser(
        "build",
        help="Build the frontend once (npm run build) so `start` can serve it from the backend as a single process.",
    )
    sub.add_parser("status", help="Show whether PotionUI is running.")
    sub.add_parser("stop", help="Stop a running PotionUI instance.")

    start_docker_p = sub.add_parser(
        "start-docker",
        help=(
            "Preflight + launch the containerized dev/simulation harness (docker compose). "
            "Requires an NVIDIA GPU and nvidia-container-toolkit."
        ),
    )
    start_docker_p.add_argument(
        "compose_args", nargs=argparse.REMAINDER,
        help="Extra arguments passed through to `docker compose up` (e.g. a service: rig-mid, rig-small).",
    )

    worker_p = sub.add_parser(
        "worker",
        help=(
            "The `worker` install preset: doctor/start a standalone Remote Native worker "
            "(worker.py) on a GPU box that serves another PotionUI instance."
        ),
    )
    worker_sub = worker_p.add_subparsers(dest="worker_command", required=True)

    worker_doctor_p = worker_sub.add_parser("doctor", help="Run environment checks for the worker preset.")
    worker_doctor_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON rows.")
    worker_doctor_p.add_argument(
        "--port", type=int, default=DEFAULT_WORKER_PORT,
        help=f"Worker port to check for availability (default {DEFAULT_WORKER_PORT}).",
    )

    worker_start_p = worker_sub.add_parser(
        "start", help="Run the worker preset's blocking checks, install if needed, then exec `python worker.py`."
    )
    worker_start_p.add_argument(
        "--host", default=DEFAULT_WORKER_HOST, help=f"Bind host (default {DEFAULT_WORKER_HOST})."
    )
    worker_start_p.add_argument(
        "--port", type=int, default=DEFAULT_WORKER_PORT, help=f"Bind port (default {DEFAULT_WORKER_PORT})."
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "doctor": cmd_doctor,
        "start": cmd_start,
        "build": cmd_build,
        "status": cmd_status,
        "stop": cmd_stop,
        "start-docker": cmd_start_docker,
        "worker": cmd_worker,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
