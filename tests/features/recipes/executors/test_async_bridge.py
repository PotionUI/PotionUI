import asyncio
import threading

import pytest

from src.features.recipes.executors._async_bridge import run_sync


async def _current_loop():
    return asyncio.get_running_loop()


@pytest.fixture
def app_loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


def test_a_coroutine_runs_on_the_given_loop_from_a_thread_without_one(app_loop):
    assert run_sync(_current_loop(), loop=app_loop) is app_loop


def test_a_coroutine_runs_on_a_loop_of_its_own_without_a_given_loop(app_loop):
    assert run_sync(_current_loop()) is not app_loop


def test_a_given_loop_that_is_not_running_is_ignored():
    idle = asyncio.new_event_loop()
    try:
        assert run_sync(_current_loop(), loop=idle) is not idle
    finally:
        idle.close()


@pytest.mark.asyncio
async def test_a_call_from_inside_the_given_loop_does_not_block_it():
    here = asyncio.get_running_loop()

    assert run_sync(_current_loop(), loop=here) is not here
