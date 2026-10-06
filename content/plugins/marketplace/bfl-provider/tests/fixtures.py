import struct
import zlib
from typing import Any, Dict, List

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

KEY = "bfl-test-key-TOPSECRET-0123456789abcdef"


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff")) + chunk(b"IEND", b"")


PNG = _png()
EXPIRED_XML = b"<?xml version=\"1.0\"?><Error><Code>AuthenticationFailed</Code><Message>Signed expiry time has passed</Message></Error>"


class BflFixture:
    def __init__(self) -> None:
        self.submit_mode = "ok"
        self.poll_mode = "ok"
        self.polls = 0
        self.polling_host = "api"
        self.credits_mode = "ok"
        self.requests: List[Dict[str, Any]] = []
        self.cdn_requests: List[Dict[str, Any]] = []
        self.regional_requests: List[Dict[str, Any]] = []
        self.api_url = ""
        self.cdn_url = ""
        self.regional_url = ""

    def record(self, bucket: List[Dict[str, Any]], request: web.Request, body: Any = None) -> None:
        bucket.append({
            "method": request.method, "path": request.path, "query": dict(request.query),
            "headers": dict(request.headers), "body": body,
        })

    def sample(self, name: str = "sample.png") -> str:
        return f"{self.cdn_url}/files/{name}?se=2026-10-06T10%3A10%3A00Z&sig=SIGNED"

    def poll_script(self) -> List[Dict[str, Any]]:
        ready = {
            "id": "task-1", "status": "Ready", "progress": None, "cost": 4.5,
            "result": {"sample": self.sample(), "seed": 1234, "prompt": "a lighthouse"},
        }
        scripts = {
            "ok": [{"id": "task-1", "status": "Pending", "progress": None},
                   {"id": "task-1", "status": "Generating", "progress": 0.5},
                   ready],
            "no_settled_cost": [{**ready, "cost": None}],
            "expired_sample": [{**ready, "result": {"sample": self.sample("expired.png")}}],
            "pending_after_running": [{"id": "task-1", "status": "Generating"}, {"id": "task-1", "status": "Pending"}],
            "request_moderated": [{"id": "task-1", "status": "Request Moderated", "result": None,
                                   "details": {"Moderation Reasons": ["Violence"]}}],
            "content_moderated": [{"id": "task-1", "status": "Pending"},
                                  {"id": "task-1", "status": "Content Moderated", "result": None,
                                   "details": {"Moderation Reasons": ["Sexual Content"]}}],
            "error": [{"id": "task-1", "status": "Pending"},
                      {"id": "task-1", "status": "Error", "details": {"error": "render crashed upstream"}}],
            "not_found": [{"id": "task-1", "status": "Task not found"}],
            "ready_without_sample": [{"id": "task-1", "status": "Ready", "result": {}}],
            "forever": [{"id": "task-1", "status": "Pending"}],
        }
        return scripts[self.poll_mode]

    def error(self, status: int, detail: Any, headers: Dict[str, str] = None) -> web.Response:
        return web.json_response({"detail": detail}, status=status, headers=headers or {})

    def polling_url(self) -> str:
        if self.polling_host == "regional":
            return f"{self.regional_url}/v1/get_result?id=task-1"
        if self.polling_host == "foreign":
            return f"{self.cdn_url}/v1/get_result?id=task-1"
        if self.polling_host == "missing":
            return ""
        return f"{self.api_url}/v1/get_result?id=task-1"

    async def submit(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.record(self.requests, request, body)
        if request.headers.get("x-key") != KEY:
            return self.error(401, f"Invalid key {request.headers.get('x-key', '')}")
        mode = self.submit_mode
        if mode == "ok":
            payload = {"id": "task-1", "polling_url": self.polling_url(), "cost": 4.0, "input_mp": 0.0, "output_mp": 1.05}
            return web.json_response(payload)
        if mode == "no_cost":
            return web.json_response({"id": "task-1", "polling_url": self.polling_url()})
        if mode == "no_id":
            return web.json_response({"polling_url": self.polling_url()})
        if mode == "credits":
            return self.error(402, "Insufficient credits")
        if mode == "forbidden":
            return self.error(403, "Not authorized for this endpoint")
        if mode == "rate":
            return self.error(429, "You have exceeded the number of active tasks (24)", {"Retry-After": "3"})
        if mode == "invalid":
            return self.error(422, [{"loc": ["body", "width"], "msg": "must be a multiple of 16", "type": "value_error"}])
        if mode == "down":
            return self.error(503, "maintenance")
        return self.error(500, "unexpected")

    async def get_result(self, request: web.Request) -> web.Response:
        self.record(self.requests, request)
        if request.headers.get("x-key") != KEY:
            return self.error(401, "Invalid key")
        return self._step()

    def _step(self) -> web.Response:
        script = self.poll_script()
        step = script[min(self.polls, len(script) - 1)]
        self.polls += 1
        return web.json_response(step)

    async def regional_result(self, request: web.Request) -> web.Response:
        self.record(self.regional_requests, request)
        if request.headers.get("x-key") != KEY:
            return self.error(401, "Invalid key")
        return self._step()

    async def credits(self, request: web.Request) -> web.Response:
        self.record(self.requests, request)
        if request.headers.get("x-key") != KEY or self.credits_mode == "auth":
            return self.error(401, "Invalid key")
        return web.json_response({"credits": 1234.5})

    async def cdn_file(self, request: web.Request) -> web.Response:
        self.record(self.cdn_requests, request)
        if request.match_info["name"] == "expired.png":
            return web.Response(status=403, body=EXPIRED_XML, content_type="application/xml")
        return web.Response(body=PNG, content_type="image/png")

    async def cdn_result(self, request: web.Request) -> web.Response:
        self.record(self.cdn_requests, request)
        return self._step()


@pytest.fixture
async def bfl():
    state = BflFixture()
    api = web.Application()
    api.router.add_get("/v1/get_result", state.get_result)
    api.router.add_get("/v1/credits", state.credits)
    api.router.add_post("/v1/{model}", state.submit)
    cdn = web.Application()
    cdn.router.add_get("/files/{name}", state.cdn_file)
    cdn.router.add_get("/v1/get_result", state.cdn_result)
    regional = web.Application()
    regional.router.add_get("/v1/get_result", state.regional_result)
    servers = [TestServer(app, host="127.0.0.1") for app in (api, cdn, regional)]
    for server in servers:
        await server.start_server()
    state.api_url, state.cdn_url, state.regional_url = (str(server.make_url("")).rstrip("/") for server in servers)
    try:
        yield state
    finally:
        for server in servers:
            await server.close()
