import time
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from jose import jwt

from src.bootstrap.middleware import register_middleware
from src.platform.security import current_user
from src.platform.security.config import AuthConfig
from src.platform.security.media_session import media_cookie_name, partitioned_media_cookie_name
from src.platform.security.token import TokenCodec

SECRET = "session-renewal-middleware-secret"
HOUR = 3600
ORIGIN = "http://localhost:26731"
LOOPBACK = {"Host": "127.0.0.1:8005"}


def _token(issued_ago, lifetime=24 * HOUR):
    now = int(time.time())
    claims = {"sub": "alice", "user_id": "user-1", "iat": now - issued_ago, "exp": now - issued_ago + lifetime}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _media_cookie(response):
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        if media_cookie_name() in parsed:
            return parsed[media_cookie_name()]
    return None


def _set_cookies(response):
    cookies = {}
    for header in response.headers.get_list("set-cookie"):
        pair, *attributes = [part.strip() for part in header.split(";")]
        name, _, value = pair.partition("=")
        cookies[name] = (value.strip('"'), {key.lower(): val for key, _, val in (a.partition("=") for a in attributes)})
    return cookies


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    config = Mock(spec=AuthConfig)
    config.secret_key = SECRET
    config.algorithm = "HS256"
    config.access_token_expire_minutes = 60
    previous = current_user._auth
    current_user.set_auth(SimpleNamespace(tokens=TokenCodec(config)))

    app = FastAPI()

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/denied")
    async def denied():
        raise HTTPException(status_code=401, detail="no")

    @app.get("/stream")
    async def stream():
        async def chunks():
            for index in range(3):
                yield f"chunk-{index}\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    @app.get("/sets-cookie")
    async def sets_cookie():
        response = JSONResponse({"ok": True})
        response.set_cookie(media_cookie_name(), "managed-by-route", path="/api")
        return response

    @app.get("/sets-partitioned-cookie")
    async def sets_partitioned_cookie():
        response = JSONResponse({"ok": True})
        response.set_cookie(partitioned_media_cookie_name(), "managed-by-route", path="/api")
        return response

    @app.get("/clears-cookie")
    async def clears_cookie():
        response = JSONResponse({"ok": True})
        response.delete_cookie(media_cookie_name(), path="/api")
        return response

    register_middleware(app)
    with TestClient(app) as test_client:
        yield test_client
    current_user._auth = previous


def test_an_aged_token_gets_a_fresh_one_in_the_response_header(client):
    old = _token(13 * HOUR)

    response = client.get("/ok", headers=_auth_header(old))

    fresh = response.headers["x-refreshed-token"]
    claims = jwt.decode(fresh, SECRET, algorithms=["HS256"])
    assert response.status_code == 200
    assert fresh != old
    assert claims["sub"] == "alice"
    assert claims["user_id"] == "user-1"
    assert claims["exp"] - claims["iat"] == 24 * HOUR


def test_the_media_cookie_follows_the_fresh_token(client):
    response = client.get("/ok", headers=_auth_header(_token(13 * HOUR)))

    morsel = _media_cookie(response)
    assert morsel.value == response.headers["x-refreshed-token"]
    assert morsel["path"] == "/api"
    assert morsel["samesite"].lower() == "lax"
    assert 24 * HOUR - 60 <= int(morsel["max-age"]) <= 24 * HOUR


def test_a_loopback_renewal_appends_the_lax_and_the_partitioned_cookie(client):
    response = client.get("/ok", headers={**_auth_header(_token(13 * HOUR)), **LOOPBACK})

    fresh = response.headers["x-refreshed-token"]
    cookies = _set_cookies(response)
    lax_value, lax = cookies[media_cookie_name()]
    partitioned_value, partitioned = cookies[partitioned_media_cookie_name()]
    assert list(cookies) == [media_cookie_name(), f"{media_cookie_name()}_p"]
    assert lax_value == partitioned_value == fresh
    assert lax["samesite"].lower() == "lax"
    assert "secure" not in lax and "partitioned" not in lax
    assert partitioned["samesite"].lower() == "none"
    assert {"secure", "partitioned", "httponly"} <= set(partitioned)
    assert partitioned["path"] == "/api"
    assert 24 * HOUR - 60 <= int(partitioned["max-age"]) <= 24 * HOUR


def test_a_lan_http_renewal_appends_only_the_lax_cookie(client):
    response = client.get("/ok", headers={**_auth_header(_token(13 * HOUR)), "Host": "192.168.1.5:8005"})

    assert response.headers["x-refreshed-token"]
    assert list(_set_cookies(response)) == [media_cookie_name()]


def test_an_https_renewal_appends_both_cookies(client):
    response = client.get(
        "/ok",
        headers={**_auth_header(_token(13 * HOUR)), "Host": "photos.example.com", "X-Forwarded-Proto": "https"},
    )

    assert list(_set_cookies(response)) == [media_cookie_name(), f"{media_cookie_name()}_p"]


def test_a_response_that_sets_only_the_partitioned_cookie_is_not_renewed(client):
    response = client.get("/sets-partitioned-cookie", headers={**_auth_header(_token(13 * HOUR)), **LOOPBACK})

    assert "x-refreshed-token" not in response.headers
    assert list(_set_cookies(response)) == [partitioned_media_cookie_name()]


def test_a_loopback_response_that_sets_the_lax_cookie_is_not_given_a_second_pair(client):
    response = client.get("/sets-cookie", headers={**_auth_header(_token(13 * HOUR)), **LOOPBACK})

    assert "x-refreshed-token" not in response.headers
    assert list(_set_cookies(response)) == [media_cookie_name()]


def test_a_young_token_is_left_alone(client):
    response = client.get("/ok", headers=_auth_header(_token(60)))

    assert "x-refreshed-token" not in response.headers
    assert _media_cookie(response) is None


def test_a_failed_response_is_not_renewed(client):
    response = client.get("/denied", headers=_auth_header(_token(13 * HOUR)))

    assert response.status_code == 401
    assert "x-refreshed-token" not in response.headers
    assert _media_cookie(response) is None


def test_a_request_without_a_bearer_token_is_left_alone(client):
    response = client.get("/ok")

    assert "x-refreshed-token" not in response.headers


def test_a_non_bearer_authorization_is_left_alone(client):
    response = client.get("/ok", headers={"Authorization": f"Basic {_token(13 * HOUR)}"})

    assert "x-refreshed-token" not in response.headers


def test_a_token_without_iat_is_left_alone(client):
    legacy = jwt.encode(
        {"sub": "alice", "user_id": "user-1", "exp": int(time.time()) + 60},
        SECRET,
        algorithm="HS256",
    )

    response = client.get("/ok", headers=_auth_header(legacy))

    assert "x-refreshed-token" not in response.headers


def test_an_expired_token_is_left_alone(client):
    response = client.get("/ok", headers=_auth_header(_token(25 * HOUR)))

    assert "x-refreshed-token" not in response.headers


def test_a_response_that_sets_the_media_cookie_is_not_renewed(client):
    response = client.get("/sets-cookie", headers=_auth_header(_token(13 * HOUR)))

    assert "x-refreshed-token" not in response.headers
    assert _media_cookie(response).value == "managed-by-route"


def test_a_response_that_clears_the_media_cookie_is_not_renewed(client):
    response = client.get("/clears-cookie", headers=_auth_header(_token(13 * HOUR)))

    assert "x-refreshed-token" not in response.headers
    assert _media_cookie(response).value == ""


def test_a_streaming_response_is_renewed_and_still_streams(client):
    response = client.get("/stream", headers=_auth_header(_token(13 * HOUR)))

    assert response.text == "chunk-0\nchunk-1\nchunk-2\n"
    assert response.headers["x-refreshed-token"]
    assert _media_cookie(response).value == response.headers["x-refreshed-token"]


def test_cors_exposes_the_refreshed_token_header(client):
    response = client.get("/ok", headers={"Origin": ORIGIN})

    exposed = response.headers["access-control-expose-headers"]
    assert "X-Refreshed-Token" in exposed


def test_the_renewal_header_is_visible_cross_origin(client):
    response = client.get("/ok", headers={**_auth_header(_token(13 * HOUR)), "Origin": ORIGIN})

    assert response.headers["x-refreshed-token"]
    assert "X-Refreshed-Token" in response.headers["access-control-expose-headers"]


def test_an_api_key_bearer_is_never_offered_to_the_jwt_renewal(client, monkeypatch):
    offered = []
    monkeypatch.setattr("src.bootstrap.middleware.renewed_token", lambda codec, token: offered.append(token))

    response = client.get("/ok", headers=_auth_header("pui_" + "a" * 46))
    client.get("/ok", headers=_auth_header("a.jwt.token"))

    assert response.status_code == 200
    assert "x-refreshed-token" not in response.headers
    assert offered == ["a.jwt.token"]
    assert _media_cookie(response) is None
