from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from src.platform.util.ids import generate_ulid

MAX_FINISHED_JOBS = 200
DEFAULT_WORKERS = 2

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
FINISHED_STATES = (DONE, FAILED, CANCELLED)


@dataclass
class ConvertJob:
    id: str
    status: str = QUEUED
    progress: float = 0.0
    result: Optional[Path] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 4),
            "result": str(self.result) if self.result else None,
            "error": self.error,
        }


class ConvertJobRegistry:
    def __init__(self, workers: int = DEFAULT_WORKERS):
        self._jobs: dict[str, ConvertJob] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="export-convert")

    def submit(self, work: Callable[[threading.Event, Callable[[float], None]], Path]) -> ConvertJob:
        job = ConvertJob(id=generate_ulid())
        with self._lock:
            self._jobs[job.id] = job
            self._trim()
        self._pool.submit(self._run, job, work)
        return job

    def _run(self, job: ConvertJob, work) -> None:
        from src.features.media.export.specs import ConversionCancelled

        if job.cancel.is_set():
            job.status = CANCELLED
            return
        job.status = RUNNING
        try:
            job.result = work(job.cancel, lambda value: setattr(job, "progress", float(value)))
            job.progress = 1.0
            job.status = DONE
        except ConversionCancelled:
            job.status = CANCELLED
        except Exception as exc:
            job.error = str(exc)
            job.status = FAILED

    def get(self, job_id: str) -> Optional[ConvertJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status in FINISHED_STATES:
            return False
        job.cancel.set()
        return True

    def wait(self, job_id: str, timeout: float = 30.0) -> Optional[ConvertJob]:
        deadline = time.monotonic() + timeout
        job = self.get(job_id)
        while job is not None and job.status not in FINISHED_STATES and time.monotonic() < deadline:
            time.sleep(0.02)
        return job

    def shutdown(self) -> None:
        for job in list(self._jobs.values()):
            job.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _trim(self) -> None:
        finished = [job for job in self._jobs.values() if job.status in FINISHED_STATES]
        if len(finished) <= MAX_FINISHED_JOBS:
            return
        finished.sort(key=lambda job: job.created_at)
        for job in finished[: len(finished) - MAX_FINISHED_JOBS]:
            self._jobs.pop(job.id, None)
