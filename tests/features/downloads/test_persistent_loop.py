"""
Unit tests for `PersistentLoop`: the background thread+loop the
download worker's queue consumer runs on so it survives any single throwaway
loop that happened to start it (see `persistent_loop.py` module docstring).
"""

import asyncio
import time

import pytest

from src.features.downloads.persistent_loop import PersistentLoop


@pytest.fixture
def loop_holder():
    holder = PersistentLoop("test-persistent-loop")
    yield holder
    holder.shutdown()


class TestPersistentLoop:
    def test_not_alive_before_first_use(self, loop_holder):
        assert loop_holder.is_alive() is False

    def test_ensure_running_starts_the_thread(self, loop_holder):
        loop = loop_holder.ensure_running()

        assert loop_holder.is_alive() is True
        assert loop.is_running() is True

    def test_ensure_running_is_idempotent(self, loop_holder):
        """Calling it twice while the thread is alive returns the SAME loop
        - callers must not get a fresh loop (and therefore a fresh, empty
        queue) on every single call."""
        first = loop_holder.ensure_running()
        second = loop_holder.ensure_running()

        assert first is second

    def test_runs_scheduled_coroutines(self, loop_holder):
        loop = loop_holder.ensure_running()

        async def add(a, b):
            return a + b

        future = asyncio.run_coroutine_threadsafe(add(2, 3), loop)
        assert future.result(timeout=5) == 5

    def test_shutdown_stops_the_thread(self, loop_holder):
        loop_holder.ensure_running()
        assert loop_holder.is_alive() is True

        loop_holder.shutdown()

        assert loop_holder.is_alive() is False

    def test_self_heals_after_the_thread_dies(self, loop_holder):
        """The exact self-healing contract required here: if whatever was
        running the loop goes away, the next `ensure_running()` transparently
        gets a working replacement instead of a loop nobody is pumping."""
        first_loop = loop_holder.ensure_running()

        # Simulate the thread dying out from under it (crash, `stop()` called
        # by something else, etc.) without going through `shutdown()`.
        first_loop.call_soon_threadsafe(first_loop.stop)
        deadline = time.monotonic() + 5
        while loop_holder.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert loop_holder.is_alive() is False

        second_loop = loop_holder.ensure_running()

        assert loop_holder.is_alive() is True
        assert second_loop is not first_loop

        async def ping():
            return "pong"

        future = asyncio.run_coroutine_threadsafe(ping(), second_loop)
        assert future.result(timeout=5) == "pong"
