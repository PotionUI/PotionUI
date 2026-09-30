import asyncio
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.features.cloud.clock import MonotonicClock
from src.features.cloud.http import CloudHttp
from src.features.cloud.testing.fake import FakeBehaviour, FakeCloudConfig, FakeCloudProvider
from src.pipelines.cloud import CloudRunCancelled, CloudRunError, CloudRunRequest
from tests.features.cloud.test_cloud_run_session import IMAGE, Harness


@pytest.fixture
async def slow_server():
    release = asyncio.Event()

    async def drip(request):
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"x" * 1000)
        await release.wait()
        return response

    app = web.Application()
    app.router.add_get("/drip", drip)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/")
    finally:
        release.set()
        await server.close()


class DownloadingProvider(FakeCloudProvider):
    base = ""

    async def fetch(self, artifact, dest, *, max_bytes=None):
        self.behaviour.calls.append("fetch")
        return await self.http.download(f"{self.base}/drip", dest, max_bytes=max_bytes)


def scratch_files(tmp_path):
    return [path for path in (tmp_path / "scratch").rglob("*") if path.is_file()]


async def test_cancel_during_a_slow_download_is_seen_within_a_second_and_leaves_nothing_behind(slow_server, tmp_path):
    h = Harness(tmp_path, behaviour=FakeBehaviour(mode="async", duration_s=1, poll_after_s=1), clock=MonotonicClock())
    http = CloudHttp(slow_server, timeout_s=30, allow_private_targets=True)
    provider = DownloadingProvider(FakeCloudConfig(id="fake-1", name="Fake"), http, clock=h.clock, behaviour=h.provider.behaviour)
    provider.base = slow_server
    h.provider = provider
    h.session._provider = provider
    h.provider.behaviour.duration_s = 0
    task = asyncio.ensure_future(h.run())
    while "fetch" not in provider.behaviour.calls:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)

    started = time.monotonic()
    await h.session.cancel()
    with pytest.raises(CloudRunCancelled):
        await task
    await http.close()

    assert time.monotonic() - started < 1.0
    assert scratch_files(tmp_path) == []


async def test_cancel_during_a_slow_poll_is_seen_within_a_second(tmp_path):
    h = Harness(tmp_path, behaviour=FakeBehaviour(mode="async", duration_s=100, poll_after_s=1), clock=MonotonicClock())

    async def stuck(job):
        await asyncio.sleep(3600)

    h.provider.poll = stuck
    task = asyncio.ensure_future(h.run())
    while "submit" not in h.provider.behaviour.calls:
        await asyncio.sleep(0.01)
    await asyncio.sleep(2.2)

    started = time.monotonic()
    await h.session.cancel()
    with pytest.raises(CloudRunCancelled):
        await task

    assert time.monotonic() - started < 1.0
    assert "cancel" in h.provider.behaviour.calls


async def test_the_deadline_during_a_slow_poll_fails_as_a_timeout(tmp_path):
    h = Harness(
        tmp_path,
        behaviour=FakeBehaviour(mode="async", duration_s=100, poll_after_s=1),
        clock=MonotonicClock(),
        timeout_seconds=3,
    )

    async def stuck(job):
        await asyncio.sleep(3600)

    h.provider.poll = stuck
    started = time.monotonic()

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "timeout"
    assert 3 <= time.monotonic() - started < 6
    assert "cancel" in h.provider.behaviour.calls


async def test_cancel_during_a_slow_submit_warns_that_a_job_may_exist(tmp_path):
    h = Harness(tmp_path, behaviour=FakeBehaviour(mode="async"), clock=MonotonicClock())

    async def stuck(request):
        await asyncio.sleep(3600)

    h.provider.submit = stuck
    task = asyncio.ensure_future(h.run())
    await asyncio.sleep(0.2)

    started = time.monotonic()
    await h.session.cancel()
    with pytest.raises(CloudRunCancelled) as raised:
        await task

    assert time.monotonic() - started < 1.0
    assert raised.value.cancel_confirmed is False
    assert any("may still finish this job and bill it" in (item.message or "") for item in h.progress)


async def test_cancelling_the_whole_run_waits_for_the_in_flight_call_to_let_go(tmp_path):
    h = Harness(tmp_path, behaviour=FakeBehaviour(mode="async", duration_s=100), clock=MonotonicClock())
    released = []

    async def stuck(request):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)
            released.append(True)
            raise

    h.provider.submit = stuck
    task = asyncio.ensure_future(h.run())
    await asyncio.sleep(0.2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert released == [True]


async def test_stopping_a_job_the_provider_cannot_cancel_stops_polling_at_once_and_warns(tmp_path):
    h = Harness(
        tmp_path,
        behaviour=FakeBehaviour(mode="async", duration_s=10_000, poll_after_s=1),
        supports_cancel=False,
        clock=MonotonicClock(),
    )
    task = asyncio.ensure_future(h.run())
    while "poll" not in h.provider.behaviour.calls:
        await asyncio.sleep(0.05)

    started = time.monotonic()
    await h.session.cancel()
    with pytest.raises(CloudRunCancelled) as raised:
        await task
    polls = h.provider.behaviour.calls.count("poll")
    await asyncio.sleep(0.3)

    assert time.monotonic() - started < 1.0 and raised.value.cancel_confirmed is False
    assert h.provider.behaviour.calls.count("poll") == polls and "cancel" not in h.provider.behaviour.calls
    assert any("may still finish this job and bill it" in (item.message or "") for item in h.progress)
