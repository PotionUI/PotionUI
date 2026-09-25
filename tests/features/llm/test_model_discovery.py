import functools
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.llm import routes as llm_mod
from src.features.llm.clients.ollama import OllamaClient
from src.features.llm.clients.openai import OpenAIClient
from src.features.llm.gateway import LLMGateway
from src.features.llm.model_listing import DiscoveredModel, ModelListingError
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

_REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def http(monkeypatch):
    calls = []

    def install(handler):
        def recording(request):
            calls.append(request)
            return handler(request)

        monkeypatch.setattr(
            httpx, "AsyncClient",
            functools.partial(_REAL_ASYNC_CLIENT, transport=httpx.MockTransport(recording)),
        )
        return calls

    return install


class TestOllamaLister:
    @pytest.mark.asyncio
    async def test_lists_tags_with_size_and_details(self, http):
        calls = http(lambda request: httpx.Response(200, json={"models": [
            {
                "name": "qwen3:8b", "size": 5200000000, "modified_at": "2026-09-01T10:00:00Z",
                "details": {"parameter_size": "8.2B", "quantization_level": "Q4_K_M", "family": "qwen3"},
            },
            {"name": "Llama3.2:latest", "size": 2000000000, "details": {}},
        ]}))

        models = await OllamaClient().list_models("http://ollama.local:11434/")

        assert str(calls[0].url) == "http://ollama.local:11434/api/tags"
        assert "authorization" not in calls[0].headers
        assert [m.id for m in models] == ["Llama3.2:latest", "qwen3:8b"]
        qwen = models[1]
        assert qwen.size == 5200000000
        assert qwen.modified_at == "2026-09-01T10:00:00Z"
        assert qwen.details == {"parameter_size": "8.2B", "quantization": "Q4_K_M", "family": "qwen3"}
        assert models[0].details is None

    @pytest.mark.asyncio
    async def test_connection_refused_names_the_server(self, http):
        def refuse(request):
            raise httpx.ConnectError("refused", request=request)

        http(refuse)
        with pytest.raises(ModelListingError) as exc_info:
            await OllamaClient().list_models("http://127.0.0.1:11434")
        assert str(exc_info.value) == "Couldn't reach Ollama at http://127.0.0.1:11434: connection refused."

    @pytest.mark.asyncio
    async def test_timeout(self, http):
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        http(slow)
        with pytest.raises(ModelListingError, match="timed out after 8s"):
            await OllamaClient().list_models("http://127.0.0.1:11434")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "localhost:11434", ""])
    async def test_rejects_non_http_schemes_without_a_request(self, http, url):
        calls = http(lambda request: httpx.Response(200, json={"models": []}))
        with pytest.raises(ModelListingError, match="http:// or https://"):
            await OllamaClient().list_models(url)
        assert calls == []


class TestOpenAICompatibleLister:
    @pytest.mark.asyncio
    async def test_lists_models_with_bearer_key(self, http):
        calls = http(lambda request: httpx.Response(200, json={"object": "list", "data": [
            {"id": "gpt-4o", "owned_by": "openai"},
            {"id": "gpt-4o-mini"},
            {"object": "model"},
        ]}))

        models = await OpenAIClient().list_models("https://api.example.com/v1", "sk-secret")

        assert str(calls[0].url) == "https://api.example.com/v1/models"
        assert calls[0].headers["authorization"] == "Bearer sk-secret"
        assert [m.id for m in models] == ["gpt-4o", "gpt-4o-mini"]
        assert models[0].details == {"owned_by": "openai"}

    @pytest.mark.asyncio
    async def test_unauthorized_points_at_the_key(self, http):
        http(lambda request: httpx.Response(401, json={"error": "bad key"}))
        with pytest.raises(ModelListingError) as exc_info:
            await OpenAIClient().list_models("https://api.example.com/v1", "sk-wrong")
        message = str(exc_info.value)
        assert "HTTP 401" in message and "API key" in message
        assert "sk-wrong" not in message

    @pytest.mark.asyncio
    async def test_non_json_body(self, http):
        http(lambda request: httpx.Response(200, text="<html>"))
        with pytest.raises(ModelListingError, match="isn't JSON"):
            await OpenAIClient().list_models("https://api.example.com/v1")


class TestGatewayCapability:
    def test_types_report_listing_support(self):
        types = {t["type"]: t["supports_model_listing"] for t in LLMGateway(Mock()).provider_types()}
        assert types == {"ollama": True, "openai": True, "native": False}

    @pytest.mark.asyncio
    async def test_unsupported_type_raises(self):
        with pytest.raises(ValueError):
            await LLMGateway(Mock()).list_models("native", "http://x")


def _user(account_type: AccountType) -> User:
    return User(username="u", email="u@example.com", password_hash="x", account_type=account_type, id="u-1")


def _app(user: User, repository=None, gateway=None) -> TestClient:
    controller = llm_mod.LLMController(
        llm_repository=repository or Mock(),
        llm_service=gateway or Mock(),
        settings=Mock(),
        plugin_registry=Mock(),
    )
    app = FastAPI()
    app.include_router(llm_mod.build_router(SimpleNamespace(llm_controller=controller)))
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _listing_gateway(models=None, error=None):
    gateway = Mock()
    gateway.supports_model_listing.side_effect = lambda type_id: type_id in ("ollama", "openai")
    gateway.list_models = AsyncMock(side_effect=error, return_value=models or [])
    return gateway


class TestDiscoverRoute:
    def test_non_admin_is_forbidden(self):
        gateway = _listing_gateway()
        client = _app(_user(AccountType.USER), gateway=gateway)
        assert client.post("/api/llm/models/discover", json={"type": "ollama", "base_url": "http://x"}).status_code == 403
        assert client.get("/api/llm/types").status_code == 403
        gateway.list_models.assert_not_called()

    def test_unsupported_type(self):
        gateway = _listing_gateway()
        resp = _app(_user(AccountType.ADMIN), gateway=gateway).post(
            "/api/llm/models/discover", json={"type": "anthropic", "base_url": "https://x"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == {"supported": False, "models": [], "error": None}
        gateway.list_models.assert_not_called()

    def test_lists_models(self):
        gateway = _listing_gateway(models=[DiscoveredModel(id="qwen3:8b", size=5)])
        resp = _app(_user(AccountType.ADMIN), gateway=gateway).post(
            "/api/llm/models/discover", json={"type": "ollama", "base_url": "http://ollama:11434"},
        )
        data = resp.json()["data"]
        assert data["supported"] is True and data["error"] is None
        assert data["models"][0]["id"] == "qwen3:8b"
        gateway.list_models.assert_awaited_once_with("ollama", "http://ollama:11434", None)

    def test_listing_error_is_returned_inline(self):
        gateway = _listing_gateway(error=ModelListingError("Couldn't reach Ollama at http://x: connection refused."))
        resp = _app(_user(AccountType.ADMIN), gateway=gateway).post(
            "/api/llm/models/discover", json={"type": "ollama", "base_url": "http://x"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["error"] == "Couldn't reach Ollama at http://x: connection refused."

    def test_stored_key_is_used_via_config_id_and_never_returned(self):
        repository = Mock()
        repository.get_configuration.return_value = SimpleNamespace(type="openai", api_key="sk-stored")
        gateway = _listing_gateway(models=[DiscoveredModel(id="gpt-4o")])
        resp = _app(_user(AccountType.ADMIN), repository=repository, gateway=gateway).post(
            "/api/llm/models/discover",
            json={"type": "openai", "base_url": "https://api.example.com/v1", "config_id": "cfg-1"},
        )
        repository.get_configuration.assert_called_once_with("cfg-1")
        gateway.list_models.assert_awaited_once_with("openai", "https://api.example.com/v1", "sk-stored")
        assert "sk-stored" not in resp.text

    def test_typed_key_wins_over_stored_key(self):
        repository = Mock()
        gateway = _listing_gateway()
        _app(_user(AccountType.ADMIN), repository=repository, gateway=gateway).post(
            "/api/llm/models/discover",
            json={"type": "openai", "base_url": "https://h/v1", "api_key": "sk-typed", "config_id": "cfg-1"},
        )
        repository.get_configuration.assert_not_called()
        gateway.list_models.assert_awaited_once_with("openai", "https://h/v1", "sk-typed")

    def test_stored_key_of_another_type_is_not_used(self):
        repository = Mock()
        repository.get_configuration.return_value = SimpleNamespace(type="openai", api_key="sk-stored")
        gateway = _listing_gateway()
        _app(_user(AccountType.ADMIN), repository=repository, gateway=gateway).post(
            "/api/llm/models/discover", json={"type": "ollama", "base_url": "http://h", "config_id": "cfg-1"},
        )
        gateway.list_models.assert_awaited_once_with("ollama", "http://h", None)

    def test_types_endpoint(self):
        gateway = Mock()
        gateway.provider_types.return_value = [{"type": "ollama", "supports_model_listing": True}]
        resp = _app(_user(AccountType.ADMIN), gateway=gateway).get("/api/llm/types")
        assert resp.json()["data"] == {"types": [{"type": "ollama", "supports_model_listing": True}]}
