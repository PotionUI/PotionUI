import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

ACTIVE = ("queued", "running")
KEEP_FINISHED = 200


@dataclass
class BackfillJob:
    id: str
    user_id: str
    rule_id: str
    rule_name: str
    status: str = "queued"
    total: int = 0
    processed: int = 0
    applied: int = 0
    run_id: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    cancel_requested: bool = False
    created_at: str = field(default_factory=now_iso)

    def start(self) -> None:
        self.status = "running"
        self.started_at = now_iso()

    def finish(self, status: str) -> None:
        self.status = status
        self.finished_at = now_iso()

    def fail(self, message: str) -> None:
        self.error = message
        self.finish("failed")

    def request_cancel(self) -> None:
        self.cancel_requested = True
        if self.status == "queued":
            self.finish("cancelled")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "status": self.status,
            "total": self.total,
            "processed": self.processed,
            "applied": self.applied,
            "run_id": self.run_id,
            "error": self.error,
            "started_at": self.started_at or self.created_at,
            "finished_at": self.finished_at,
        }


class JobBook:

    def __init__(self):
        self._jobs: Dict[str, BackfillJob] = {}
        self._lock = threading.Lock()

    def create(self, user_id: str, rule_id: str, rule_name: str) -> BackfillJob:
        job = BackfillJob(id=generate_ulid(), user_id=user_id, rule_id=rule_id, rule_name=rule_name)
        with self._lock:
            self._jobs[job.id] = job
            finished = sorted((j for j in self._jobs.values() if j.status not in ACTIVE), key=lambda j: j.id)
            for old in finished[:max(0, len(finished) - KEEP_FINISHED)]:
                self._jobs.pop(old.id, None)
        return job

    def get(self, user_id: str, job_id: str) -> Optional[BackfillJob]:
        job = self._jobs.get(job_id)
        return job if job is not None and job.user_id == user_id else None

    def for_user(self, user_id: str, active_only: bool) -> List[BackfillJob]:
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.user_id == user_id]
        if active_only:
            jobs = [j for j in jobs if j.status in ACTIVE]
        return sorted(jobs, key=lambda j: j.id, reverse=True)

    def active_for_rule(self, user_id: str, rule_id: str) -> Optional[BackfillJob]:
        return next((j for j in self.for_user(user_id, True) if j.rule_id == rule_id), None)

    def active_for_run(self, user_id: str, run_id: str) -> Optional[BackfillJob]:
        return next((j for j in self.for_user(user_id, True) if j.run_id == run_id), None)

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status in ACTIVE)

    def cancel_for_user(self, user_id: str) -> None:
        for job in self.for_user(user_id, True):
            job.request_cancel()

    def cancel_all(self) -> None:
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.status in ACTIVE]
        for job in jobs:
            job.request_cancel()
