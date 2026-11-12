import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.features.remote_execution.transport import WorkerTransport
from src.features.remote_execution.worker import routes as worker_routes


class Coordinator:
    def __init__(self, finish_after):
        self.finish_after = finish_after
        self.started = asyncio.get_event_loop().time()

    def record_for(self, execution_id):
        done = asyncio.get_event_loop().time() - self.started >= self.finish_after
        return SimpleNamespace(is_terminal=done)

    def events_after(self, execution_id, cursor):
        return []


def _app(finish_after):
    container = SimpleNamespace(config=SimpleNamespace(token="t"), coordinator=Coordinator(finish_after))
    app = FastAPI()
    app.include_router(worker_routes.build_worker_router(container))
    return app


@pytest.mark.asyncio
async def test_a_quiet_job_keeps_the_event_stream_alive_with_keepalive_comments(monkeypatch):
    monkeypatch.setattr(worker_routes, "HEARTBEAT_SECONDS", 0.05)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(0.3)), base_url="http://w") as client:
        response = await client.get("/v1/executions/x/events", headers={"Authorization": "Bearer t"})

    assert response.text.count(": keepalive") >= 3
    assert "data:" not in response.text


@pytest.mark.asyncio
async def test_a_stream_with_no_quiet_stretch_sends_no_keepalive(monkeypatch):
    monkeypatch.setattr(worker_routes, "HEARTBEAT_SECONDS", 5)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(0.1)), base_url="http://w") as client:
        response = await client.get("/v1/executions/x/events", headers={"Authorization": "Bearer t"})

    assert ": keepalive" not in response.text


@pytest.mark.asyncio
async def test_the_transport_reports_every_line_including_keepalives_as_a_sign_of_life():
    body = ": keepalive\n\n: keepalive\n\n"
    transport = WorkerTransport(
        "http://w", "t", transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    )
    beats = []

    events = [e async for e in transport.stream_events("x", on_alive=lambda: beats.append(1))]

    assert events == []
    assert len(beats) >= 2
