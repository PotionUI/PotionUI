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


VIDEO_MODELS = {
    "data": [
        {
            "id": "vendor-v/clip-pro",
            "name": "Clip Pro",
            "description": "Makes clips",
            "supported_durations": [4, 6, 8],
            "supported_resolutions": ["720p", "1080p"],
            "supported_aspect_ratios": ["16:9", "9:16"],
            "supported_sizes": ["1280x720"],
            "generate_audio": True,
            "supported_frame_images": ["first_frame", "last_frame"],
            "max_input_references": 3,
            "pricing_skus": {"per_second_720p": "0.10", "audio_addon": 0.02},
            "allowed_passthrough_parameters": ["motion"],
        },
        {
            "id": "vendor-v/clip-lite",
            "supported_durations": [5],
            "supported_aspect_ratios": ["16:9"],
            "pricing_skus": [{"sku": "video_tokens", "cost_usd": 0.000001}],
        },
        {"id": "vendor-v/odd", "architecture": {"output_modalities": ["text"]}},
        {"id": "", "supported_durations": [3]},
        {"supported_durations": [3]},
        12,
    ]
}

VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"v" * 2000


class OpenRouterFixture:
    def __init__(self) -> None:
        self.mode = "b64"
        self.requests: List[Dict[str, Any]] = []
        self.cdn_requests: List[Dict[str, Any]] = []
        self.api_url = ""
        self.cdn_url = ""
        self.failing_endpoints: set = set()
        self.video_mode = "ok"
        self.video_submit_mode = "ok"
        self.video_polls = 0
        self.video_bytes = VIDEO_BYTES
        self.videos_listed = True
        self.videos_fail = 0
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

    def video_script(self):
        content = f"{self.api_url}/videos/vid-1/content?index=0"
        done = {"status": "completed", "unsigned_urls": [content], "usage": {"cost": 0.8, "is_byok": False}}
        scripts = {
            "ok": [{"status": "pending", "queue_position": 2}, {"status": "in_progress", "progress": 40}, done],
            "foreign": [{"status": "completed", "unsigned_urls": [f"{self.cdn_url}/files/clip.mp4"]}],
            "failed": [{"status": "in_progress"}, {"status": "failed", "error": {"message": "render crashed upstream"}}],
            "moderated": [{"status": "failed", "error": {"message": "blocked by the safety policy"}}],
            "cancelled": [{"status": "in_progress"}, {"status": "cancelled"}],
            "expired": [{"status": "expired"}],
            "forever": [{"status": "in_progress", "progress": 10}],
        }
        return scripts[self.video_mode]

    async def list_videos(self, request: web.Request) -> web.Response:
        self.record(request)
        if self.videos_fail:
            return self.error(self.videos_fail, "trouble")
        if not self.videos_listed:
            return self.error(404, "no such route")
        return web.json_response(VIDEO_MODELS)

    async def submit_video(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.record(request, body)
        if self.video_submit_mode == "credits":
            return self.error(402, "no credits", {"Retry-After": "7"})
        return web.json_response(
            {"id": "vid-1", "polling_url": f"{self.api_url}/videos/vid-1", "status": "pending"}, status=202
        )

    async def poll_video(self, request: web.Request) -> web.Response:
        self.record(request)
        script = self.video_script()
        step = script[min(self.video_polls, len(script) - 1)]
        self.video_polls += 1
        return web.json_response(step)

    async def video_content(self, request: web.Request) -> web.Response:
        self.record(request)
        if request.headers.get("Authorization") != f"Bearer {KEY}":
            return self.error(401, "missing key")
        return web.Response(body=self.video_bytes, content_type="video/mp4")

    async def cdn_clip(self, request: web.Request) -> web.Response:
        self.cdn_requests.append({"headers": dict(request.headers)})
        return web.Response(body=VIDEO_BYTES, content_type="video/mp4")

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
    api.router.add_get("/videos/models", state.list_videos)
    api.router.add_post("/videos", state.submit_video)
    api.router.add_get("/videos/{id}", state.poll_video)
    api.router.add_get("/videos/{id}/content", state.video_content)
    cdn = web.Application()
    cdn.router.add_get("/files/out.png", state.cdn_file)
    cdn.router.add_get("/files/clip.mp4", state.cdn_clip)
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
