from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bootstrap.app import health_payload
from src.platform.runtime import cuda_allocator


def test_a_healthy_process_reports_healthy(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", None)

    assert health_payload() == {"status": "healthy", "service": "potionui-api"}


def test_a_damaged_allocator_reports_degraded_with_the_way_out(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", "CUDA driver error: device not ready")

    payload = health_payload()

    assert payload["status"] == "degraded"
    assert payload["service"] == "potionui-api"
    assert "CUDA driver error: device not ready" in payload["reason"]
    assert payload["reason"].endswith(cuda_allocator.RESTART_HINT)


def test_a_degraded_process_still_answers_200_so_nothing_restarts_it_on_its_own(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", "CUDA driver error: device not ready")
    app = FastAPI()
    app.get("/health")(health_payload)

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
