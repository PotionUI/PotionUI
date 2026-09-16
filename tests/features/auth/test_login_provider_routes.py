from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.auth.routes import build_router
from src.platform.plugins.login_providers import (
    DuplicateLoginProviderError,
    InvalidLoginProviderError,
    LoginProviderDefinition,
    LoginProviderRegistry,
    login_provider_registry,
    source_from_start_path,
)
from src.platform.security import Auth
from src.platform.security.login_handoff import LoginHandoffStore
from src.plugin_api.identity import register_login_provider, unregister_login_provider


@pytest.fixture
def clean_registry():
    previous = login_provider_registry.all()
    for definition in previous:
        login_provider_registry.unregister(definition.id)
    yield login_provider_registry
    for definition in login_provider_registry.all():
        login_provider_registry.unregister(definition.id)
    for definition in previous:
        login_provider_registry.register(definition)


@pytest.fixture
def handoff():
    return LoginHandoffStore()


@pytest.fixture
def client(handoff):
    container = SimpleNamespace(auth=Mock(spec=Auth), login_handoff=handoff)
    app = FastAPI()
    app.include_router(build_router(container))
    return TestClient(app)


def test_providers_is_empty_when_no_plugin_registered_one(client, clean_registry):
    response = client.get("/api/auth/providers")

    assert response.status_code == 200
    assert response.json() == []


def test_providers_lists_a_registered_provider(client, clean_registry):
    register_login_provider("acme-sso", "Acme SSO", "/api/plugins/acme-sso/start")

    response = client.get("/api/auth/providers")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "acme-sso",
            "label": "Acme SSO",
            "start_path": "/api/plugins/acme-sso/start",
        }
    ]


def test_providers_keeps_registration_order(client, clean_registry):
    register_login_provider("first", "First", "/api/plugins/first/start")
    register_login_provider("second", "Second", "/api/plugins/second/start")

    ids = [entry["id"] for entry in client.get("/api/auth/providers").json()]

    assert ids == ["first", "second"]


def test_providers_needs_no_authentication(client, clean_registry):
    register_login_provider("acme-sso", "Acme SSO", "/api/plugins/acme-sso/start")

    response = client.get("/api/auth/providers")

    assert response.status_code == 200
    assert "WWW-Authenticate" not in response.headers


def test_providers_drops_an_unregistered_provider(client, clean_registry):
    register_login_provider("acme-sso", "Acme SSO", "/api/plugins/acme-sso/start")
    unregister_login_provider("acme-sso")

    assert client.get("/api/auth/providers").json() == []


def test_exchange_trades_a_code_for_the_token(client, handoff):
    code = handoff.issue("token-abc")

    response = client.post("/api/auth/external/exchange", json={"code": code})

    assert response.status_code == 200
    assert response.json() == {"access_token": "token-abc", "token_type": "bearer"}


def test_exchange_rejects_a_reused_code(client, handoff):
    code = handoff.issue("token-abc")
    client.post("/api/auth/external/exchange", json={"code": code})

    response = client.post("/api/auth/external/exchange", json={"code": code})

    assert response.status_code == 401


def test_exchange_rejects_an_unknown_code(client):
    response = client.post("/api/auth/external/exchange", json={"code": "nope"})

    assert response.status_code == 401


class TestLoginProviderRegistry:
    def test_duplicate_ids_are_rejected(self):
        registry = LoginProviderRegistry()
        registry.register(LoginProviderDefinition("a", "A", "/api/plugins/a/start"))

        with pytest.raises(DuplicateLoginProviderError):
            registry.register(LoginProviderDefinition("a", "A again", "/api/plugins/a/s"))

    def test_a_missing_label_is_rejected(self):
        registry = LoginProviderRegistry()

        with pytest.raises(InvalidLoginProviderError):
            registry.register(LoginProviderDefinition("a", "", "/api/plugins/a/start"))

    def test_a_relative_start_path_is_rejected(self):
        registry = LoginProviderRegistry()

        with pytest.raises(InvalidLoginProviderError):
            registry.register(LoginProviderDefinition("a", "A", "plugins/a/start"))

    def test_unregister_source_drops_every_provider_owned_by_a_plugin(self):
        registry = LoginProviderRegistry()
        registry.register(
            LoginProviderDefinition("a", "A", "/api/plugins/p/a", source="p")
        )
        registry.register(
            LoginProviderDefinition("b", "B", "/api/plugins/p/b", source="p")
        )
        registry.register(
            LoginProviderDefinition("c", "C", "/api/plugins/q/c", source="q")
        )

        registry.unregister_source("p")

        assert [definition.id for definition in registry.all()] == ["c"]

    def test_source_is_read_off_the_plugin_route_prefix(self, clean_registry):
        register_login_provider("acme-sso", "Acme SSO", "/api/plugins/acme-login/start")

        assert login_provider_registry.get("acme-sso").source == "acme-login"

    def test_source_falls_back_to_the_provider_id_off_a_plugin_route(
        self, clean_registry
    ):
        register_login_provider("acme-sso", "Acme SSO", "/elsewhere/start")

        assert login_provider_registry.get("acme-sso").source == "acme-sso"

    def test_source_from_start_path_ignores_non_plugin_routes(self):
        assert source_from_start_path("/api/plugins/p/start") == "p"
        assert source_from_start_path("/api/auth/login") is None
        assert source_from_start_path("/api/plugins/") is None


class TestLoginHandoffStore:
    def test_a_code_redeems_exactly_once(self):
        store = LoginHandoffStore()
        code = store.issue("token")

        assert store.redeem(code) == "token"
        assert store.redeem(code) is None

    def test_an_expired_code_does_not_redeem(self):
        store = LoginHandoffStore(ttl_seconds=-1)
        code = store.issue("token")

        assert store.redeem(code) is None

    def test_two_codes_are_independent(self):
        store = LoginHandoffStore()
        first = store.issue("one")
        second = store.issue("two")

        assert store.redeem(second) == "two"
        assert store.redeem(first) == "one"

    def test_an_empty_code_never_redeems(self):
        store = LoginHandoffStore()
        store.issue("token")

        assert store.redeem("") is None
        assert store.redeem(None) is None

    def test_clear_drops_outstanding_codes(self):
        store = LoginHandoffStore()
        code = store.issue("token")

        store.clear()

        assert store.redeem(code) is None
