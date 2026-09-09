"""The single background pass that applies the retention windows.

One task list, run once shortly after startup and then daily, and on demand
from the admin panel. A pass never overlaps another: the admin endpoint is
refused while one is in flight, and the daily tick skips its turn rather than
queueing behind it.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from src.features.housekeeping.settings import (
    SETTING_LLM_TRACE_DAYS,
    SETTING_RUN_REPORT_DAYS,
    SETTING_TMP_DAYS,
    load_retention,
)
from src.features.housekeeping.tasks import (
    prune_llm_traces,
    prune_run_reports,
    prune_tmp,
    scan_tmp,
)

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS = 60.0
INTERVAL_SECONDS = 24 * 60 * 60


class HousekeepingRunning(Exception):
    """A housekeeping pass is already in flight."""


@dataclass(frozen=True)
class HousekeepingTask:
    """One pruning pass and the estimate of what it would remove."""

    name: str
    setting_key: str
    run: Callable[[int], Dict[str, Any]]
    preview: Callable[[int], Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_tasks(settings, run_report_repository, run_report_recorder, trace_repository) -> List[HousekeepingTask]:
    """The passes that ship with the app, in the order they run."""
    return [
        HousekeepingTask(
            name="tmp",
            setting_key=SETTING_TMP_DAYS,
            run=lambda days: prune_tmp(settings.get_tmp_directory(), days),
            preview=lambda days: scan_tmp(settings.get_tmp_directory(), days),
        ),
        HousekeepingTask(
            name="run_reports",
            setting_key=SETTING_RUN_REPORT_DAYS,
            run=lambda days: prune_run_reports(run_report_repository, run_report_recorder, days),
            preview=lambda days: run_report_repository.count_older_than(days) if days > 0 else 0,
        ),
        HousekeepingTask(
            name="llm_traces",
            setting_key=SETTING_LLM_TRACE_DAYS,
            run=lambda days: prune_llm_traces(trace_repository, days),
            preview=lambda days: trace_repository.count_older_than(days) if days > 0 else 0,
        ),
    ]


class HousekeepingWorker:
    """Holds the task list, the schedule, and the last pass's summary."""

    def __init__(
        self,
        settings,
        tasks: List[HousekeepingTask],
        *,
        initial_delay_seconds: float = INITIAL_DELAY_SECONDS,
        interval_seconds: float = INTERVAL_SECONDS,
    ):
        self.settings = settings
        self.tasks = list(tasks)
        self.initial_delay_seconds = initial_delay_seconds
        self.interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._running = False
        self._last_run: Optional[Dict[str, Any]] = None
        self._next_run_at: Optional[str] = None
        self._loop_task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def last_run(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._last_run

    @property
    def next_run_at(self) -> Optional[str]:
        return self._next_run_at

    def retention(self) -> Dict[str, int]:
        return load_retention(self.settings)

    def preview(self) -> Dict[str, Any]:
        """What every task would remove under the settings in force now.

        A task whose estimate fails reports the same shape a disabled window
        does, so the panel never has to render a hole.
        """
        retention = self.retention()
        preview: Dict[str, Any] = {}
        for task in self.tasks:
            try:
                preview[task.name] = task.preview(retention[task.setting_key])
            except Exception as e:
                logger.warning("Housekeeping preview for %s failed: %s", task.name, e)
                preview[task.name] = task.preview(0)
        return preview

    def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._schedule(self.initial_delay_seconds)
        self._loop_task = asyncio.create_task(self._loop())
        logger.info(
            "Housekeeping worker started (first pass in %ss, then every %ss)",
            self.initial_delay_seconds,
            self.interval_seconds,
        )

    async def stop(self) -> None:
        task = self._loop_task
        self._loop_task = None
        self._next_run_at = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def run_now(self) -> Dict[str, Any]:
        """Run every task once, off the event loop. Raises
        `HousekeepingRunning` when a pass is already in flight."""
        self._claim()
        summary: Optional[Dict[str, Any]] = None
        try:
            summary = await asyncio.to_thread(self._run_pass)
        finally:
            with self._lock:
                self._running = False
                if summary is not None:
                    self._last_run = summary
        return summary

    def _claim(self) -> None:
        with self._lock:
            if self._running:
                raise HousekeepingRunning("A housekeeping pass is already running")
            self._running = True

    def _schedule(self, seconds: float) -> None:
        self._next_run_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    async def _loop(self) -> None:
        await asyncio.sleep(self.initial_delay_seconds)
        while True:
            try:
                await self.run_now()
            except HousekeepingRunning:
                logger.info("Skipping the scheduled housekeeping pass: one is already running")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Housekeeping pass failed: %s", e, exc_info=True)
            self._schedule(self.interval_seconds)
            await asyncio.sleep(self.interval_seconds)

    def _run_pass(self) -> Dict[str, Any]:
        started_at = _now()
        retention = self.retention()
        results: Dict[str, Any] = {}
        errors: List[str] = []
        for task in self.tasks:
            try:
                results[task.name] = task.run(retention[task.setting_key])
            except Exception as e:
                logger.error("Housekeeping task %s failed: %s", task.name, e, exc_info=True)
                results[task.name] = {"errors": [str(e)]}
                errors.append(f"{task.name}: {e}")
        return {
            "started_at": started_at,
            "finished_at": _now(),
            "tasks": results,
            "errors": errors,
        }
