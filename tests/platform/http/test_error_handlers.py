"""Tests for the sanitized global error handlers (src/bootstrap/errors.py).

These attach the *real* handler functions to a throwaway FastAPI app (no
lifespan boot) to verify that error responses never leak stack traces or echo
the request body, and that unhandled errors carry a correlation id.
"""
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.bootstrap import errors


class _Body(BaseModel):
    count: int


@pytest.fixture
def client():
    app = FastAPI()
    app.add_exception_handler(Exception, errors.global_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret internal detail: /home/secret/path")

    @app.post("/validate")
    async def validate(body: _Body):
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


class TestGlobalExceptionHandler:
    def test_no_stacktrace_and_has_correlation_id(self, client):
        resp = client.get("/boom")
        assert resp.status_code == 500
        payload = resp.json()

        assert "stacktrace" not in payload
        assert "request_url" not in payload
        assert "request_method" not in payload
        # The internal detail must not leak into the client response.
        assert "secret internal detail" not in str(payload)
        assert payload["error"] == "internal_error"
        assert payload["message"] == "Internal server error"
        assert payload.get("correlation_id")


class TestValidationExceptionHandler:
    def test_no_request_body_echo(self, client):
        # 'count' should be an int; send a string so validation fails.
        resp = client.post("/validate", json={"count": "not-a-number"})
        assert resp.status_code == 422
        payload = resp.json()

        assert "request_body" not in payload
        assert "validation_errors" not in payload  # renamed to sanitized 'detail'
        assert payload["error"] == "validation_error"
        # Each sanitized error must not carry the echoed 'input' value.
        for err in payload.get("detail", []):
            assert "input" not in err
            assert "ctx" not in err


class TestSanitizeValidationErrors:
    def test_strips_input_and_ctx(self):
        raw = [
            {"loc": ("body", "count"), "msg": "bad", "type": "int_parsing", "input": "secret", "ctx": {"x": 1}},
        ]
        cleaned = errors._sanitize_validation_errors(raw)
        assert cleaned[0].get("input") is None
        assert "input" not in cleaned[0]
        assert "ctx" not in cleaned[0]
        assert cleaned[0]["msg"] == "bad"
