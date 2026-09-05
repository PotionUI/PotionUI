"""Runs an LLM client method against a canned HTTP peer and keeps the request.

Every client builds its payload inside the method that issues the call, so the
only honest way to pin a request body is to let the real method run. This routes
``httpx.AsyncClient`` through a ``MockTransport``, so request building, response
parsing and the trace call all execute unchanged while the socket never opens.

Streaming bodies are handed over as a list of byte chunks the test chooses, so a
test can split one SSE/NDJSON line across two chunks and prove the client
reassembles it.
"""

import json
from typing import Any, Callable, Dict, List

import httpx

from src.features.llm import trace_collector
from src.features.llm.repository import LLMConfig


class WireCapture:
    """Records every request the client sent and replies with canned responses."""

    def __init__(self, responses: List[Callable[[], httpx.Response]]):
        self._responses = list(responses)
        self.requests: List[httpx.Request] = []
        self.traces: List[Dict[str, Any]] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[index]()

    @property
    def body(self) -> Dict[str, Any]:
        return json.loads(self.requests[-1].content)

    @property
    def headers(self) -> Dict[str, str]:
        return dict(self.requests[-1].headers)

    @property
    def url(self) -> str:
        return str(self.requests[-1].url)

    @property
    def trace(self) -> Dict[str, Any]:
        return self.traces[-1]


def install_wire_capture(
    monkeypatch, responses: List[Callable[[], httpx.Response]]
) -> WireCapture:
    """Point every ``httpx.AsyncClient`` at a mock peer and capture trace calls."""
    capture = WireCapture(responses)
    transport = httpx.MockTransport(capture._handle)
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    monkeypatch.setattr(
        trace_collector, "record", lambda **kwargs: capture.traces.append(kwargs)
    )
    return capture


def json_response(payload: Dict[str, Any], status: int = 200) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(status, json=payload)


def chunked_response(chunks: List[bytes], status: int = 200) -> Callable[[], httpx.Response]:
    """A streaming reply delivered as exactly these byte chunks."""

    def _make() -> httpx.Response:
        async def _body():
            for chunk in chunks:
                yield chunk

        return httpx.Response(status, content=_body())

    return _make


class _ClosableStream(httpx.AsyncByteStream):
    """A body that records the moment the client released the response."""

    def __init__(self, chunks: List[bytes], closed: List[bool]):
        self._chunks = chunks
        self._closed = closed

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self._closed.append(True)


def closable_response(chunks: List[bytes], closed: List[bool]) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(200, stream=_ClosableStream(chunks, closed))


def sse_response(events: List[str], status: int = 200) -> Callable[[], httpx.Response]:
    """One SSE ``data:`` frame per entry, each in its own chunk."""
    return chunked_response([f"data: {e}\n\n".encode() for e in events], status)


def ndjson_response(objects: List[Dict[str, Any]], status: int = 200) -> Callable[[], httpx.Response]:
    return chunked_response([(json.dumps(o) + "\n").encode() for o in objects], status)


def make_config(provider_type: str, **overrides: Any) -> LLMConfig:
    defaults: Dict[str, Any] = dict(
        id="cfg-1",
        name="Golden Config",
        type=provider_type,
        enabled=True,
        base_url="http://peer.invalid:1234",
        api_key=None,
        model="golden-model",
        system_message="stored system message",
        temperature=0.7,
        max_tokens=512,
        timeout=30,
        supports_vision=False,
        provider_options=None,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


async def collect(stream) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    async for event in stream:
        events.append(event)
    return events


OPENAI_REPLY: Dict[str, Any] = {
    "choices": [{"message": {"content": "canned reply"}, "finish_reason": "stop"}],
    "usage": {"total_tokens": 30, "prompt_tokens": 20, "completion_tokens": 10},
}

OLLAMA_REPLY: Dict[str, Any] = {
    "message": {"content": "canned reply"},
    "prompt_eval_count": 20,
    "eval_count": 10,
}

OLLAMA_GENERATE_REPLY: Dict[str, Any] = {
    "response": "canned reply",
    "prompt_eval_count": 20,
    "eval_count": 10,
}


def image_message_history() -> List[Dict[str, Any]]:
    return [{"role": "user", "content": "describe this"}]


def tool_history() -> List[Dict[str, Any]]:
    """A history covering every message shape the tool paths special-case."""
    return [
        {"role": "user", "content": "use the tool"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_form_state", "arguments": {"scope": "all"}},
                }
            ],
        },
        {"role": "tool", "content": "{\"ok\": true}", "tool_call_id": "call_1", "name": "get_form_state"},
        {"role": "user", "content": "thanks"},
    ]


def tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_form_state",
                "description": "Read the current form",
                "parameters": {
                    "type": "object",
                    "properties": {"scope": {"type": "string", "description": "what to read"}},
                    "required": ["scope"],
                },
            },
        }
    ]


IMAGE_B64 = "aW1hZ2UtYnl0ZXM="


def strip(payload: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    """Payload without the named keys — used to compare stream vs non-stream."""
    return {k: v for k, v in payload.items() if k not in keys}
