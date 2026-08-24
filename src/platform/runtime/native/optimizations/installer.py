"""Runs `pip install` for one optimization at a time with live log streaming.

One job at a time (an `asyncio.Lock`): a second `start()` while a build is
running raises `RuntimeError`, which the controller turns into a 409. The
subprocess's stdout+stderr are merged and pumped into `InstallJob.log` as
`(timestamp, line)` pairs so a poller can fetch only the new lines by offset.

Job state is in-memory only: a server restart mid-install orphans the pip
process (an accepted limitation).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.platform.observability.logger import logger

from .catalog import Optimization

_LOG_CAP = 5000
_INSTALL_TIMEOUT_SECONDS = 45 * 60
_CANCEL_GRACE_SECONDS = 5


@dataclass
class InstallJob:
    opt_id: str
    status: str = "running"  # running | success | failed | cancelled
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    log: list[tuple[float, str]] = field(default_factory=list)
    process: Optional[asyncio.subprocess.Process] = None
    result: Optional[dict] = None
    error: Optional[str] = None

    def append_log(self, line: str) -> None:
        self.log.append((time.time(), line))
        if len(self.log) > _LOG_CAP:
            del self.log[: len(self.log) - _LOG_CAP]


def _target_python() -> str:
    """Pick the interpreter pip installs should run under.

    In the docker PYTHONPATH-workaround environment, `sys.executable` is the
    system python while torch actually loads from `<repo>/venv` — pip must
    install into the same interpreter torch will import from, or the newly
    built extension lands somewhere `import` never looks. If torch is loaded
    from a `venv/` directory under the repo root, prefer that venv's python.
    """
    try:
        import torch

        torch_path = Path(torch.__file__).resolve()
        repo_root = Path(__file__).resolve().parents[4]
        venv_python = repo_root / "venv" / "bin" / "python"
        if venv_python.exists() and "venv" in torch_path.parts:
            return str(venv_python)
    except Exception:  # noqa: BLE001 — fall through to sys.executable
        pass
    return sys.executable


class OptimizationInstaller:
    """Singleton coordinator for at-most-one concurrent pip build."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._job: Optional[InstallJob] = None

    @property
    def current_job(self) -> Optional[InstallJob]:
        return self._job

    def start(self, opt: Optimization, probe) -> InstallJob:
        """Kick off a build. Raises RuntimeError (-> 409) if one is already running."""
        if self._job is not None and self._job.status == "running":
            raise RuntimeError(f"An installation is already in progress ({self._job.opt_id})")

        spec = opt.install_spec(probe)
        job = InstallJob(opt_id=opt.id)
        self._job = job
        asyncio.create_task(self._run(opt, spec, job))
        return job

    async def _run(self, opt: Optimization, spec, job: InstallJob) -> None:
        async with self._lock:
            python = _target_python()
            env = {**os.environ, **spec.env, "PIP_NO_INPUT": "1"}
            # Try the primary package name(s); if the catalog entry offers a
            # fallback spelling (e.g. an alternate NVIDIA pip naming scheme)
            # and the primary attempt fails, try that one too - see
            # InstallSpec.fallback_pip_args.
            attempts = [spec.pip_args]
            if spec.fallback_pip_args:
                attempts.append(spec.fallback_pip_args)

            try:
                rc: Optional[int] = None
                for attempt_index, pip_args in enumerate(attempts):
                    args = [python, "-m", "pip", "install", "--no-input", *pip_args]
                    if attempt_index > 0:
                        job.append_log("primary package name(s) not installable; trying fallback naming scheme")
                    job.append_log(f"$ {' '.join(args)}")

                    proc = await asyncio.create_subprocess_exec(
                        *args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        env=env,
                    )
                    job.process = proc

                    async def _pump(proc: asyncio.subprocess.Process = proc) -> int:
                        assert proc.stdout is not None
                        async for raw_line in proc.stdout:
                            job.append_log(raw_line.decode(errors="replace").rstrip("\n"))
                        return await proc.wait()

                    try:
                        rc = await asyncio.wait_for(_pump(), timeout=_INSTALL_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        job.append_log("install timed out; killing process")
                        proc.kill()
                        await proc.wait()
                        job.status = "failed"
                        job.error = "timed out"
                        job.finished_at = time.time()
                        return

                    if job.status == "cancelled":
                        # cancel() already finalized the job while we were waiting
                        # on the process; don't clobber its terminal state.
                        return

                    if rc == 0:
                        break  # success - no need to try a fallback spelling

                if rc == 0:
                    try:
                        job.result = opt.activate()
                        job.status = "success"
                    except Exception as e:  # noqa: BLE001
                        job.status = "failed"
                        job.error = f"install succeeded but activation failed: {e}"
                        job.append_log(job.error)
                else:
                    job.status = "failed"
                    job.error = f"pip exited with status {rc}"
            except asyncio.CancelledError:
                job.status = "cancelled"
                raise
            except Exception as e:  # noqa: BLE001
                job.status = "failed"
                job.error = str(e)
                job.append_log(f"install failed: {e}")
                logger.error(f"[OptimizationInstaller] install of {opt.id} failed: {e}")
            finally:
                if job.status == "running":
                    job.status = "failed"
                    job.error = "install ended without a terminal status"
                job.finished_at = time.time()

    async def cancel(self) -> bool:
        """Terminate the running job, if any. Returns True if a job was cancelled."""
        job = self._job
        if job is None or job.status != "running" or job.process is None:
            return False

        proc = job.process
        job.append_log("cancelling install...")
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=_CANCEL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

        job.status = "cancelled"
        job.finished_at = time.time()
        job.append_log("install cancelled")
        return True


installer = OptimizationInstaller()
