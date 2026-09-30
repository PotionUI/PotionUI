import socket

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.features.cloud.http import CloudHttp
from src.features.cloud.testing.fake import FakeBehaviour, FakeClock, FakeCloudConfig, FakeCloudProvider
from src.pipelines.cloud import CloudRunError
from tests.features.cloud.test_cloud_run_session import Harness


class PostingProvider(FakeCloudProvider):
    attempts = 0

    async def submit(self, request):
        type(self).attempts += 1
        await self.http.request_json("POST", "/submit", json={"prompt": request.prompt})
        raise AssertionError("the scripted server never accepts")


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def server():
    hits = []

    async def submit(request):
        hits.append(1)
        return web.json_response({"error": "down"}, status=503)

    async def reset_after_read(request):
        await request.read()
        hits.append(1)
        request.transport.close()
        return web.Response()

    app = web.Application()
    app.router.add_post("/submit", submit)
    app.router.add_post("/reset", reset_after_read)
    test_server = TestServer(app, host="127.0.0.1")
    await test_server.start_server()
    try:
        yield str(test_server.make_url("")).rstrip("/"), hits
    finally:
        await test_server.close()


def harness_with(tmp_path, base_url, provider_class=PostingProvider):
    h = Harness(tmp_path, behaviour=FakeBehaviour())
    http = CloudHttp(base_url, timeout_s=5, clock=FakeClock(), allow_private_targets=True)
    provider = provider_class(FakeCloudConfig(id="fake-1", name="Fake"), http, clock=h.clock, behaviour=h.provider.behaviour)
    h.provider = provider
    h.session._provider = provider
    provider_class.attempts = 0
    return h, http


async def test_a_5xx_after_the_request_was_sent_is_not_resubmitted(server, tmp_path):
    url, hits = server
    h, http = harness_with(tmp_path, url)

    with pytest.raises(CloudRunError) as raised:
        await h.run()
    await http.close()

    assert raised.value.kind == "unavailable"
    assert PostingProvider.attempts == 1 and len(hits) == 1


async def test_a_connection_dropped_after_sending_is_not_resubmitted(server, tmp_path):
    url, hits = server

    class Resetting(PostingProvider):
        async def submit(self, request):
            type(self).attempts += 1
            await self.http.request_json("POST", "/reset", json={})

    h, http = harness_with(tmp_path, url, Resetting)

    with pytest.raises(CloudRunError) as raised:
        await h.run()
    await http.close()

    assert raised.value.kind == "unavailable"
    assert Resetting.attempts == 1 and len(hits) == 1


async def test_a_refused_connection_is_retried_because_nothing_was_sent(tmp_path):
    h, http = harness_with(tmp_path, f"http://127.0.0.1:{unused_port()}")

    with pytest.raises(CloudRunError) as raised:
        await h.run()
    await http.close()

    assert raised.value.kind == "unavailable"
    assert PostingProvider.attempts == 3


async def test_http_marks_what_was_sent(server, tmp_path):
    url, _ = server
    http = CloudHttp(url, timeout_s=5, clock=FakeClock(), allow_private_targets=True)
    refused = CloudHttp(f"http://127.0.0.1:{unused_port()}", timeout_s=5, clock=FakeClock(), allow_private_targets=True)
    try:
        with pytest.raises(Exception) as after_send:
            await http.request_json("POST", "/submit", json={})
        with pytest.raises(Exception) as never_sent:
            await refused.request_json("POST", "/submit", json={})
    finally:
        await http.close()
        await refused.close()

    assert after_send.value.request_sent is True
    assert never_sent.value.request_sent is False
