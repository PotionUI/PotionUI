#!/usr/bin/env python3
"""Runner for browser-UI journeys - the Playwright layer on top of the same
throwaway backend the HTTP journeys (tests/e2e/journeys/) use.

Where an HTTP journey drives the API directly, a UI journey drives a real
Chromium browser against the built frontend, so it catches the class of
frontend-reactivity bug (stuck spinners, `$effect` request loops, controls that
never settle) that an HTTP assertion can't see. Each run:

  1. Builds the frontend once (`npm run build`) unless --skip-build.
  2. Splits the requested specs into fixed-size chunks (default 3 - see
     DEFAULT_CHUNK_SIZE) and, for each chunk:
       a. Boots one throwaway backend (`ThrowawayApp`, port >= 8055, temp
          DB/storage, read-only depot mirror).
       b. Serves the build with `vite preview` on an ephemeral port
          (>= 4173, never 26731), with /api + /health + /ws proxied to the
          throwaway backend (see the `preview` block in
          frontend/vite.config.ts).
       c. Runs `npx playwright test` for just that chunk's specs, watching
          the preview process the whole time.
       d. Collects screenshots (.png) and video clips (.webm) into
          tests/e2e/ui/artifacts/<journey>/, then tears both down.

Why chunk + fresh backend/preview per chunk: passing ~10+ specs to a single
invocation has reliably killed the `vite preview` process partway through
(see tests/e2e/ui/README.md). A fresh backend + preview per chunk is the
isolation unit - it keeps one chunk's leftover server/DB state from bleeding
into the next - while the PreviewMonitor below makes a chunk's preview dying
cheap to detect and abort, rather than something only a small chunk size
could contain.

If the preview process dies anyway mid-chunk, the runner does not let the
resulting connection-refused cascade masquerade as ordinary spec failures: it
detects the death via a background poll of the preview subprocess, aborts that
chunk's Playwright run immediately instead of waiting out the cascade, prints
an unmistakable diagnostic (including the preview process's own exit status -
never previously captured), and exits with a distinct status code
(EXIT_PREVIEW_DIED) so a caller can tell "the harness broke" apart from "a
spec failed".

Usage:

    python tests/e2e/ui/run.py                       # every spec, chunked
    python tests/e2e/ui/run.py empty-group-tabs      # one spec
    python tests/e2e/ui/run.py --headed --keep
    python tests/e2e/ui/run.py --skip-build          # reuse last build
    python tests/e2e/ui/run.py --chunk-size 5        # override the default
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

import psutil

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
HARNESS_DIR = REPO_ROOT / "tests" / "e2e" / "harness"
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

import cloud_fake
from e2e_harness import (  # noqa: E402
    StageError,
    ThrowawayApp,
    kill_group,
    log,
    pick_free_port,
    popen_group_kwargs,
    teardown_backend,
)

try:
    import requests
except ImportError:  # pragma: no cover - requests ships in requirements.txt
    print("The 'requests' package is required (pip install -r requirements.txt).", file=sys.stderr)
    raise

FRONTEND_DIR = REPO_ROOT / "frontend"
SPECS_DIR = FRONTEND_DIR / "tests" / "e2e"
NPX = shutil.which("npx") or "npx"
NPM = shutil.which("npm") or "npm"
PLAYWRIGHT_OUTPUT_DIR = SPECS_DIR / ".playwright-artifacts"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

# A fixed owner password so the browser can log in as the instance owner that
# ThrowawayApp already claimed - it's a throwaway loopback-only instance.
OWNER_USERNAME = "e2e-owner"
OWNER_PASSWORD = "e2e-owner-pw-2f1a9c"

PREVIEW_START_PORT = 4173

DEFAULT_CHUNK_SIZE = 8

# How often the background thread polls the preview subprocess while
# Playwright is running against it.
PREVIEW_POLL_INTERVAL_SECONDS = 0.5

EXIT_OK = 0
EXIT_TEST_FAILURE = 1
EXIT_ARGS_ERROR = 2
EXIT_PREVIEW_DIED = 3
EXIT_LOCK_TIMEOUT = 4

RUN_LOCK_PATH = Path(__file__).resolve().parent / ".run.lock"
DEFAULT_LOCK_TIMEOUT_MINUTES = 30.0
LOCK_POLL_SECONDS = 5.0
UNREADABLE_LOCK_GRACE_SECONDS = 10.0


def discover_specs() -> List[str]:
    return sorted(p.name[: -len(".spec.ts")] for p in SPECS_DIR.glob("*.spec.ts"))


FRESH_INSTANCE_SPECS = {"empty-group-tabs"}


def chunked(items: List[str], size: int) -> List[List[str]]:
    if size <= 0:
        size = len(items) or 1
    solo = [[name] for name in items if name in FRESH_INSTANCE_SPECS]
    shared = [name for name in items if name not in FRESH_INSTANCE_SPECS]
    return solo + [shared[i : i + size] for i in range(0, len(shared), size)]


CHUNK_MINUTES = (0.5, 1.9, 1.3, 1.2, 1.4, 1.9, 1.7, 2.0, 0.75)
DEFAULT_CHUNK_MINUTES = 1.5


def parse_shard(value: str) -> tuple:
    try:
        index_text, total_text = value.split("/")
        index, total = int(index_text), int(total_text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--shard expects i/n, got {value!r}")
    if total < 1 or not 1 <= index <= total:
        raise argparse.ArgumentTypeError(f"--shard expects 1 <= i <= n, got {value!r}")
    return index, total


def shard_assignment(chunk_count: int, total: int, head_start: Optional[dict] = None) -> List[int]:
    loads = [0.0] * total
    for shard, minutes in (head_start or {}).items():
        loads[shard - 1] += minutes
    weights = [
        CHUNK_MINUTES[i] if i < len(CHUNK_MINUTES) else DEFAULT_CHUNK_MINUTES
        for i in range(chunk_count)
    ]
    assignment = [0] * chunk_count
    for i in sorted(range(chunk_count), key=lambda k: (-weights[k], k)):
        shard = min(range(total), key=lambda k: (loads[k], k))
        loads[shard] += weights[i]
        assignment[i] = shard + 1
    return assignment


def select_shard(chunks: List[List[str]], index: int, total: int, head_start: Optional[dict] = None) -> List[tuple]:
    assignment = shard_assignment(len(chunks), total, head_start)
    return [(i + 1, chunk) for i, chunk in enumerate(chunks) if assignment[i] == index]


def describe_exit_status(code: Optional[int]) -> str:
    """Human-readable form of a Popen returncode - negative means killed by
    signal on POSIX. This is the diagnostic nobody has captured before: the
    preview process's own exit status, not just its (silent) log.

    We track the `npx vite preview` wrapper process, not the innermost node
    process it spawns - if something signals the *innermost* process (e.g. an
    OOM killer), Python never sees a negative returncode for that, because its
    own direct child (npx) is what exited, translating the grandchild's death
    into the conventional shell exit code 128+signal instead. So a positive
    code > 128 is decoded as a probable signal too."""
    if code is None:
        return "still running / exit status not captured"
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
        return f"killed by {name} (raw returncode {code})"
    if code == 0:
        return "exited cleanly (0)"
    if code > 128:
        sig_num = code - 128
        try:
            name = signal.Signals(sig_num).name
        except ValueError:
            name = f"signal {sig_num}"
        return f"exited with code {code} (128+{sig_num} - conventionally a child killed by {name})"
    return f"exited with code {code}"


class RunLockTimeout(Exception):
    def __init__(self, holder: dict):
        super().__init__(describe_lock_holder(holder))
        self.holder = holder


def describe_lock_holder(holder: Optional[dict]) -> str:
    if not holder:
        return "an unknown run (the lock file is empty or unreadable)"
    command = holder.get("command") or "run.py"
    return f"pid {holder.get('pid')} started {holder.get('started_at', '?')} ({command})"


def read_lock_holder(path: Path) -> Optional[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def lock_holder_alive(holder: dict) -> bool:
    pid = holder.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not psutil.pid_exists(pid):
        return False
    created = holder.get("process_created")
    if not isinstance(created, (int, float)):
        return True
    try:
        return abs(psutil.Process(pid).create_time() - created) < 1.0
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True


def _lock_is_stale(path: Path, holder: dict) -> bool:
    if holder:
        return not lock_holder_alive(holder)
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age > UNREADABLE_LOCK_GRACE_SECONDS


def _own_lock_record() -> dict:
    pid = os.getpid()
    try:
        created = psutil.Process(pid).create_time()
    except psutil.Error:
        created = None
    return {
        "pid": pid,
        "process_created": created,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": " ".join([Path(sys.argv[0]).name] + sys.argv[1:]) if sys.argv else "run.py",
    }


def acquire_run_lock(
    path: Path = RUN_LOCK_PATH,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_MINUTES * 60,
    poll_seconds: float = LOCK_POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    say: Callable[[str], None] = log,
) -> dict:
    deadline = clock() + timeout_seconds
    announced = None
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = read_lock_holder(path)
            if holder is None:
                continue
            if _lock_is_stale(path, holder):
                if read_lock_holder(path) == holder:
                    say(f"Taking over a stale run lock left by {describe_lock_holder(holder)}: that process is gone.")
                    with contextlib.suppress(FileNotFoundError):
                        path.unlink()
                continue
            remaining = deadline - clock()
            if remaining <= 0:
                raise RunLockTimeout(holder)
            if holder != announced:
                say(
                    f"Another UI run holds {path.name}: {describe_lock_holder(holder)}. Waiting for it to "
                    f"finish (up to {timeout_seconds / 60:.0f} min, --lock-timeout to change)..."
                )
                announced = holder
            else:
                say(f"Still waiting for pid {holder.get('pid')} to release {path.name} ({remaining / 60:.1f} min left)...")
            sleep(min(poll_seconds, remaining))
            continue
        record = _own_lock_record()
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        return record


def release_run_lock(record: dict, path: Path = RUN_LOCK_PATH) -> None:
    holder = read_lock_holder(path)
    if holder and holder.get("pid") == record.get("pid") and holder.get("started_at") == record.get("started_at"):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


@contextlib.contextmanager
def termination_signals_raise_system_exit():
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise(signum, _frame):
        raise SystemExit(128 + signum)

    previous = {}
    for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        with contextlib.suppress(OSError, ValueError):
            previous[signum] = signal.signal(signum, _raise)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signum, handler)


def run_build() -> None:
    """`npm run build`, retried once after a pause: the tree can be mid-edit by
    a concurrent agent and fail transiently."""
    for attempt in (1, 2):
        log(f"Building frontend (npm run build, attempt {attempt})...")
        proc = subprocess.run([NPM, "run", "build"], cwd=str(FRONTEND_DIR))
        if proc.returncode == 0:
            log("Frontend build OK")
            return
        if attempt == 1:
            log("Build failed - retrying once after 60s (tree may be mid-edit)")
            time.sleep(60)
    raise StageError("build", "npm run build failed twice")


def start_preview(backend_port: int, preview_port: int, log_path: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["E2E_BACKEND_PORT"] = str(backend_port)
    env["E2E_PREVIEW_PORT"] = str(preview_port)
    cmd = [NPX, "vite", "preview", "--port", str(preview_port), "--host", "127.0.0.1"]
    log(f"Starting preview: {' '.join(cmd)} (backend proxy -> :{backend_port}, log: {log_path.name})")
    log_file = open(log_path, "wb", buffering=0)
    proc = subprocess.Popen(
        cmd, cwd=str(FRONTEND_DIR), env=env,
        stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        **popen_group_kwargs(),
    )
    log_file.close()
    return proc


def wait_for_preview(base_url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(base_url + "/", timeout=5)
            if r.status_code < 500:
                log(f"Preview reachable at {base_url} (HTTP {r.status_code})")
                return
            last = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(1.0)
    raise StageError("preview", f"Preview never became reachable at {base_url} (last: {last})")


def stop_preview(proc: subprocess.Popen) -> Optional[int]:
    """Intentional, expected teardown - terminate the preview process and
    return its exit status for logging. A SIGTERM-induced exit here is normal
    and not itself evidence of anything."""
    if proc.poll() is not None:
        return proc.returncode
    kill_group(proc, force=False)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill_group(proc, force=True)
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
    return proc.returncode


class PreviewMonitor:
    """Background poll of the preview subprocess while Playwright is running
    against it. Detects the process exiting on its own (i.e. NOT via our own
    stop_preview teardown) so a dying preview server can be reported the
    moment it happens, instead of surfacing later as a wall of ordinary-looking
    `net::ERR_CONNECTION_REFUSED` test failures."""

    def __init__(self, proc: subprocess.Popen, poll_interval: float = PREVIEW_POLL_INTERVAL_SECONDS):
        self._proc = proc
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._died = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)

    def start(self) -> "PreviewMonitor":
        self._thread.start()
        return self

    def _watch(self) -> None:
        while not self._stop.is_set():
            if self._proc.poll() is not None:
                self._died.set()
                return
            self._stop.wait(self._poll_interval)

    def stop(self) -> None:
        """Call this BEFORE intentionally tearing the preview process down
        yourself, so our own SIGTERM never gets misreported as an unexpected
        death."""
        self._stop.set()
        self._thread.join(timeout=5)

    @property
    def died_unexpectedly(self) -> bool:
        return self._died.is_set()


def run_playwright_watched(cmd: List[str], cwd: str, env: dict, monitor: PreviewMonitor) -> Optional[int]:
    """Run Playwright, but stop waiting on it the moment the preview monitor
    reports a death - rather than sitting through however many remaining specs
    each time out against a connection that's no longer accepted. Returns
    Playwright's own return code, or None if we aborted it early because the
    preview died underneath it."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, **popen_group_kwargs())
    while True:
        try:
            return proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            if monitor.died_unexpectedly:
                log(
                    "Preview process died while Playwright was still running - "
                    "aborting this chunk's Playwright run now instead of waiting "
                    "out a connection-refused cascade."
                )
                kill_group(proc, force=False)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    kill_group(proc, force=True)
                    with contextlib.suppress(Exception):
                        proc.wait(timeout=10)
                return None


def collect_videos(names: List[str]) -> None:
    """Playwright drops each test's `video.webm` into a per-test hash dir under
    PLAYWRIGHT_OUTPUT_DIR. Copy them next to the screenshots as
    artifacts/<journey>/<journey>.webm so the evidence carries a sane name.

    Must run right after each chunk's Playwright invocation, before the next
    chunk's invocation clears PLAYWRIGHT_OUTPUT_DIR (Playwright's default
    outputDir behavior)."""
    if not PLAYWRIGHT_OUTPUT_DIR.is_dir():
        return
    # Longest journey name first so a name that's a prefix of another can't steal
    # the match.
    ordered = sorted(names, key=len, reverse=True)
    for video in sorted(PLAYWRIGHT_OUTPUT_DIR.glob("*/video.webm")):
        dir_name = video.parent.name
        journey = next((n for n in ordered if dir_name.startswith(n)), None)
        if journey is None:
            continue
        dest_dir = ARTIFACTS_DIR / journey
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{journey}.webm"
        i = 2
        while dest.exists():
            dest = dest_dir / f"{journey}-{i}.webm"
            i += 1
        shutil.copy2(video, dest)
        log(f"Collected video: {dest}")


def collect_failure_artifacts(
    names: List[str],
    output_dir: Optional[Path] = None,
    artifacts_dir: Optional[Path] = None,
) -> None:
    output_dir = PLAYWRIGHT_OUTPUT_DIR if output_dir is None else output_dir
    artifacts_dir = ARTIFACTS_DIR if artifacts_dir is None else artifacts_dir
    if not output_dir.is_dir():
        return
    ordered = sorted(names, key=len, reverse=True)
    for test_dir in sorted(p for p in output_dir.iterdir() if p.is_dir()):
        journey = next((n for n in ordered if test_dir.name.startswith(n)), None)
        if journey is None:
            continue
        sources = [*test_dir.glob("trace.zip"), *test_dir.glob("*.png")]
        if not sources:
            continue
        dest_dir = artifacts_dir / journey
        dest_dir.mkdir(parents=True, exist_ok=True)
        for source in sources:
            dest = dest_dir / f"{test_dir.name}-{source.name}"
            shutil.copy2(source, dest)
            log(f"Collected failure artifact: {dest}")


def collect_backend_log(app: "ThrowawayApp", chunk_index: int) -> None:
    log_path = app.instance.log_path if app.instance is not None else None
    if log_path is None or not log_path.is_file():
        return
    dest = ARTIFACTS_DIR / f"backend-chunk{chunk_index}.log"
    shutil.copy2(log_path, dest)
    log(f"Collected backend log: {dest}")


def run_chunk(
    *,
    chunk_names: List[str],
    chunk_index: int,
    total_chunks: int,
    args: argparse.Namespace,
) -> int:
    """Boot a fresh throwaway backend + preview, run this chunk's specs
    against it, tear both down, and return Playwright's return code.

    Raises _PreviewDied (never a StageError - that's reserved for harness boot
    failures) if the preview process dies while Playwright is running."""
    log(f"=== Chunk {chunk_index}/{total_chunks}: {', '.join(chunk_names)} ===")

    needs_cloud_fake = cloud_fake.wants_cloud_fake(chunk_names)
    with ThrowawayApp(
        models_dir=args.models_dir, port=args.port, keep=args.keep,
        username=OWNER_USERNAME, password=OWNER_PASSWORD,
        extra_env=cloud_fake.plugin_env() if needs_cloud_fake else None,
    ) as app:
        if needs_cloud_fake:
            cloud_fake.prepare(app)
        backend_port = app.instance.port
        log(f"Throwaway backend up at {app.base_url} (owner={app.username})")

        preview_port = args.preview_port or pick_free_port(PREVIEW_START_PORT)
        base_url = f"http://127.0.0.1:{preview_port}"
        preview_log_path = ARTIFACTS_DIR / f"preview-chunk{chunk_index}.log"
        preview_proc = start_preview(backend_port, preview_port, preview_log_path)
        try:
            wait_for_preview(base_url)

            monitor = PreviewMonitor(preview_proc).start()

            env = dict(os.environ)
            env["E2E_BASE_URL"] = base_url
            env["E2E_BACKEND_URL"] = app.base_url
            env["E2E_USERNAME"] = app.username
            env["E2E_PASSWORD"] = OWNER_PASSWORD
            env["E2E_ARTIFACTS_DIR"] = str(ARTIFACTS_DIR)
            env["E2E_DB_PATH"] = str(app.instance.db_path)

            cmd = [NPX, "playwright", "test", *chunk_names]
            if args.headed:
                cmd.append("--headed")
            log(f"Running: {' '.join(cmd)}")
            returncode = run_playwright_watched(cmd, str(FRONTEND_DIR), env, monitor)

            monitor.stop()
            collect_videos(chunk_names)
            collect_failure_artifacts(chunk_names)

            if monitor.died_unexpectedly:
                exit_code = preview_proc.poll()
                raise _PreviewDied(
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    chunk_names=chunk_names,
                    exit_status=describe_exit_status(exit_code),
                    log_path=preview_log_path,
                )

            return returncode if returncode is not None else 1
        finally:
            code = stop_preview(preview_proc)
            log(f"Preview (chunk {chunk_index}) exited: {describe_exit_status(code)}")
            try:
                teardown_backend(app.instance, keep=True)
            except Exception as exc:
                log(f"Stopping backend before log collection failed: {exc}")
            try:
                collect_backend_log(app, chunk_index)
            except Exception as exc:
                log(f"Collecting backend log failed: {exc}")


class _PreviewDied(Exception):
    def __init__(self, *, chunk_index: int, total_chunks: int, chunk_names: List[str], exit_status: str, log_path: Path):
        self.chunk_index = chunk_index
        self.total_chunks = total_chunks
        self.chunk_names = chunk_names
        self.exit_status = exit_status
        self.log_path = log_path
        super().__init__("preview died mid-run")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run browser-UI journeys against a throwaway PotionUI instance.")
    parser.add_argument("journeys", nargs="*", help="Spec basenames to run, e.g. empty-group-tabs (default: all).")
    parser.add_argument("--models-dir", default=None, help="Depot to mirror read-only (default: models/tests).")
    parser.add_argument("--port", type=int, default=None, help="Throwaway backend port (default: auto from 8055).")
    parser.add_argument("--preview-port", type=int, default=None, help="Preview port (default: auto from 4173).")
    parser.add_argument("--keep", action="store_true", help="Leave the backend + temp dir on disk after the run.")
    parser.add_argument("--headed", action="store_true", help="Run Chromium headed (needs a display).")
    parser.add_argument("--skip-build", action="store_true", help="Reuse the existing .svelte-kit build output.")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=(
            f"Max specs per fresh backend+preview+Playwright invocation "
            f"(default {DEFAULT_CHUNK_SIZE} - empirically safe; passing ~10+ specs to one "
            f"invocation has killed the preview server). Use a single chunk "
            f"(--chunk-size 0) only to deliberately reproduce that failure."
        ),
    )
    parser.add_argument(
        "--shard",
        type=parse_shard,
        default=(1, 1),
        metavar="I/N",
        help="Run only shard I of N: a deterministic, time-balanced subset of the chunks (default 1/1 = all).",
    )
    parser.add_argument(
        "--shard-head-start",
        type=float,
        default=0.0,
        metavar="MIN",
        help="Minutes of other work already assigned to shard 1 (e.g. the HTTP journeys); balances the split.",
    )
    parser.add_argument("--list", action="store_true", help="Print the chunks this invocation would run and exit.")
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=DEFAULT_LOCK_TIMEOUT_MINUTES,
        metavar="MIN",
        help=(
            f"Minutes to wait for another UI run to release {RUN_LOCK_PATH.name} "
            f"(default {DEFAULT_LOCK_TIMEOUT_MINUTES:.0f}); runs share frontend/build and the preview port."
        ),
    )
    args = parser.parse_args(argv)

    names = args.journeys or discover_specs()
    if not names:
        print("No specs found under frontend/tests/e2e/", file=sys.stderr)
        return EXIT_ARGS_ERROR
    unknown = [n for n in names if not (SPECS_DIR / f"{n}.spec.ts").is_file()]
    if unknown:
        print(f"Unknown spec(s): {unknown}. Available: {discover_specs()}", file=sys.stderr)
        return EXIT_ARGS_ERROR

    shard_index, shard_total = args.shard
    chunk_size = args.chunk_size if args.chunk_size and args.chunk_size > 0 else len(names)
    all_chunks = chunked(names, chunk_size)
    head_start = {1: args.shard_head_start} if args.shard_head_start else None
    numbered = select_shard(all_chunks, shard_index, shard_total, head_start)
    if args.list:
        for number, chunk in numbered:
            print(f"shard {shard_index}/{shard_total} chunk {number}/{len(all_chunks)}: {' '.join(chunk)}")
        return EXIT_OK
    names = [n for _, chunk in numbered for n in chunk]
    if not names:
        print(f"Shard {shard_index}/{shard_total} has no chunks.", file=sys.stderr)
        return EXIT_ARGS_ERROR

    try:
        lock = acquire_run_lock(RUN_LOCK_PATH, timeout_seconds=max(args.lock_timeout, 0.0) * 60)
    except RunLockTimeout as exc:
        print(
            f"\nGave up after {args.lock_timeout:g} min waiting for {RUN_LOCK_PATH}: still held by {exc}. "
            f"Wait for that run to finish, or delete the file if you are sure it is not running.",
            file=sys.stderr,
        )
        return EXIT_LOCK_TIMEOUT
    try:
        with termination_signals_raise_system_exit():
            return _run_locked(args, names, numbered, all_chunks, shard_index, shard_total, chunk_size)
    finally:
        release_run_lock(lock, RUN_LOCK_PATH)


def _run_locked(
    args: argparse.Namespace,
    names: List[str],
    numbered: List[tuple],
    all_chunks: List[List[str]],
    shard_index: int,
    shard_total: int,
    chunk_size: int,
) -> int:
    for name in names:
        shutil.rmtree(ARTIFACTS_DIR / name, ignore_errors=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_build:
        run_build()
    elif not (FRONTEND_DIR / ".svelte-kit" / "output" / "client").is_dir():
        raise StageError("build", "--skip-build given but no build output exists; run once without it.")

    log(
        f"Shard {shard_index}/{shard_total}: {len(names)} spec(s) as {len(numbered)} of "
        f"{len(all_chunks)} chunk(s) of up to {chunk_size} "
        f"(fresh backend + preview + Playwright process per chunk)"
    )

    overall_returncode = EXIT_OK
    try:
        for idx, chunk_names in numbered:
            returncode = run_chunk(
                chunk_names=chunk_names, chunk_index=idx, total_chunks=len(all_chunks), args=args,
            )
            if returncode != 0:
                overall_returncode = EXIT_TEST_FAILURE

        log(f"Artifacts (screenshots + video) under: {ARTIFACTS_DIR}")
        return overall_returncode

    except _PreviewDied as died:
        remaining_chunks = [c for i, c in numbered if i > died.chunk_index]
        remaining_specs = [n for c in remaining_chunks for n in c]
        border = "=" * 78
        print(f"\n{border}", file=sys.stderr)
        print("PREVIEW SERVER DIED MID-RUN - this is a harness failure, not spec failures.", file=sys.stderr)
        print(
            f"Chunk {died.chunk_index}/{died.total_chunks} (specs: {', '.join(died.chunk_names)}) "
            f"was still running Playwright when `vite preview` exited unexpectedly.",
            file=sys.stderr,
        )
        print(f"Preview process exit status: {died.exit_status}", file=sys.stderr)
        print(f"Preview log for this chunk: {died.log_path}", file=sys.stderr)
        if remaining_specs:
            print(
                f"Specs never attempted ({len(remaining_chunks)} chunk(s), {len(remaining_specs)} spec(s)): "
                f"{', '.join(remaining_specs)}",
                file=sys.stderr,
            )
        else:
            print("This was the last chunk - no specs after it were skipped.", file=sys.stderr)
        print(
            "Any pass/fail result already reported for the dying chunk's spec(s) is NOT "
            "trustworthy - re-run them in isolation once the preview is healthy again.",
            file=sys.stderr,
        )
        print(border, file=sys.stderr)
        return EXIT_PREVIEW_DIED

    except StageError as exc:
        print(f"\nFAILED at stage [{exc.stage}]: {exc.message}", file=sys.stderr)
        return EXIT_TEST_FAILURE


if __name__ == "__main__":
    sys.exit(main())
