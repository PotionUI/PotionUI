import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.features.cloud.http import CloudHttp
from src.features.cloud.testing.fake import FakeClock

SECRET = "sk-test-form-0123456789"


@pytest.fixture
async def server():
    seen = []

    async def form(request):
        parts = []
        reader = await request.multipart()
        async for part in reader:
            parts.append((part.name, part.filename, await part.read()))
        seen.append({"type": request.content_type, "auth": request.headers.get("Authorization"), "parts": parts})
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/form", form)
    test_server = TestServer(app, host="127.0.0.1")
    await test_server.start_server()
    try:
        yield str(test_server.make_url("")).rstrip("/"), seen
    finally:
        await test_server.close()


async def test_a_form_body_is_sent_as_multipart_with_the_key(server):
    url, seen = server
    body = aiohttp.FormData()
    body.add_field("prompt", "a lighthouse")
    body.add_field("image[]", b"one", filename="a.png", content_type="image/png")
    body.add_field("image[]", b"two", filename="b.png", content_type="image/png")
    http = CloudHttp(url, auth_headers={"Authorization": f"Bearer {SECRET}"}, timeout_s=5, clock=FakeClock())
    try:
        answer = await http.request_json("POST", "/form", data=body)
    finally:
        await http.close()

    assert answer == {"ok": True}
    (request,) = seen
    assert request["type"] == "multipart/form-data"
    assert request["auth"] == f"Bearer {SECRET}"
    assert request["parts"] == [("prompt", None, b"a lighthouse"), ("image[]", "a.png", b"one"), ("image[]", "b.png", b"two")]
