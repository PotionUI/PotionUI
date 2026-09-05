"""Tests for the bounded off-loop boundary the history endpoints run through."""

import asyncio
import inspect
import time
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from src.features.generation.exceptions import InvalidDateFilterException
from src.features.generation.history_executor import (
    HistoryExecutor,
    HistoryExecutorSaturated,
)
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.routes import GenerationController


SLOW_CALL_SECONDS = 0.3
TICK_SECONDS = 0.02
MAX_ACCEPTABLE_GAP_SECONDS = 0.1


def make_controller(facade: GenerationHistoryFacade) -> GenerationController:
    return GenerationController(
        generation_orchestrator=Mock(),
        generation_history_facade=facade,
        file_service=Mock(),
        run_report_recorder=Mock(),
    )


def make_facade() -> GenerationHistoryFacade:
    return GenerationHistoryFacade(
        generation_repo=Mock(),
        file_service=Mock(),
        plugin_registry=Mock(),
    )


async def tick_until(stop: asyncio.Event) -> float:
    """Tick every TICK_SECONDS until `stop`; return the largest gap observed."""
    max_gap = 0.0
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(TICK_SECONDS)
        now = time.perf_counter()
        max_gap = max(max_gap, now - last)
        last = now
    return max_gap


class TestLoopIsNotBlocked:
    """The history unit of work must not stall the event loop."""

    async def test_history_request_does_not_block_the_loop(self):
        facade = make_facade()
        payload = {"generations": [{"id": "gen-1"}], "total": 1}

        def slow_get_history(*args, **kwargs):
            time.sleep(SLOW_CALL_SECONDS)
            return payload

        facade._query.get_history = slow_get_history
        controller = make_controller(facade)

        stop = asyncio.Event()
        ticker = asyncio.create_task(tick_until(stop))
        await asyncio.sleep(0)

        response = await controller.get_generation_history(current_user=Mock(id="u1"))

        stop.set()
        max_gap = await ticker
        facade.shutdown()

        assert response.data == payload
        assert max_gap < MAX_ACCEPTABLE_GAP_SECONDS, (
            f"event loop stalled for {max_gap:.3f}s during a history request"
        )

    async def test_export_zip_does_not_block_the_loop(self):
        import tempfile

        facade = make_facade()
        zip_content = b"zip-bytes"

        def slow_export(*args, **kwargs):
            time.sleep(SLOW_CALL_SECONDS)
            spooled = tempfile.SpooledTemporaryFile()
            spooled.write(zip_content)
            spooled.seek(0)
            return spooled, "export.zip"

        facade._archive.export_zip = slow_export
        controller = make_controller(facade)

        stop = asyncio.Event()
        ticker = asyncio.create_task(tick_until(stop))
        await asyncio.sleep(0)

        response = await controller.export_generations(
            generation_ids=["gen-1"], strip_metadata=False, current_user=Mock(id="u1")
        )

        stop.set()
        max_gap = await ticker
        facade.shutdown()

        assert response.headers["content-length"] == str(len(zip_content))
        assert max_gap < MAX_ACCEPTABLE_GAP_SECONDS


class TestResponseParity:
    """Moving the work off-loop must not change what the endpoint returns."""

    async def test_successful_history_returns_the_query_payload(self):
        facade = make_facade()
        payload = {"generations": [], "total": 0, "filters": {"status": "completed"}}
        facade._query.get_history = Mock(return_value=payload)
        controller = make_controller(facade)

        response = await controller.get_generation_history(
            current_user=Mock(id="u1"), status="completed", tag_ids="a, b"
        )
        facade.shutdown()

        assert response.success is True
        assert response.data == payload

        # The facade forwards positionally, so bind against the real signature
        # rather than reading a kwarg that is never passed as one.
        call = facade._query.get_history.call_args
        bound = inspect.signature(GenerationHistoryQuery.get_history).bind(
            facade._query, *call.args, **call.kwargs
        )
        assert bound.arguments["tag_ids"] == ["a", "b"]
        assert bound.arguments["status"] == "completed"
        assert bound.arguments["user_id"] == "u1"

    async def test_invalid_date_filter_still_maps_to_400(self):
        facade = make_facade()
        facade._query.get_history = Mock(
            side_effect=InvalidDateFilterException("bad date")
        )
        controller = make_controller(facade)

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_generation_history(current_user=Mock(id="u1"))
        facade.shutdown()

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "invalid_date_format"

    async def test_unexpected_failure_still_maps_to_history_fetch_failed(self):
        facade = make_facade()
        facade._query.get_history = Mock(side_effect=RuntimeError("boom"))
        controller = make_controller(facade)

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_generation_history(current_user=Mock(id="u1"))
        facade.shutdown()

        assert exc_info.value.detail["error"] == "history_fetch_failed"


class TestAdmissionControl:
    """Capacity is bounded and refusals are immediate."""

    async def test_overflow_callers_are_refused_and_the_rest_succeed(self):
        executor = HistoryExecutor(max_workers=1, max_pending=1)
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        def blocked():
            asyncio.run_coroutine_threadsafe(_wait(release), loop).result(5)
            return "done"

        async def _wait(event):
            await event.wait()

        capacity = 2
        admitted = [asyncio.create_task(executor.run(blocked)) for _ in range(capacity)]
        await asyncio.sleep(0.05)

        with pytest.raises(HistoryExecutorSaturated):
            await executor.run(blocked)

        release.set()
        assert await asyncio.gather(*admitted) == ["done"] * capacity
        executor.shutdown()

    async def test_refusal_reaches_the_client_as_503(self):
        facade = make_facade()
        facade.executor = HistoryExecutor(max_workers=1, max_pending=0)
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def _wait(event):
            await event.wait()

        def blocked(*args, **kwargs):
            asyncio.run_coroutine_threadsafe(_wait(release), loop).result(5)
            return {"generations": [], "total": 0}

        facade._query.get_history = blocked
        controller = make_controller(facade)

        first = asyncio.create_task(
            controller.get_generation_history(current_user=Mock(id="u1"))
        )
        await asyncio.sleep(0.05)

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_generation_history(current_user=Mock(id="u2"))

        release.set()
        await first
        facade.shutdown()

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["error"] == "history_busy"

    async def test_cancelled_caller_releases_its_slot(self):
        executor = HistoryExecutor(max_workers=1, max_pending=0)
        started = asyncio.Event()
        loop = asyncio.get_running_loop()

        def slow():
            loop.call_soon_threadsafe(started.set)
            time.sleep(SLOW_CALL_SECONDS)
            return "first"

        pending = asyncio.create_task(executor.run(slow))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

        # The worker is still running, so its slot is still held: releasing it
        # on cancellation instead would let a disconnecting client hand its
        # database connection to a second request.
        assert executor.inflight == 1

        deadline = time.perf_counter() + 2.0
        while executor.inflight and time.perf_counter() < deadline:
            await asyncio.sleep(0.01)

        assert executor.inflight == 0
        assert await executor.run(lambda: "second") == "second"
        executor.shutdown()


class TestShutdown:
    """Shutdown drops the pool without waiting on running work."""

    async def test_shutdown_with_pending_work_returns_promptly(self):
        executor = HistoryExecutor(max_workers=1, max_pending=8)
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def _wait(event):
            await event.wait()

        def blocked():
            asyncio.run_coroutine_threadsafe(_wait(release), loop).result(5)
            return "done"

        running = asyncio.create_task(executor.run(blocked))
        await asyncio.sleep(0.05)
        queued = asyncio.create_task(executor.run(blocked))
        await asyncio.sleep(0.05)

        started = time.perf_counter()
        executor.shutdown()
        assert time.perf_counter() - started < 0.5

        with pytest.raises(asyncio.CancelledError):
            await queued

        release.set()
        assert await running == "done"

    async def test_run_after_shutdown_is_refused(self):
        executor = HistoryExecutor()
        executor.shutdown()

        with pytest.raises(HistoryExecutorSaturated):
            await executor.run(lambda: "never")
