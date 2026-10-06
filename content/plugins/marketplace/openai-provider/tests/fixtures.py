import base64
import io
from typing import Any, Dict, List, Optional

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image

KEY = "sk-proj-TOPSECRET-0123456789abcdef"


def png(size=(4, 3), color=(200, 30, 30, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = png()

LISTED = ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-2", "gpt-image-1.5", "gpt-4o"]

USAGE = {
    "input_tokens": 50,
    "input_tokens_details": {"text_tokens": 10, "image_tokens": 40},
    "output_tokens": 4160,
    "total_tokens": 4210,
}

ERRORS = {
    "moderation": (400, {
        "message": "Your request was rejected by the safety system. SECRET PROMPT TEXT",
        "type": "image_generation_user_error",
        "param": None,
        "code": "moderation_blocked",
        "moderation_details": {"categories": ["violence"], "moderation_stage": "input"},
    }),
    "auth": (401, {"message": f"Incorrect API key provided: {KEY}.", "type": "invalid_request_error", "code": "invalid_api_key"}),
    "quota": (429, {"message": "You exceeded your current quota.", "type": "insufficient_quota", "code": "insufficient_quota"}),
    "rate": (429, {"message": "Rate limit reached for images per min.", "type": "requests", "code": "rate_limit_exceeded"}),
    "rate_reset": (429, {"message": "Rate limit reached for tokens.", "type": "tokens", "code": "rate_limit_exceeded"}),
    "forbidden": (403, {"message": "Your organization must be verified to use the model.", "type": "invalid_request_error", "code": None}),
    "unknown_model": (404, {"message": "The model does not exist.", "type": "invalid_request_error", "code": "model_not_found"}),
    "bad": (400, {"message": "Invalid value for size.", "type": "invalid_request_error", "param": "size", "code": "invalid_value"}),
    "server": (500, {"message": "The server had an error.", "type": "server_error", "code": None}),
    "overloaded": (503, {"message": "The engine is currently overloaded.", "type": "server_error", "code": None}),
}


class OpenAIFixture:
    def __init__(self) -> None:
        self.mode = "ok"
        self.models_mode = "ok"
        self.requests: List[Dict[str, Any]] = []
        self.usage: Optional[Dict[str, Any]] = dict(USAGE)

    def record(self, request: web.Request, body: Any = None, files: Any = None) -> None:
        self.requests.append({
            "method": request.method, "path": request.path, "headers": dict(request.headers),
            "content_type": request.content_type, "body": body, "files": files,
        })

    def failure(self, mode: str) -> web.Response:
        status, error = ERRORS[mode]
        headers = {"x-request-id": "req_fail"}
        if mode == "rate":
            headers["Retry-After"] = "7"
        if mode == "rate_reset":
            headers["x-ratelimit-reset-requests"] = "1m30s"
            headers["x-ratelimit-reset-tokens"] = "250ms"
        return web.json_response({"error": error}, status=status, headers=headers)

    def images(self, count: int, output_format: str = "png") -> web.Response:
        if self.mode == "empty":
            return web.json_response({"created": 1, "data": []})
        if self.mode != "ok":
            return self.failure(self.mode)
        payload: Dict[str, Any] = {
            "created": 1713833628,
            "data": [{"b64_json": base64.b64encode(PNG).decode()} for _ in range(count)],
            "output_format": output_format,
            "quality": "high",
            "size": "1024x1024",
        }
        if self.usage is not None:
            payload["usage"] = self.usage
        return web.json_response(payload, headers={"x-request-id": "req_123"})

    async def list_models(self, request: web.Request) -> web.Response:
        self.record(request)
        if self.models_mode == "forbidden":
            return self.failure("forbidden")
        if self.models_mode == "auth":
            return self.failure("auth")
        data = [{"id": model_id, "object": "model", "created": 1, "owned_by": "system"} for model_id in LISTED]
        data[1]["shutdown_date"] = "2027-03-01"
        return web.json_response({"object": "list", "data": data})

    async def generations(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.record(request, body)
        return self.images(int(body.get("n", 1)), body.get("output_format", "png"))

    async def edits(self, request: web.Request) -> web.Response:
        fields: Dict[str, Any] = {}
        files: List[Dict[str, Any]] = []
        reader = await request.multipart()
        async for part in reader:
            data = await part.read()
            if part.filename:
                files.append({"name": part.name, "filename": part.filename, "type": part.headers.get("Content-Type"), "data": bytes(data)})
            else:
                fields[part.name] = data.decode()
        self.record(request, fields, files)
        return self.images(int(fields.get("n", 1)), fields.get("output_format", "png"))


@pytest.fixture
async def openai_api():
    state = OpenAIFixture()
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_get("/models", state.list_models)
    app.router.add_post("/images/generations", state.generations)
    app.router.add_post("/images/edits", state.edits)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    state.api_url = str(server.make_url("")).rstrip("/")
    try:
        yield state
    finally:
        await server.close()
