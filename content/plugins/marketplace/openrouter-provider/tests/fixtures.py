import asyncio
import base64
import struct
import zlib
from typing import Any, Dict, List

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

KEY = "sk-or-v1-TOPSECRET-0123456789abcdef"


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff")) + chunk(b"IEND", b"")


PNG = _png()

MODELS = {
    "data": [
        {
            "id": "vendor-a/image-pro",
            "name": "Image Pro",
            "description": "Edits and draws",
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image"]},
            "supported_parameters": [
                {"name": "aspect_ratio", "type": "enum", "values": ["1:1", "16:9"]},
                {"name": "n", "type": "range", "min": 1, "max": 4},
                {"name": "quality", "type": "enum", "values": ["low", "high"]},
                {"name": "input_references", "type": "range", "min": 0, "max": 6},
                "seed",
                "background",
                "resolution",
            ],
            "supports_streaming": True,
        },
        {
            "id": "vendor-b/text-only",
            "name": "Text Only",
            "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
            "supported_parameters": ["aspect_ratio", "output_format", "output_compression"],
            "expiration_date": "2026-12-31",
        },
        {"id": "vendor-c/chat-only", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "vendor-d/odd", "architecture": {"output_modalities": ["image"]}, "supported_parameters": [{"name": "depth", "type": "range"}, 7]},
        {"name": "missing id", "architecture": {"output_modalities": ["image"]}},
        "garbage",
    ]
}

ENDPOINTS = {
    "vendor-a/image-pro": {
        "data": {
            "id": "vendor-a/image-pro",
            "endpoints": [
                {
                    "provider_name": "Alpha",
                    "supported_parameters": [{"name": "aspect_ratio", "type": "enum", "values": ["1:1", "21:9"]}],
                    "allowed_passthrough_parameters": ["style"],
                    "pricing": {"billable": "image", "unit": "image", "cost_usd": 0.05},
                },
                {
                    "provider_name": "Beta",
                    "supported_parameters": [],
                    "allowed_passthrough_parameters": [],
                    "pricing": [{"billable": "image", "unit": "image", "cost_usd": 0.04}, {"billable": "hd", "unit": "weird", "cost_usd": 0.1}],
                },
            ],
        }
    },
    "vendor-b/text-only": {"data": {"endpoints": [{"pricing": {"unit": "megapixel", "cost_usd": "0.02"}}]}},
}


class OpenRouterFixture:
    def __init__(self) -> None:
        self.mode = "b64"
        self.requests: List[Dict[str, Any]] = []
        self.cdn_requests: List[Dict[str, Any]] = []
        self.api_url = ""
        self.cdn_url = ""
        self.failing_endpoints: set = set()
        self.moderation_metadata = {
            "reasons": ["violence"],
            "flagged_input": "SECRET PROMPT TEXT",
            "provider_name": "Alpha",
            "model_slug": "vendor-a/image-pro",
        }

    def record(self, request: web.Request, body: Any = None) -> None:
        self.requests.append({"method": request.method, "path": request.path, "headers": dict(request.headers), "body": body})

    def error(self, status: int, message: str = "nope", headers: Dict[str, str] = None, metadata: Dict[str, Any] = None):
        payload = {"error": {"code": status, "message": message}}
        if metadata:
            payload["error"]["metadata"] = metadata
        return web.json_response(payload, status=status, headers=headers or {})

    async def list_models(self, request: web.Request) -> web.Response:
        self.record(request)
        if self.mode == "auth_on_list":
            return self.error(401, f"bad key {request.headers.get('Authorization', '')}")
        return web.json_response(MODELS)

    async def endpoints(self, request: web.Request) -> web.Response:
        self.record(request)
        model = f"{request.match_info['vendor']}/{request.match_info['name']}"
        if model in self.failing_endpoints:
            return self.error(404, "unknown")
        return web.json_response(ENDPOINTS.get(model, {"data": {"endpoints": []}}))

    async def generate(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.record(request, body)
        mode = self.mode
        if mode == "b64":
            return web.json_response({
                "id": "gen-123",
                "data": [{"b64_json": base64.b64encode(PNG).decode()}] * int(body.get("n", 1)),
                "media_type": "image/png",
                "usage": {"cost": 0.0412},
            })
        if mode == "url":
            return web.json_response({"data": [{"url": f"{self.cdn_url}/files/out.png", "media_type": "image/png"}]})
        if mode == "empty":
            return web.json_response({"data": []})
        if mode == "inline_error":
            return web.json_response({"error": {"code": 500, "message": "upstream exploded"}})
        if mode == "echo_key":
            return self.error(400, f"invalid value for header {request.headers.get('Authorization', '')}")
        statuses = {"auth": 401, "credits": 402, "moderation": 403, "forbidden": 403, "rate": 429, "down": 502, "routing": 503, "bad": 400, "slow": 408}
        status = statuses[mode]
        headers = {"Retry-After": "7"} if mode in ("rate", "routing", "credits") else {}
        metadata = self.moderation_metadata if mode == "moderation" else None
        return self.error(status, f"{mode} happened", headers, metadata)

    async def cdn_file(self, request: web.Request) -> web.Response:
        self.cdn_requests.append({"headers": dict(request.headers)})
        return web.Response(body=PNG, content_type="image/png")


@pytest.fixture
async def openrouter():
    state = OpenRouterFixture()
    api = web.Application()
    api.router.add_get("/images/models", state.list_models)
    api.router.add_get("/images/models/{vendor}/{name}/endpoints", state.endpoints)
    api.router.add_post("/images", state.generate)
    cdn = web.Application()
    cdn.router.add_get("/files/out.png", state.cdn_file)
    api_server = TestServer(api, host="127.0.0.1")
    cdn_server = TestServer(cdn, host="127.0.0.1")
    await api_server.start_server()
    await cdn_server.start_server()
    state.api_url = str(api_server.make_url("")).rstrip("/")
    state.cdn_url = str(cdn_server.make_url("")).rstrip("/")
    try:
        yield state
    finally:
        await api_server.close()
        await cdn_server.close()
