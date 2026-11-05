import logging
from typing import Any, Dict, List

from src.features.organize import errors
from src.features.organize.evaluator import evaluate
from src.features.organize.jobs import BackfillJob
from src.features.organize.manager import (
    BACKFILL_CHUNK,
    NOTICE_JOB_FINISHED,
    NOTICE_JOB_PROGRESS,
    PACED_CHUNK,
    OrganizeManager,
    RetryTotals,
    Viewer,
    viewer_of,
)

logger = logging.getLogger(__name__)


class BackfillManager:

    def __init__(self, organize: OrganizeManager):
        self.o = organize

    def start(self, user: Any, rule_id: str) -> Dict[str, Any]:
        viewer = viewer_of(user)
        rule = self.o.require_rule(viewer.id, rule_id)
        if self.o.is_paused_for(viewer.id):
            raise errors.organize_paused()
        running = self.o.jobs.active_for_rule(viewer.id, rule_id)
        if running is not None:
            raise errors.OrganizeError("job_running", "This rule is already being applied", 409, {"job_id": running.id})
        reason = self.o.blocking_reason(rule, viewer.id, viewer.is_admin, self.o._restricted(viewer.id))
        if reason:
            raise errors.OrganizeError("rule_not_runnable", "Fix what this rule needs before applying it", 422,
                                       {"reason": reason})
        job = self.o.jobs.create(viewer.id, rule.id, rule.name)
        self.o.executor.submit(self._run, job, viewer)
        return job.to_dict()

    def retry_waiting(self, user: Any, run_id: str) -> Dict[str, Any]:
        viewer = viewer_of(user)
        run = self.o.c.runs.get_run(viewer.id, run_id)
        if run is None:
            raise errors.OrganizeError("run_not_found", "Run not found", 404)
        rule = self.o.require_rule(viewer.id, run.rule_id)
        if self.o.is_paused_for(viewer.id):
            raise errors.organize_paused()
        running = self.o.jobs.active_for_rule(viewer.id, rule.id)
        if running is not None:
            raise errors.OrganizeError("job_running", "This rule is already being applied", 409, {"job_id": running.id})
        waiting = [o.item_id for o in self.o.c.outcomes.waiting_for_run(run_id)]
        if not waiting:
            raise errors.OrganizeError("nothing_waiting", "Nothing is waiting in this run", 409)
        reason = self.o.blocking_reason(rule, viewer.id, viewer.is_admin, self.o._restricted(viewer.id))
        if reason:
            raise errors.OrganizeError("rule_not_runnable", "Fix what this rule needs before applying it", 422,
                                       {"reason": reason})
        job = self.o.jobs.create(viewer.id, rule.id, rule.name, kind="retry")
        job.total = len(waiting)
        self.o.executor.submit(self._retry, job, viewer, waiting)
        return job.to_dict()

    def _retry(self, job: BackfillJob, viewer: Viewer, item_ids: List[str]) -> None:
        try:
            self._retry_fill(job, viewer, item_ids)
        except Exception:
            logger.exception("Auto-organize retry %s failed", job.id)
            job.fail("Trying again stopped because of an error")

    def _retry_fill(self, job: BackfillJob, viewer: Viewer, item_ids: List[str]) -> None:
        if job.status == "cancelled":
            return
        rule = self.o.c.rules.get(viewer.id, job.rule_id)
        if rule is None:
            job.fail("The rule was deleted")
            return
        job.start()
        run = self.o.c.runs.create_run(rule.id, rule.name, viewer.id, rule.subject, "backfill", started_by=viewer.actor)
        job.run_id = run.id

        def progress(totals: RetryTotals) -> None:
            job.processed = totals.processed
            job.applied = totals.changed
            job.skipped = totals.skipped
            job.waiting = totals.waiting

        totals = self.o.retry_items(
            rule, item_ids, lambda: run, progress=progress,
            cancelled=lambda: job.cancel_requested or self.o.is_paused_for(viewer.id),
        )
        status = "cancelled" if job.cancel_requested or totals.processed < len(item_ids) else "completed"
        if totals.blocked:
            status = "failed"
            job.error = "Something this rule needs is no longer available"
        self.o.c.runs.finish_run(viewer.id, run.id, status)
        job.finish(status)

    def _run(self, job: BackfillJob, viewer: Viewer) -> None:
        try:
            self._fill(job, viewer)
        except Exception:
            logger.exception("Auto-organize backfill %s failed", job.id)
            job.fail("Applying the rule stopped because of an error")
            if job.run_id:
                self.o.c.runs.finish_run(viewer.id, job.run_id, "failed")

    def _fill(self, job: BackfillJob, viewer: Viewer) -> None:
        if job.status == "cancelled":
            return
        rule = self.o.c.rules.get(viewer.id, job.rule_id)
        if rule is None:
            job.fail("The rule was deleted")
            return
        job.start()
        restricted = self.o._restricted(viewer.id)
        ids, _approximate = self.o.matching_ids(viewer, rule.subject, rule.match, rule.conditions, None)
        handled = self.o.c.rules.handled_ids(rule.id, rule.subject, ids)
        ids = [i for i in ids if i not in handled]
        job.total = len(ids)
        run = self.o.c.runs.create_run(rule.id, rule.name, viewer.id, rule.subject, "backfill", started_by=viewer.actor)
        job.run_id = run.id
        chunk_size = PACED_CHUNK if self.o.rule_is_paced(rule) else BACKFILL_CHUNK
        status = "completed"
        for start in range(0, len(ids), chunk_size):
            if job.cancel_requested or self.o.is_paused_for(viewer.id):
                status = "cancelled"
                break
            chunk = ids[start:start + chunk_size]
            blocked = False
            seen = 0
            for item in self.o.load_items(rule.subject, viewer.id, chunk, viewer.is_admin, restricted):
                if not evaluate(self.o.c.registry, rule.match, rule.conditions, item):
                    continue
                result = self.o.apply(rule, item, lambda: run, bump=False)
                if result.blocked:
                    blocked = True
                    break
                seen += 1
                if result.changed:
                    job.applied += 1
                job.skipped += result.skipped
                if result.waiting:
                    job.waiting += 1
                job.processed = min(job.total, start + seen)
            if rule.subject == "generation":
                self.o.c.writer.bump_history(viewer.id)
            job.processed = min(job.total, start + len(chunk))
            self.o.notify(user_id=viewer.id or viewer.actor, level="info", title="Auto-organize",
                         message=f"Applying \"{rule.name}\"", type=NOTICE_JOB_PROGRESS, metadata=job.to_dict(),
                         transient=True, show_toast=False)
            if blocked:
                status = "failed"
                job.error = "Something this rule needs is no longer available"
                break
        self.o.c.runs.finish_run(viewer.id, run.id, status)
        job.finish(status)
        if status != "failed":
            self.o.notify(user_id=viewer.id or viewer.actor, level="success", title="Auto-organize",
                         message=self._finished_message(viewer.id, run.id, job.applied, job.skipped, job.waiting),
                         type=NOTICE_JOB_FINISHED, metadata=job.to_dict())

    def _finished_message(self, user_id: Any, run_id: str, applied: int, skipped: int = 0, waiting: int = 0) -> str:
        left_out = []
        if skipped:
            left_out.append(f"{skipped} skipped")
        if waiting:
            left_out.append(f"{waiting} waiting")
        suffix = f" ({', '.join(left_out)})" if left_out else ""
        if not applied:
            return ("Nothing new to add" if not left_out else f"Nothing changed{suffix}")
        summary = self.o.c.runs.run_change_summary(user_id, [run_id]).get(run_id, [])
        targets = [row["target_name"] for row in summary if row["target_type"] in ("collection", "model_collection")]
        noun = "item" if applied == 1 else "items"
        if targets:
            return f"Added {applied} {noun} to {', '.join(dict.fromkeys(targets))}{suffix}"
        groups = [row["target_name"] for row in summary if row["target_type"] == "user_group"]
        if groups:
            model_noun = "model" if applied == 1 else "models"
            return f"Gave {', '.join(dict.fromkeys(groups))} access to {applied} {model_noun}{suffix}"
        labels = []
        for row in summary:
            definition = self.o.c.registry.action(row["action_kind"])
            if definition is not None and definition.done_label:
                labels.append(definition.done_label)
        if labels:
            return f"{', '.join(dict.fromkeys(labels))}: {applied} {noun}{suffix}"
        return f"Organized {applied} {noun}{suffix}"

    def list_jobs(self, user: Any, active: bool) -> List[Dict[str, Any]]:
        return [job.to_dict() for job in self.o.jobs.for_user(viewer_of(user).id, active)]

    def get_job(self, user: Any, job_id: str) -> Dict[str, Any]:
        job = self.o.jobs.get(viewer_of(user).id, job_id)
        if job is None:
            raise errors.OrganizeError("job_not_found", "Job not found", 404)
        return job.to_dict()

    def cancel_job(self, user: Any, job_id: str) -> Dict[str, Any]:
        job = self.o.jobs.get(viewer_of(user).id, job_id)
        if job is None:
            raise errors.OrganizeError("job_not_found", "Job not found", 404)
        job.request_cancel()
        return job.to_dict()
