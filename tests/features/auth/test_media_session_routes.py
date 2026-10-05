from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.features.auth.routes import build_router
from src.platform.security import Auth, LoginHandoffStore
from src.platform.security import current_user
from src.platform.security.media_session import MEDIA_COOKIE, token_lifetime_seconds
from src.platform.security.user import AccountType, User

SECRET = "media-session-test-secret"


def _token(minutes=60, user_id="user-1"):
    expires = datetime.utcnow() + timedelta(minutes=minutes)
    return jwt.encode({"sub": "alice", "user_id": user_id, "exp": expires}, SECRET, algorithm="HS256")


def _media_cookie(response):
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        if MEDIA_COOKIE in parsed:
            return parsed[MEDIA_COOKIE], header
    return None, None


@pytest.fixture
def user():
    return User(
        id="user-1", username="alice", email="alice@example.com",
        password_hash="h", account_type=AccountType.USER,
    )


@pytest.fixture
def auth(user):
    manager = Mock(spec=Auth)
    manager.get_user_from_token.side_effect = lambda token: user if token.count(".") == 2 else None
    return manager


@pytest.fixture
def handoff():
    return LoginHandoffStore()


@pytest.fixture
def client(auth, handoff):
    previous = current_user._auth
    current_user.set_auth(auth)
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(auth=auth, login_handoff=handoff, content_policy_resolver=None)))
    with TestClient(app) as test_client:
        yield test_client
    current_user._auth = previous


class TestMediaCookieIssued:
    def test_login_sets_an_httponly_lax_cookie_carrying_the_token(self, client, auth, user):
        token = _token()
        auth.authenticate.return_value = (user, token)

        response = client.post("/api/auth/login", data={"username": "alice", "password": "pw"})

        morsel, header = _media_cookie(response)
        assert response.status_code == 200
        assert morsel.value == token
        assert morsel["path"] == "/api"
        assert morsel["samesite"].lower() == "lax"
        assert "httponly" in header.lower()
        assert 3500 <= int(morsel["max-age"]) <= 3600

    def test_the_cookie_is_secure_behind_an_https_proxy(self, client, auth, user):
        auth.authenticate.return_value = (user, _token())

        response = client.post(
            "/api/auth/login", data={"username": "alice", "password": "pw"},
            headers={"X-Forwarded-Proto": "https"},
        )

        _, header = _media_cookie(response)
        assert "secure" in header.lower()

    def test_a_failed_login_sets_no_cookie(self, client, auth):
        auth.authenticate.side_effect = ValueError("bad")

        response = client.post("/api/auth/login", data={"username": "alice", "password": "nope"})

        assert response.status_code == 401
        assert _media_cookie(response) == (None, None)

    def test_register_sets_the_cookie(self, client, auth, user):
        token = _token()
        auth.register.return_value = (user, token)

        response = client.post(
            "/api/auth/register",
            json={"username": "alice", "email": "alice@example.com", "password": "correct-horse-1"},
        )

        assert response.status_code == 200
        assert _media_cookie(response)[0].value == token

    def test_external_login_exchange_sets_the_cookie(self, client, handoff):
        token = _token()
        code = handoff.issue(token)

        response = client.post("/api/auth/external/exchange", json={"code": code})

        assert response.status_code == 200
        assert _media_cookie(response)[0].value == token

    def test_me_refreshes_the_cookie_from_the_bearer_token(self, client):
        token = _token()

        response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert _media_cookie(response)[0].value == token

    def test_me_without_a_token_sets_nothing(self, client):
        response = client.get("/api/auth/me")

        assert response.status_code == 401
        assert _media_cookie(response) == (None, None)


class TestMediaCookieCleared:
    def test_logout_expires_the_cookie(self, client):
        response = client.post("/api/auth/logout")

        morsel, _ = _media_cookie(response)
        assert response.status_code == 204
        assert morsel.value == ""
        assert int(morsel["max-age"]) == 0
        assert morsel["path"] == "/api"

    def test_an_expired_token_clears_rather_than_sets(self, client):
        response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_token(minutes=-5)}"})

        morsel, _ = _media_cookie(response)
        assert int(morsel["max-age"]) == 0


class TestTokenLifetime:
    def test_lifetime_follows_the_exp_claim(self):
        assert 590 <= token_lifetime_seconds(_token(minutes=10)) <= 600

    @pytest.mark.parametrize("token", ["", "not-a-jwt", jwt.encode({"sub": "x"}, SECRET, algorithm="HS256")])
    def test_a_token_without_a_readable_expiry_has_no_lifetime(self, token):
        assert token_lifetime_seconds(token) == 0
