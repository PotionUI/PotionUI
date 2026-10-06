import base64
import copy
import struct
import zlib
from typing import Any, Dict, List

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

KEY = "fake-gemini-key-TOPSECRET-0123456789"


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff")) + chunk(b"IEND", b"")


PNG = _png()
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"v" * 2000

LISTING_PAGES = [
    {
        "models": [
            {
                "name": "models/gemini-3.1-flash-image",
                "displayName": "Nano Banana 2",
                "supportedGenerationMethods": ["generateContent", "countTokens", "batchGenerateContent"],
                "modelStage": "GA",
            },
            {"name": "models/gemini-3.5-flash", "displayName": "Gemini 3.5 Flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-005", "supportedGenerationMethods": ["embedContent"]},
            {
                "name": "models/gemini-9-ultra-image",
                "displayName": "Gemini 9 Ultra Image",
                "description": "A model the plugin has never heard of",
                "supportedGenerationMethods": ["generateContent"],
                "modelStage": "DEPRECATED",
                "retirementTime": "2027-01-31T00:00:00Z",
            },
        ],
        "nextPageToken": "page-2",
    },
    {
        "models": [
            {"name": "models/gemini-3-pro-image", "displayName": "Nano Banana Pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"], "modelStage": "RETIRED"},
            {"name": "models/veo-3.1-lite-generate-preview", "displayName": "Veo 3.1 Lite", "supportedGenerationMethods": ["predictLongRunning"]},
            {"name": "models/veo-3.1-generate-preview", "displayName": "Veo 3.1", "supportedGenerationMethods": ["predictLongRunning"]},
            {"name": "models/gemini-omni-1.1-flash", "supportedGenerationMethods": ["createInteraction"]},
            "garbage",
            {"displayName": "no name"},
        ]
    },
]


def image_answer(count: int = 1, finish: str = "STOP") -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = [
        {"text": "thinking about the lighthouse", "thought": True},
        {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(b"draft").decode()}, "thought": True},
        {"text": "Here is your picture."},
    ]
    parts.extend({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}} for _ in range(count))
    return {
        "responseId": "resp-123",
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 1220,
            "thoughtsTokenCount": 200,
            "totalTokenCount": 2420,
            "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1120}, {"modality": "TEXT", "tokenCount": 100}],
        },
        "modelVersion": "gemini-3.1-flash-image",
    }


class GeminiFixture:
    def __init__(self) -> None:
        self.mode = "image"
        self.listing_mode = "ok"
        self.video_mode = "ok"
        self.video_polls = 0
        self.requests: List[Dict[str, Any]] = []
        self.foreign_requests: List[Dict[str, Any]] = []
        self.api_url = ""
        self.foreign_url = ""
        self.pages = copy.deepcopy(LISTING_PAGES)

    def record(self, request: web.Request, body: Any = None) -> None:
        self.requests.append({
            "method": request.method,
            "path": request.path,
            "query": dict(request.query),
            "headers": dict(request.headers),
            "body": body,
        })

    def error(self, status: int, code: str, message: str, details: List[Dict[str, Any]] = None, headers: Dict[str, str] = None):
        payload = {"error": {"code": status, "message": message, "status": code, "details": details or []}}
        return web.json_response(payload, status=status, headers=headers or {})

    def failure(self, mode: str, request: web.Request):
        if mode == "bad_key":
            return self.error(400, "INVALID_ARGUMENT", f"API key not valid. Please pass a valid API key. {request.headers.get('x-goog-api-key', '')}", [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID", "domain": "googleapis.com"},
            ])
        if mode == "unauthenticated":
            return self.error(401, "UNAUTHENTICATED", "Request had invalid authentication credentials.")
        if mode == "forbidden":
            return self.error(403, "PERMISSION_DENIED", "The caller does not have permission")
        if mode == "prepay":
            return self.error(402, "PAYMENT_REQUIRED", "Your prepayment credits are depleted.")
        if mode == "billing":
            return self.error(400, "FAILED_PRECONDITION", "Gemini API free tier is not available for this model. Please enable billing on your project.")
        if mode == "location":
            return self.error(400, "FAILED_PRECONDITION", "User location is not supported for the API use.")
        if mode == "rate":
            return self.error(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.", [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"},
            ])
        if mode == "rate_header":
            return self.error(429, "RESOURCE_EXHAUSTED", "Slow down.", headers={"Retry-After": "7"})
        if mode == "internal":
            return self.error(500, "INTERNAL", "An internal error has occurred.")
        if mode == "overloaded":
            return self.error(503, "UNAVAILABLE", "The model is overloaded. Please try again later.")
        if mode == "deadline":
            return self.error(504, "DEADLINE_EXCEEDED", "Deadline expired before operation could complete.")
        if mode == "not_found":
            return self.error(404, "NOT_FOUND", "models/x is not found for API version v1beta.")
        if mode == "invalid":
            return self.error(400, "INVALID_ARGUMENT", "Invalid value at 'generation_config.image_config.aspect_ratio'.")
        return None

    async def list_models(self, request: web.Request) -> web.Response:
        self.record(request)
        failed = self.failure(self.listing_mode, request)
        if failed is not None:
            return failed
        if self.listing_mode == "empty":
            return web.json_response({"models": []})
        index = 1 if request.query.get("pageToken") == "page-2" else 0
        return web.json_response(self.pages[index])

    async def model_call(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.record(request, body)
        method = request.match_info["call"].rsplit(":", 1)[-1]
        if method == "predictLongRunning":
            return await self.submit_video(request)
        failed = self.failure(self.mode, request)
        if failed is not None:
            return failed
        if self.mode == "image":
            return web.json_response(image_answer())
        if self.mode == "prompt_blocked":
            return web.json_response({
                "promptFeedback": {
                    "blockReason": "SAFETY",
                    "safetyRatings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "probability": "HIGH", "blocked": True}],
                },
                "usageMetadata": {"promptTokenCount": 9},
            })
        if self.mode == "image_safety":
            return web.json_response({
                "candidates": [{"content": {"parts": [{"text": "SECRET MODEL TEXT"}]}, "finishReason": "IMAGE_SAFETY"}],
            })
        if self.mode == "prohibited":
            return web.json_response({"candidates": [{"finishReason": "PROHIBITED_CONTENT"}]})
        if self.mode == "text_only":
            return web.json_response({
                "candidates": [{"content": {"parts": [{"text": "I can only describe it in words."}]}, "finishReason": "STOP"}],
            })
        if self.mode == "no_usage":
            answer = image_answer()
            answer.pop("usageMetadata")
            return web.json_response(answer)
        if self.mode == "garbage":
            return web.Response(text="<html>oops</html>", content_type="text/html")
        raise AssertionError(self.mode)

    async def submit_video(self, request: web.Request) -> web.Response:
        if self.video_mode == "rate":
            return self.failure("rate", request)
        if self.video_mode == "bad_name":
            return web.json_response({"name": "../../secrets", "done": False})
        return web.json_response({"name": "models/veo-3.1-lite-generate-preview/operations/op-1", "done": False})

    def video_script(self) -> List[Dict[str, Any]]:
        uri = f"{self.api_url}/files/clip-1:download?alt=media"
        done = {
            "name": "models/veo-3.1-lite-generate-preview/operations/op-1",
            "done": True,
            "response": {
                "@type": "type.googleapis.com/google.ai.generativelanguage.v1beta.PredictLongRunningResponse",
                "generateVideoResponse": {"generatedSamples": [{"video": {"uri": uri}}]},
            },
        }
        scripts = {
            "ok": [{"name": "op-1"}, {"name": "op-1", "done": False}, done],
            "foreign": [{
                "done": True,
                "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": f"{self.foreign_url}/clip.mp4"}}]}},
            }],
            "failed": [{"done": True, "error": {"code": 13, "message": "render crashed upstream"}}],
            "refused": [{"done": True, "error": {"code": 3, "message": "The prompt was blocked by our safety policy."}}],
            "filtered": [{
                "done": True,
                "response": {"generateVideoResponse": {
                    "raiMediaFilteredCount": 1,
                    "raiMediaFilteredReasons": ["The video could not be generated because it may violate our policies."],
                }},
            }],
            "forever": [{"done": False}],
        }
        return scripts[self.video_mode]

    async def poll_video(self, request: web.Request) -> web.Response:
        self.record(request)
        script = self.video_script()
        step = script[min(self.video_polls, len(script) - 1)]
        self.video_polls += 1
        return web.json_response(step)

    async def download(self, request: web.Request) -> web.Response:
        self.record(request)
        if request.headers.get("x-goog-api-key") != KEY:
            return self.error(403, "PERMISSION_DENIED", "missing key")
        return web.Response(body=VIDEO_BYTES, content_type="video/mp4")

    async def foreign_clip(self, request: web.Request) -> web.Response:
        self.foreign_requests.append({"headers": dict(request.headers), "query": dict(request.query)})
        return web.Response(body=VIDEO_BYTES, content_type="video/mp4")


@pytest.fixture
async def gemini():
    state = GeminiFixture()
    api = web.Application()
    api.router.add_get("/models", state.list_models)
    api.router.add_post("/models/{call}", state.model_call)
    api.router.add_get("/models/{model}/operations/{op}", state.poll_video)
    api.router.add_get("/files/{file}", state.download)
    foreign = web.Application()
    foreign.router.add_get("/clip.mp4", state.foreign_clip)
    api_server = TestServer(api, host="127.0.0.1")
    foreign_server = TestServer(foreign, host="127.0.0.1")
    await api_server.start_server()
    await foreign_server.start_server()
    state.api_url = str(api_server.make_url("")).rstrip("/")
    state.foreign_url = str(foreign_server.make_url("")).rstrip("/")
    try:
        yield state
    finally:
        await api_server.close()
        await foreign_server.close()
