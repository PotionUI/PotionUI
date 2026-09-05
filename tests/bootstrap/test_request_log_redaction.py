"""Credentials must not survive a round trip through the request log.

Every case here drives the *real* logging middleware and the *real* exception
handlers through a throwaway FastAPI app and then reads back what was written
to `caplog`. The assertion is always the same shape: the synthetic credential
does not appear anywhere in the captured log text, and the request was still
served normally. Each case runs twice, with DEBUG mode on and off, because the
body-logging branch only exists in one of them.
"""
import logging

import pytest
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from src.bootstrap import errors, middleware

PASSWORD = "hunter2-never-log-me"
NEW_PASSWORD = "correct-horse-never-log-me"
NESTED_TOKEN = "tok-nested-never-log-me"
UPLOAD_SECRET = "multipart-never-log-me"
PLAINTEXT_SECRET = "plaintext-never-log-me"
QUERY_TOKEN = "query-token-never-log-me"
QUERY_TOKEN_MIXED = "query-mixedcase-never-log-me"
VALIDATION_SECRET = "validator-never-log-me"


class _ChangePassword(BaseModel):
    current_password: str
    new_password: str


class _ApiKeyPayload(BaseModel):
    api_key: str

    @field_validator("api_key")
    @classmethod
    def _always_reject(cls, value: str) -> str:
        # A validator writing the offending value into its own message is the
        # shape that survives dropping `input` and `ctx`.
        raise ValueError(f"rejected key {value}")


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(Exception, errors.global_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)
    app.add_middleware(middleware.LoggingMiddleware)

    @app.post("/api/auth/login")
    async def login(username: str = Form(...), password: str = Form(...)):
        return {"user": username, "password_len": len(password)}

    @app.post("/api/auth/change-password")
    async def auth_change_password(body: _ChangePassword):
        return {"ok": True, "seen": body.new_password == NEW_PASSWORD}

    @app.post("/api/account/change-password")
    async def account_change_password(body: _ChangePassword):
        return {"ok": True, "seen": body.new_password == NEW_PASSWORD}

    @app.post("/api/backends/config")
    async def backend_config(payload: dict):
        return {"ok": True, "seen": payload}

    @app.post("/api/media/upload")
    async def upload(note: str = Form(...), file: UploadFile = File(...)):
        return {"note_len": len(note), "filename": file.filename}

    @app.post("/api/notes/raw")
    async def raw(request: Request):
        body = await request.body()
        return {"length": len(body)}

    @app.get("/api/search")
    async def search(q: str = "", token: str = ""):
        return {"q": q}

    @app.post("/api/backends/validate")
    async def validate(payload: _ApiKeyPayload):
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(params=[True, False], ids=["debug-on", "debug-off"])
def debug_mode(request, monkeypatch):
    monkeypatch.setattr(middleware, "DEBUG_MODE", request.param)
    return request.param


@pytest.fixture
def client(debug_mode, caplog):
    caplog.set_level(logging.DEBUG)
    return _build_app()


# The middleware logs here; `errors.py` uses the root logger. Everything else
# under `caplog` belongs to the test harness - httpx echoes the URL it just
# requested - and is not something the server writes down.
_SERVER_LOGGERS = ("api.requests", "root")


def _log_text(caplog) -> str:
    return "\n".join(
        record.getMessage() for record in caplog.records if record.name in _SERVER_LOGGERS
    )


def test_form_encoded_login_body_is_never_logged(client, caplog, debug_mode):
    resp = client.post(
        "/api/auth/login",
        data={"username": "alice", "password": PASSWORD},
    )
    assert resp.status_code == 200
    assert resp.json()["password_len"] == len(PASSWORD)

    text = _log_text(caplog)
    assert PASSWORD not in text
    assert "POST /api/auth/login" in text
    if debug_mode:
        assert "<redacted: credential route>" in text


def test_json_password_change_on_an_auth_route_is_never_logged(client, caplog, debug_mode):
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200
    assert resp.json()["seen"] is True

    text = _log_text(caplog)
    assert PASSWORD not in text
    assert NEW_PASSWORD not in text


def test_json_password_change_off_the_auth_routes_is_masked_by_key(client, caplog, debug_mode):
    resp = client.post(
        "/api/account/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200
    assert resp.json()["seen"] is True

    text = _log_text(caplog)
    assert PASSWORD not in text
    assert NEW_PASSWORD not in text
    if debug_mode:
        # The line is still there and still useful - only the values are gone.
        assert "current_password" in text
        assert "new_password" in text


def test_nested_json_credentials_are_masked(client, caplog, debug_mode):
    payload = {
        "name": "comfy-1",
        "connection": {"url": "https://comfy.example.test", "auth_token": NESTED_TOKEN},
        "fallbacks": [{"url": "https://b.example.test", "api_key": NESTED_TOKEN}],
    }
    resp = client.post("/api/backends/config", json=payload)
    assert resp.status_code == 200
    assert resp.json()["seen"]["connection"]["auth_token"] == NESTED_TOKEN

    text = _log_text(caplog)
    assert NESTED_TOKEN not in text
    if debug_mode:
        assert "comfy.example.test" in text


def test_multipart_body_is_never_logged(client, caplog, debug_mode):
    resp = client.post(
        "/api/media/upload",
        data={"note": UPLOAD_SECRET},
        files={"file": ("a.txt", b"contents", "text/plain")},
    )
    assert resp.status_code == 200
    assert resp.json()["filename"] == "a.txt"

    text = _log_text(caplog)
    assert UPLOAD_SECRET not in text
    if debug_mode:
        assert "<redacted: multipart/form-data>" in text


def test_plain_text_body_is_never_logged(client, caplog, debug_mode):
    resp = client.post(
        "/api/notes/raw",
        content=f"password={PLAINTEXT_SECRET}".encode(),
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 200

    text = _log_text(caplog)
    assert PLAINTEXT_SECRET not in text
    if debug_mode:
        assert "<redacted: text/plain>" in text


def test_query_credentials_are_masked_and_safe_keys_survive(client, caplog, debug_mode):
    resp = client.get(
        "/api/search",
        params=[("token", QUERY_TOKEN), ("Token", QUERY_TOKEN_MIXED), ("q", "safe")],
    )
    assert resp.status_code == 200
    assert resp.json()["q"] == "safe"

    text = _log_text(caplog)
    assert QUERY_TOKEN not in text
    assert QUERY_TOKEN_MIXED not in text
    assert "q=safe" in text


def test_validation_error_does_not_log_the_offending_credential(client, caplog, debug_mode):
    resp = client.post("/api/backends/validate", json={"api_key": VALIDATION_SECRET})
    assert resp.status_code == 422

    text = _log_text(caplog)
    assert VALIDATION_SECRET not in text
    assert "Validation error in POST /api/backends/validate" in text


def test_a_json_body_still_reaches_the_endpoint_after_being_logged(client, debug_mode):
    """Reading the body to log it must not consume it out from under the route."""
    resp = client.post("/api/backends/config", json={"a": 1, "b": [2, 3]})
    assert resp.status_code == 200
    assert resp.json()["seen"] == {"a": 1, "b": [2, 3]}
