"""The worker: one pass at a time, a remembered summary, and a schedule that
does not block startup."""

import asyncio
import threading
from unittest.mock import Mock

import pytest

from src.features.housekeeping.settings import SETTING_TMP_DAYS
from src.features.housekeeping.worker import (
    HousekeepingRunning,
    HousekeepingTask,
    HousekeepingWorker,
)


def _settings(**values):
    settings = Mock()
    settings.get_setting.side_effect = lambda key, default=None, user_id=None: values.get(key, default)
    return settings


def _task(name="tmp", setting_key=SETTING_TMP_DAYS, run=None, preview=None):
    return HousekeepingTask(
        name=name,
        setting_key=setting_key,
        run=run or (lambda days: {"removed": days, "errors": []}),
        preview=preview or (lambda days: {"files": days, "bytes": days * 10}),
    )


class TestRunNow:

    async def test_a_pass_records_its_tasks_and_becomes_the_last_run(self):
        worker = HousekeepingWorker(_settings(tmp_retention_days=5), [_task()])

        summary = await worker.run_now()

        assert summary["tasks"] == {"tmp": {"removed": 5, "errors": []}}
        assert summary["errors"] == []
        assert summary["started_at"] <= summary["finished_at"]
        assert worker.last_run == summary
        assert worker.running is False

    async def test_last_run_is_empty_until_a_pass_has_finished(self):
        worker = HousekeepingWorker(_settings(), [_task()])

        assert worker.last_run is None

    async def test_a_second_pass_is_refused_while_the_first_is_in_flight(self):
        release = threading.Event()
        started = threading.Event()

        def block(days):
            started.set()
            release.wait(5)
            return {"removed": 0, "errors": []}

        worker = HousekeepingWorker(_settings(), [_task(run=block)])
        first = asyncio.create_task(worker.run_now())
        await asyncio.to_thread(started.wait, 5)

        assert worker.running is True
        with pytest.raises(HousekeepingRunning):
            await worker.run_now()

        release.set()
        await first
        assert worker.running is False

    async def test_a_task_that_raises_is_recorded_and_the_rest_still_run(self):
        def explode(days):
            raise RuntimeError("disk gone")

        worker = HousekeepingWorker(
            _settings(), [_task(name="tmp", run=explode), _task(name="llm_traces")]
        )

        summary = await worker.run_now()

        assert summary["errors"] == ["tmp: disk gone"]
        assert summary["tasks"]["tmp"] == {"errors": ["disk gone"]}
        assert "llm_traces" in summary["tasks"]
        assert worker.running is False


class TestPreview:

    def test_preview_asks_every_task_what_the_current_window_would_remove(self):
        worker = HousekeepingWorker(_settings(tmp_retention_days=3), [_task()])

        assert worker.preview() == {"tmp": {"files": 3, "bytes": 30}}

    def test_a_preview_that_raises_falls_back_to_the_disabled_shape(self):
        def explode(days):
            if days > 0:
                raise RuntimeError("no such directory")
            return {"files": 0, "bytes": 0}

        worker = HousekeepingWorker(_settings(), [_task(preview=explode)])

        assert worker.preview() == {"tmp": {"files": 0, "bytes": 0}}


class TestSchedule:

    async def test_start_returns_immediately_and_the_first_pass_lands_after_the_delay(self):
        ran = threading.Event()
        worker = HousekeepingWorker(
            _settings(),
            [_task(run=lambda days: ran.set() or {"removed": 0, "errors": []})],
            initial_delay_seconds=0.01,
            interval_seconds=60,
        )

        worker.start()
        assert ran.is_set() is False
        assert worker.next_run_at is not None

        await asyncio.sleep(0.2)
        assert ran.is_set() is True
        assert worker.last_run is not None

        await worker.stop()

    async def test_start_twice_schedules_only_one_loop(self):
        worker = HousekeepingWorker(_settings(), [_task()], initial_delay_seconds=60)

        worker.start()
        first = worker._loop_task
        worker.start()

        assert worker._loop_task is first
        await worker.stop()

    async def test_stop_cancels_the_schedule(self):
        worker = HousekeepingWorker(_settings(), [_task()], initial_delay_seconds=60)
        worker.start()
        task = worker._loop_task

        await worker.stop()

        assert task.cancelled() or task.done()
        assert worker.next_run_at is None
