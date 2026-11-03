import asyncio
import logging
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.bootstrap.app as app_module
from src.bootstrap.app import add_health_route, health_payload, start_warm_up, stop_warm_up, warm_up


def test_a_ready_process_reports_healthy():
    assert health_payload() == {"status": "healthy", "service": "potionui-api"}


def test_a_process_still_warming_up_reports_starting():
    assert health_payload(ready=False) == {"status": "starting", "service": "potionui-api"}


def test_the_health_route_answers_503_until_the_warm_up_is_done():
    ready = threading.Event()
    app = FastAPI()
    add_health_route(app, ready)
    client = TestClient(app)

    starting = client.get("/health")
    ready.set()
    healthy = client.get("/health")

    assert (starting.status_code, starting.json()) == (503, {"status": "starting", "service": "potionui-api"})
    assert (healthy.status_code, healthy.json()) == (200, {"status": "healthy", "service": "potionui-api"})


def _container(loader, backends):
    return SimpleNamespace(
        preset_template_loader=loader,
        backend_registry=SimpleNamespace(get_all_backends=lambda: backends),
        gpu_monitor="monitor",
    )


def test_the_warm_up_loads_the_presets_and_resolves_every_backend_device(monkeypatch):
    resolved = []
    monkeypatch.setattr(app_module, "resolve_device_evidence", lambda backend, monitor: resolved.append((backend, monitor)))
    loader = Mock()
    ready = threading.Event()

    warm_up(_container(loader, {"native": "native-backend", "cloud": "cloud-backend"}), ready)

    loader.all.assert_called_once_with()
    assert resolved == [("native-backend", "monitor"), ("cloud-backend", "monitor")]
    assert ready.is_set()


def test_a_failing_warm_up_still_lets_health_turn_ready(monkeypatch):
    def fail(backend, monitor):
        raise RuntimeError("no device")

    monkeypatch.setattr(app_module, "resolve_device_evidence", fail)
    loader = Mock()
    loader.all.side_effect = RuntimeError("bad preset root")
    ready = threading.Event()

    warm_up(_container(loader, {"native": "native-backend"}), ready)

    assert ready.is_set()


def test_a_warm_up_that_raises_logs_it_and_still_marks_the_app_ready(caplog):
    def no_registry():
        raise RuntimeError("registry gone")

    container = SimpleNamespace(
        preset_template_loader=Mock(),
        backend_registry=SimpleNamespace(get_all_backends=no_registry),
        gpu_monitor="monitor",
    )
    ready = threading.Event()

    with caplog.at_level(logging.ERROR):
        warm_up(container, ready)

    assert ready.is_set()
    assert "registry gone" in caplog.text


async def test_the_warm_up_task_is_kept_on_the_app_until_shutdown(monkeypatch):
    monkeypatch.setattr(app_module, "resolve_device_evidence", lambda backend, monitor: None)
    app = FastAPI()
    ready = threading.Event()

    task = start_warm_up(app, _container(Mock(), {}), ready)

    assert app.state.warm_up_task is task
    await task
    assert ready.is_set()
    await stop_warm_up(app)
    assert app.state.warm_up_task is None


async def test_shutdown_does_not_wait_for_a_warm_up_still_running(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(app_module, "warm_up", lambda container, ready: release.wait(10))
    app = FastAPI()

    task = start_warm_up(app, object(), threading.Event())
    await asyncio.sleep(0)
    try:
        await asyncio.wait_for(stop_warm_up(app), timeout=2)
    finally:
        release.set()

    assert task.cancelled()
    assert app.state.warm_up_task is None
