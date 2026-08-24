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
          (>= 4173, never 3001), with /api + /health + /ws proxied to the
          throwaway backend (see the `preview` block in
          frontend/vite.config.ts).
       c. Runs `npx playwright test` for just that chunk's specs, watching
          the preview process the whole time.
       d. Collects screenshots (.png) and video clips (.webm) into
          tests/e2e/ui/artifacts/<journey>/, then tears both down.

Why chunk + fresh backend/preview per chunk: passing ~10+ specs to a single
invocation has reliably killed the `vite preview` process partway through
(see tests/e2e/ui/README.md). Batches of 3 run as separate `run.py`
invocations - which restart backend, preview, and the Playwright process each
time - were reproduced healthy three times running for the same total spec
count that killed a single big invocation. Chunking internalizes that
known-good pattern literally (fresh everything per chunk) instead of asking
every caller to remember to split their command line.

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
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
HARNESS_DIR = REPO_ROOT / "tests" / "e2e" / "harness"
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from e2e_harness import StageError, ThrowawayApp, log, pick_free_port  # noqa: E402

try:
    import requests
except ImportError:  # pragma: no cover - requests ships in requirements.txt
    print("The 'requests' package is required (pip install -r requirements.txt).", file=sys.stderr)
    raise

FRONTEND_DIR = REPO_ROOT / "frontend"
SPECS_DIR = FRONTEND_DIR / "tests" / "e2e"
PLAYWRIGHT_OUTPUT_DIR = SPECS_DIR / ".playwright-artifacts"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

# A fixed owner password so the browser can log in as the instance owner that
# ThrowawayApp already claimed - it's a throwaway loopback-only instance.
OWNER_USERNAME = "e2e-owner"
OWNER_PASSWORD = "e2e-owner-pw-2f1a9c"

PREVIEW_START_PORT = 4173

# Empirically safe: three separate run.py invocations of 3 specs each stayed
# healthy for the same total (9) that killed one big invocation. Overridable
# via --chunk-size; see the docstring above for why fresh-backend-per-chunk is
# the chosen unit rather than just fresh-Playwright-process-per-chunk.
DEFAULT_CHUNK_SIZE = 3

# How often the background thread polls the preview subprocess while
# Playwright is running against it.
PREVIEW_POLL_INTERVAL_SECONDS = 0.5

EXIT_OK = 0
EXIT_TEST_FAILURE = 1
EXIT_ARGS_ERROR = 2
EXIT_PREVIEW_DIED = 3


def discover_specs() -> List[str]:
    return sorted(p.name[: -len(".spec.ts")] for p in SPECS_DIR.glob("*.spec.ts"))


def chunked(items: List[str], size: int) -> List[List[str]]:
    if size <= 0:
        size = len(items) or 1
    return [items[i : i + size] for i in range(0, len(items), size)]


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


def run_build() -> None:
    """`npm run build`, retried once after a pause: the tree can be mid-edit by
    a concurrent agent and fail transiently."""
    for attempt in (1, 2):
        log(f"Building frontend (npm run build, attempt {attempt})...")
        proc = subprocess.run(["npm", "run", "build"], cwd=str(FRONTEND_DIR))
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
    cmd = ["npx", "vite", "preview", "--port", str(preview_port), "--host", "127.0.0.1"]
    log(f"Starting preview: {' '.join(cmd)} (backend proxy -> :{backend_port}, log: {log_path.name})")
    log_file = open(log_path, "wb", buffering=0)
    proc = subprocess.Popen(
        cmd, cwd=str(FRONTEND_DIR), env=env,
        stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
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
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
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
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, start_new_session=True)
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
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
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

    with ThrowawayApp(
        models_dir=args.models_dir, port=args.port, keep=args.keep,
        username=OWNER_USERNAME, password=OWNER_PASSWORD,
    ) as app:
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

            cmd = ["npx", "playwright", "test", *chunk_names]
            if args.headed:
                cmd.append("--headed")
            log(f"Running: {' '.join(cmd)}")
            returncode = run_playwright_watched(cmd, str(FRONTEND_DIR), env, monitor)

            monitor.stop()
            collect_videos(chunk_names)

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
    args = parser.parse_args(argv)

    names = args.journeys or discover_specs()
    if not names:
        print("No specs found under frontend/tests/e2e/", file=sys.stderr)
        return EXIT_ARGS_ERROR
    unknown = [n for n in names if not (SPECS_DIR / f"{n}.spec.ts").is_file()]
    if unknown:
        print(f"Unknown spec(s): {unknown}. Available: {discover_specs()}", file=sys.stderr)
        return EXIT_ARGS_ERROR

    # Clear only the journeys this run re-executes: concurrent/sequential runs
    # of OTHER journeys must not lose their collected evidence.
    for name in names:
        shutil.rmtree(ARTIFACTS_DIR / name, ignore_errors=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_build:
        run_build()
    elif not (FRONTEND_DIR / ".svelte-kit" / "output" / "client").is_dir():
        raise StageError("build", "--skip-build given but no build output exists; run once without it.")

    chunk_size = args.chunk_size if args.chunk_size and args.chunk_size > 0 else len(names)
    chunks = chunked(names, chunk_size)
    log(
        f"Running {len(names)} spec(s) as {len(chunks)} chunk(s) of up to {chunk_size} "
        f"(fresh backend + preview + Playwright process per chunk)"
    )

    overall_returncode = EXIT_OK
    try:
        for idx, chunk_names in enumerate(chunks, start=1):
            returncode = run_chunk(
                chunk_names=chunk_names, chunk_index=idx, total_chunks=len(chunks), args=args,
            )
            if returncode != 0:
                overall_returncode = EXIT_TEST_FAILURE

        log(f"Artifacts (screenshots + video) under: {ARTIFACTS_DIR}")
        return overall_returncode

    except _PreviewDied as died:
        remaining_chunks = chunks[died.chunk_index :]
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
