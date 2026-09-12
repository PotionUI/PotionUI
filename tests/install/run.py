#!/usr/bin/env python3
"""Install matrix harness for the `./potionui` bootstrap CLI.

Proves `./potionui start` works end to end for each install profile
(`local`, `hybrid`, `remote`) plus the `worker` preset, on a throwaway
checkout — never the maintainer's working tree, venv, or ports 7681/7680.

Per profile:

    materialize -> doctor --json -> start -> /health -> claim token file
    -> register owner -> list presets -> status -> stop -> process gone

`worker` follows its own shape: `worker doctor --json` -> `worker start`
(backgrounded, since it execs into the foreground worker process) ->
/health -> stop. A blocking doctor row (most commonly GPU, on a box with
no NVIDIA card) ends the run there without installing anything further -
`worker start` re-runs the same doctor check before touching the network,
so a GPU-less host can never be dragged into a CUDA install by this harness.

Usage:

    python tests/install/run.py                          # --profiles remote
    python tests/install/run.py --profiles local,hybrid,remote,worker
    python tests/install/run.py --from-dir . --with-dev
    python tests/install/run.py --reuse-venv ./venv --profiles local
    python tests/install/run.py --json report.json --keep
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]


def is_windows() -> bool:
    return os.name == "nt"


def potionui_launcher(checkout_dir: Path) -> list:
    """The `./potionui <args>` invocation for the current platform.

    On Windows this routes through `cmd /c` on an absolute path into
    `checkout_dir`: CreateProcess (what subprocess uses with shell=False)
    can only launch PE binaries, not a .cmd/.bat file directly - it fails
    with WinError 193 unless a shell resolves the batch-file association,
    which is why an interactive `run:` step in CI (itself a shell) can
    invoke `potionui.cmd` directly while this subprocess call cannot. The
    absolute path also sidesteps a second gotcha: CreateProcess resolves a
    path-less relative filename against the *parent* process's current
    directory, not the child's assigned `cwd=`, so a bare name here would
    silently search the wrong tree. POSIX exec has neither gotcha (a
    relative "./potionui" resolves against the child's own post-fork cwd,
    and the kernel executes the interpreter named on its shebang line
    directly), so it keeps the existing relative, shell-less form."""
    if is_windows():
        return ["cmd", "/c", str(checkout_dir / "potionui.cmd")]
    return ["./potionui"]

try:
    import requests
except ImportError:  # pragma: no cover - requests ships in requirements.txt
    print("The 'requests' package is required (pip install -r requirements.txt).", file=sys.stderr)
    raise

DEFAULT_ROOT = Path(tempfile.gettempdir()) / "potionui-install-matrix"
DEFAULT_PORT_START = 8055
CORE_PROFILES = ("local", "hybrid", "remote")
ALL_PROFILES = CORE_PROFILES + ("worker",)
DEFAULT_START_TIMEOUT = 180.0
DEFAULT_INSTALL_TIMEOUT = 1800.0  # ceiling on top of --timeout for pip/npm installs
CLI_INVOKE_TIMEOUT = 60.0  # doctor / status / stop are cheap, non-installing calls

# Directory names never copied into a throwaway checkout. `frontend/build` is
# deliberately NOT excluded - `--from-dir` keeps it when present, so
# single-process start can be exercised; `--from-git` never has it
# (frontend/build is gitignored).
#
# node_modules/__pycache__/.git are excluded at ANY depth (frontend/
# node_modules, content/plugins/node_modules, __pycache__ throughout src/).
# venv/storage/.runtime/models/outputs are excluded ONLY at the checkout
# root - `src/features/models/` is a real feature package, not the
# top-level `./models` downloaded-checkpoint directory, and must be copied.
ANY_DEPTH_EXCLUDED_NAMES = frozenset({"node_modules", ".git", "__pycache__"})
ROOT_ONLY_EXCLUDED_NAMES = frozenset({"venv", "storage", ".runtime", "models", "outputs"})


def make_copytree_ignore(src_root) -> Callable[[str, list], set]:
    """Build a `shutil.copytree(ignore=...)` callback scoped to `src_root`:
    ANY_DEPTH_EXCLUDED_NAMES are dropped everywhere, ROOT_ONLY_EXCLUDED_NAMES
    only where they sit directly under `src_root`."""
    root_str = str(Path(src_root).resolve())

    def ignore(dirpath: str, names: list) -> set:
        ignored = {n for n in names if n in ANY_DEPTH_EXCLUDED_NAMES}
        if str(Path(dirpath).resolve()) == root_str:
            ignored |= {n for n in names if n in ROOT_ONLY_EXCLUDED_NAMES}
        return ignored

    return ignore


class PhaseError(Exception):
    """Raised by a phase function to end the run with a reason, without a
    traceback - the harness turns this into a FAIL row, not a crash."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested directly in test_run.py)
# ---------------------------------------------------------------------------

def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_free_ports(count: int, start: int = DEFAULT_PORT_START, is_free: Callable[[int], bool] = port_is_free) -> list:
    """Return `count` distinct ports >= start that `is_free` currently
    reports free, scanning upward. Pure aside from the injected `is_free`."""
    ports: list = []
    port = start
    while len(ports) < count:
        if is_free(port):
            ports.append(port)
        port += 1
    return ports


@dataclass
class Phase:
    name: str
    fn: Callable[[], None]


@dataclass
class PhaseRunResult:
    phase_reached: str
    passed: bool
    reason: str
    seconds: float
    phase_seconds: dict = field(default_factory=dict)


def run_phases(phases: list, clock: Callable[[], float] = time.monotonic) -> PhaseRunResult:
    """Run `phases` in order, stopping at the first one that raises. Returns
    the name of the last phase attempted either way, so a caller can tell
    "everything passed" from "failed partway through phase X" without
    inspecting exceptions itself."""
    start = clock()
    phase_seconds: dict = {}
    last_name = phases[0].name if phases else ""
    for phase in phases:
        t0 = clock()
        last_name = phase.name
        try:
            phase.fn()
        except PhaseError as exc:
            phase_seconds[phase.name] = clock() - t0
            return PhaseRunResult(phase.name, False, str(exc), clock() - start, phase_seconds)
        except Exception as exc:  # unexpected failure - still a row, not a crash
            phase_seconds[phase.name] = clock() - t0
            return PhaseRunResult(phase.name, False, f"unexpected error: {exc!r}", clock() - start, phase_seconds)
        phase_seconds[phase.name] = clock() - t0
    return PhaseRunResult(last_name, True, "ok", clock() - start, phase_seconds)


@dataclass
class ProfileReport:
    profile: str
    phase_reached: str
    passed: bool
    reason: str
    seconds: float
    checkout_dir: str
    log_dir: str

    def to_row(self) -> dict:
        return {
            "profile": self.profile,
            "phase_reached": self.phase_reached,
            "passed": self.passed,
            "reason": self.reason,
            "seconds": round(self.seconds, 1),
            "checkout_dir": self.checkout_dir,
            "log_dir": self.log_dir,
        }


def print_table(reports: list) -> None:
    headers = ("PROFILE", "PHASE", "SECONDS", "RESULT", "REASON")
    rows = [
        (
            r.profile,
            r.phase_reached,
            f"{r.seconds:.1f}",
            "PASS" if r.passed else "FAIL",
            "" if r.passed else r.reason,
        )
        for r in reports
    ]
    widths = [max(len(h), *(len(row[i]) for row in rows)) if rows else len(h) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))


# ---------------------------------------------------------------------------
# Checkout materialization
# ---------------------------------------------------------------------------

def materialize_from_git(ref: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(
        ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", ref], stdout=subprocess.PIPE
    )
    extract = subprocess.Popen(["tar", "-x", "-C", str(dest)], stdin=archive.stdout)
    assert archive.stdout is not None
    archive.stdout.close()
    extract.communicate()
    archive.wait()
    if archive.returncode != 0 or extract.returncode != 0:
        raise PhaseError(f"git archive/tar extract failed (git={archive.returncode}, tar={extract.returncode})")


def materialize_from_dir(src: Path, dest: Path) -> None:
    shutil.copytree(src, dest, ignore=make_copytree_ignore(src), symlinks=True, dirs_exist_ok=True)


# ---------------------------------------------------------------------------
# CLI invocation helpers
# ---------------------------------------------------------------------------

def run_cli_capture(checkout_dir: Path, cli_args: list, backend_port: int, frontend_port: int,
                     env: dict, log_path: Path, timeout: float) -> int:
    """Run `./potionui <cli_args>` to completion, streaming stdout+stderr to
    log_path. Global --backend-port/--frontend-port must precede the
    subcommand (argparse: they belong to the parent parser)."""
    cmd = [*potionui_launcher(checkout_dir), "--backend-port", str(backend_port), "--frontend-port", str(frontend_port), *cli_args]
    with open(log_path, "wb") as fh:
        fh.write((" ".join(cmd) + "\n").encode())
        fh.flush()
        proc = subprocess.run(cmd, cwd=str(checkout_dir), env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode


def http_get_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        resp = requests.get(url, timeout=timeout)
        return 200 <= resp.status_code < 400
    except requests.RequestException:
        return False


def wait_for(check_fn: Callable[[], bool], timeout: float, interval: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_fn():
            return True
        time.sleep(interval)
    return check_fn()


def read_pids_from_state(checkout_dir: Path) -> list:
    state_path = checkout_dir / ".runtime" / "state.json"
    if not state_path.exists():
        return []
    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    pids = []
    for name in ("backend", "frontend"):
        info = state.get(name)
        if info and "pid" in info:
            pids.append(info["pid"])
    return pids


def _pid_alive_windows(pid: int) -> bool:
    """`os.kill(pid, 0)` does not probe liveness on Windows - CPython maps a
    signal number it doesn't special-case there to TerminateProcess, so
    passing 0 would actually kill the target instead of checking it. This
    harness verifies `./potionui stop` independently of the CLI's own
    process bookkeeping, so it reimplements the OpenProcess /
    GetExitCodeProcess probe here rather than importing scripts.potionui_cli."""
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid: int) -> bool:
    if is_windows():
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------------------
# Per-profile phase construction
# ---------------------------------------------------------------------------

def build_core_phases(profile: str, checkout_dir: Path, log_dir: Path, ports: tuple,
                       env: dict, args) -> list:
    backend_port, frontend_port = ports

    def p_materialize():
        if checkout_dir.exists():
            shutil.rmtree(checkout_dir)
        if args.from_dir is not None:
            materialize_from_dir(Path(args.from_dir).resolve(), checkout_dir)
        else:
            materialize_from_git(args.from_git, checkout_dir)
        if not is_windows():
            (checkout_dir / "potionui").chmod(0o755)  # potionui.cmd needs no execute bit on Windows
        if args.reuse_venv:
            venv_src = Path(args.reuse_venv).resolve()
            if not venv_src.is_dir():
                raise PhaseError(f"--reuse-venv path does not exist or is not a directory: {venv_src}")
            venv_dest = checkout_dir / "venv"
            if venv_dest.exists():
                shutil.rmtree(venv_dest)
            venv_dest.symlink_to(venv_src, target_is_directory=True)

    def p_doctor():
        log_path = log_dir / "doctor.json"
        code = run_cli_capture(
            checkout_dir, ["doctor", "--json", "--profile", profile], backend_port, frontend_port,
            env, log_path, timeout=CLI_INVOKE_TIMEOUT,
        )
        try:
            lines = log_path.read_text().splitlines()
            json_text = "\n".join(lines[1:])  # drop the echoed command line
            rows = json.loads(json_text)
        except (OSError, json.JSONDecodeError) as exc:
            raise PhaseError(f"doctor --json produced unparseable output ({exc}); see {log_path}") from exc
        errors = [r for r in rows if r.get("severity") == "error"]
        if profile == "remote":
            gpu_errors = [r for r in errors if r.get("code") == "GPU"]
            if gpu_errors:
                raise PhaseError(f"remote profile's GPU doctor row must not block: {gpu_errors}")
        if errors:
            raise PhaseError(f"doctor reported blocking error(s): {[r['code'] for r in errors]} (exit {code}, see {log_path})")

    def p_start():
        log_path = log_dir / "start.log"
        cli_args = ["start", "--profile", profile, "--timeout", str(args.timeout)]
        if args.with_dev:
            cli_args.append("--dev")
        subprocess_timeout = args.timeout + args.install_timeout
        code = run_cli_capture(checkout_dir, cli_args, backend_port, frontend_port, env, log_path, timeout=subprocess_timeout)
        if code != 0:
            raise PhaseError(f"`./potionui start` exited {code} - see {log_path}")

    def p_health():
        url = f"http://127.0.0.1:{backend_port}/health"
        if not http_get_ok(url):
            raise PhaseError(f"{url} did not return 2xx/3xx after start reported success")

    def p_claim_token():
        token_path = checkout_dir / "storage" / "setup_claim_token"
        if not token_path.exists():
            raise PhaseError(f"expected an unclaimed instance's claim token at {token_path}")

    def p_owner_and_presets():
        base = f"http://127.0.0.1:{backend_port}"
        username = "install-matrix-owner"
        password = f"install-matrix-{secrets.token_urlsafe(12)}"
        # example.com (RFC 2606) - .invalid/.test are rejected outright by
        # the backend's email-validator as "special-use or reserved".
        resp = requests.post(
            f"{base}/api/auth/register",
            json={"username": username, "email": f"{username}@example.com", "password": password},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise PhaseError(f"owner registration failed ({resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        token = (body.get("data") or {}).get("access_token")
        if not token:
            raise PhaseError(f"registration returned no access token: {body}")
        account_type = ((body.get("data") or {}).get("user") or {}).get("account_type")
        if account_type != "ADMIN":
            raise PhaseError(f"first registered account was not made owner/ADMIN: {account_type}")

        presets_resp = requests.get(
            f"{base}/api/presets", params={"include_uninstalled": "true"},
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        if presets_resp.status_code >= 400:
            raise PhaseError(f"preset list failed ({presets_resp.status_code}): {presets_resp.text[:300]}")
        presets = (presets_resp.json().get("data")) or []
        if not isinstance(presets, list) or len(presets) < 1:
            raise PhaseError(f"expected >= 1 preset from a fresh checkout, got: {presets!r}")

    def p_status():
        log_path = log_dir / "status.log"
        code = run_cli_capture(checkout_dir, ["status"], backend_port, frontend_port, env, log_path, timeout=CLI_INVOKE_TIMEOUT)
        if code != 0:
            raise PhaseError(f"`./potionui status` reported not-running (exit {code}) while the instance should be up - see {log_path}")

    pids_before_stop: list = []

    def p_stop():
        pids_before_stop.extend(read_pids_from_state(checkout_dir))
        log_path = log_dir / "stop.log"
        code = run_cli_capture(checkout_dir, ["stop"], backend_port, frontend_port, env, log_path, timeout=CLI_INVOKE_TIMEOUT)
        if code != 0:
            raise PhaseError(f"`./potionui stop` exited {code} - see {log_path}")

    def p_process_gone():
        if not pids_before_stop:
            raise PhaseError("no pids were recorded in .runtime/state.json before stop; cannot confirm teardown")
        still_alive = [pid for pid in pids_before_stop if pid_alive(pid)]
        if still_alive:
            raise PhaseError(f"process(es) still alive after `./potionui stop`: {still_alive}")

    return [
        Phase("materialize", p_materialize),
        Phase("doctor", p_doctor),
        Phase("start", p_start),
        Phase("health", p_health),
        Phase("claim_token", p_claim_token),
        Phase("owner_and_presets", p_owner_and_presets),
        Phase("status", p_status),
        Phase("stop", p_stop),
        Phase("process_gone", p_process_gone),
    ]


def build_worker_phases(checkout_dir: Path, log_dir: Path, port: int, env: dict, args) -> list:
    worker_env = dict(env)
    worker_env["POTIONUI_WORKER_TOKEN"] = secrets.token_urlsafe(24)
    worker_proc_holder: dict = {}

    def p_materialize():
        if checkout_dir.exists():
            shutil.rmtree(checkout_dir)
        if args.from_dir is not None:
            materialize_from_dir(Path(args.from_dir).resolve(), checkout_dir)
        else:
            materialize_from_git(args.from_git, checkout_dir)
        if not is_windows():
            (checkout_dir / "potionui").chmod(0o755)  # potionui.cmd needs no execute bit on Windows
        if args.reuse_venv:
            venv_src = Path(args.reuse_venv).resolve()
            if not venv_src.is_dir():
                raise PhaseError(f"--reuse-venv path does not exist or is not a directory: {venv_src}")
            (checkout_dir / "venv").symlink_to(venv_src, target_is_directory=True)

    def p_worker_doctor():
        log_path = log_dir / "worker-doctor.json"
        cmd = [*potionui_launcher(checkout_dir), "worker", "doctor", "--json", "--port", str(port)]
        with open(log_path, "wb") as fh:
            fh.write((" ".join(cmd) + "\n").encode())
            fh.flush()
            proc = subprocess.run(cmd, cwd=str(checkout_dir), env=worker_env, stdout=fh, stderr=subprocess.STDOUT, timeout=CLI_INVOKE_TIMEOUT)
        try:
            lines = log_path.read_text().splitlines()
            rows = json.loads("\n".join(lines[1:]))
        except (OSError, json.JSONDecodeError) as exc:
            raise PhaseError(f"worker doctor --json produced unparseable output ({exc}); see {log_path}") from exc
        errors = [r for r in rows if r.get("severity") == "error"]
        if errors:
            raise PhaseError(
                f"worker doctor blocked on {[r['code'] for r in errors]} (exit {proc.returncode}) - "
                f"expected on a box with no NVIDIA GPU; see {log_path}"
            )

    def p_worker_start():
        cmd = [*potionui_launcher(checkout_dir), "worker", "start", "--port", str(port)]
        log_path = log_dir / "worker-start.log"
        log_fh = open(log_path, "wb")
        proc = subprocess.Popen(cmd, cwd=str(checkout_dir), env=worker_env, stdout=log_fh, stderr=subprocess.STDOUT, start_new_session=True)
        worker_proc_holder["proc"] = proc
        worker_proc_holder["log_fh"] = log_fh
        time.sleep(0.5)
        if proc.poll() is not None:
            raise PhaseError(f"worker start exited immediately (code {proc.returncode}) - see {log_path}")

    def p_worker_health():
        url = f"http://127.0.0.1:{port}/health"
        if not wait_for(lambda: http_get_ok(url), timeout=args.timeout, interval=2.0):
            raise PhaseError(f"{url} never returned 2xx/3xx within {args.timeout}s")

    phases = [
        Phase("materialize", p_materialize),
        Phase("worker_doctor", p_worker_doctor),
        Phase("worker_start", p_worker_start),
        Phase("worker_health", p_worker_health),
        Phase("worker_stop", lambda: stop_worker_process(worker_proc_holder)),
    ]
    return phases, worker_proc_holder


def stop_worker_process(worker_proc_holder: dict) -> None:
    """Kill the backgrounded `worker start` process group, if any is still
    alive. Idempotent - safe to call as the normal `worker_stop` phase AND
    again as a safety net after a run that failed before reaching it."""
    import signal as _signal

    proc = worker_proc_holder.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, _signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, _signal.SIGKILL)
            proc.wait(timeout=10.0)
    log_fh = worker_proc_holder.get("log_fh")
    if log_fh and not log_fh.closed:
        log_fh.close()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_profile(profile: str, root: Path, port_start: int, args) -> ProfileReport:
    checkout_dir = root / f"checkout-{profile}"
    log_dir = root / f"logs-{profile}"
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PIP_CACHE_DIR"] = str(root / "pip-cache")

    worker_proc_holder: Optional[dict] = None
    if profile == "worker":
        port = pick_free_ports(1, start=port_start)[0]
        phases, worker_proc_holder = build_worker_phases(checkout_dir, log_dir, port, env, args)
        backend_port = frontend_port = None
    else:
        backend_port, frontend_port = pick_free_ports(2, start=port_start)
        phases = build_core_phases(profile, checkout_dir, log_dir, (backend_port, frontend_port), env, args)

    try:
        result = run_phases(phases)
    finally:
        # Safety net independent of which phase failed: a `start`/`worker
        # start` that succeeded but a *later* phase that raised must never
        # leave a supervised backend/frontend/worker process orphaned on the
        # box. Both calls are idempotent against an already-stopped instance.
        if profile == "worker":
            if worker_proc_holder is not None:
                stop_worker_process(worker_proc_holder)
        elif (checkout_dir / ".runtime" / "state.json").exists():
            with contextlib.suppress(Exception):
                run_cli_capture(
                    checkout_dir, ["stop"], backend_port, frontend_port, env,
                    log_dir / "cleanup-stop.log", timeout=CLI_INVOKE_TIMEOUT,
                )

    if not args.keep and checkout_dir.exists():
        shutil.rmtree(checkout_dir, ignore_errors=True)

    return ProfileReport(
        profile=profile,
        phase_reached=result.phase_reached,
        passed=result.passed,
        reason=result.reason,
        seconds=result.seconds,
        checkout_dir=str(checkout_dir) if args.keep else "(removed)",
        log_dir=str(log_dir),
    )


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profiles", default="remote", help="Comma-separated: local,hybrid,remote,worker (default: remote).")
    parser.add_argument("--from-git", default="HEAD", help="git ref to materialize each checkout from (default: HEAD).")
    parser.add_argument("--from-dir", default=None, help="Copy this directory instead of using git archive (e.g. '.').")
    parser.add_argument("--reuse-venv", default=None, help="Symlink this venv into each checkout, to prove `start` skips pip install.")
    parser.add_argument("--with-dev", action="store_true", help="Also boot the Vite dev server (--dev). Off by default.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_START_TIMEOUT, help="Readiness timeout passed to `./potionui start`/worker health polling.")
    parser.add_argument("--install-timeout", type=float, default=DEFAULT_INSTALL_TIMEOUT, help="Extra ceiling for the pip/npm install `start` may run.")
    parser.add_argument("--port-start", type=int, default=DEFAULT_PORT_START, help="Lowest port to consider free (default 8055).")
    parser.add_argument("--keep", action="store_true", help="Leave checkouts on disk and print their paths.")
    parser.add_argument("--json", default=None, help="Write the report as JSON to this path.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Scratch root for checkouts/logs (never inside the repo).")
    args = parser.parse_args(argv)

    if Path(args.root).resolve() == REPO_ROOT or REPO_ROOT in Path(args.root).resolve().parents:
        parser.error(f"--root must not be inside the repo ({REPO_ROOT})")

    return args


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    unknown = [p for p in profiles if p not in ALL_PROFILES]
    if unknown:
        print(f"Unknown profile(s): {unknown}. Valid: {ALL_PROFILES}", file=sys.stderr)
        return 2

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "pip-cache").mkdir(exist_ok=True)

    reports = []
    port_start = args.port_start
    for profile in profiles:
        print(f"\n=== {profile} ===")
        report = run_profile(profile, root, port_start, args)
        reports.append(report)
        port_start += 3  # leave headroom so the next profile's scan doesn't collide with this one's ports
        status = "PASS" if report.passed else f"FAIL at {report.phase_reached}: {report.reason}"
        print(f"{profile}: {status} ({report.seconds:.1f}s)")
        if args.keep:
            print(f"  checkout: {report.checkout_dir}")
        print(f"  logs: {report.log_dir}")

    print()
    print_table(reports)

    if args.json:
        Path(args.json).write_text(json.dumps([r.to_row() for r in reports], indent=2))
        print(f"\nJSON report: {args.json}")

    return 0 if all(r.passed for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
