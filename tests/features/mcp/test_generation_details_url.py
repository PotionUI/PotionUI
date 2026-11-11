import json
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from starlette.requests import Request

from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.builtin.organize_gallery_tool import OrganizeGalleryTool
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.routes import request_origin
from tests.features.mcp.support import build_collaborators, handle_method, user_with_every_tool


@pytest.fixture(autouse=True)
def _user_with_every_tool(mcp_db):
    user_with_every_tool(mcp_db, "user-1")


def _collaborators(history_facade=None, orchestrator=None, public_base_url=""):
    registry = ToolRegistry()
    registry.register(OrganizeGalleryTool())
    registry.register(StartGenerationTool())
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    settings = Mock()
    settings.get_setting.return_value = public_base_url
    return build_collaborators(registry, ToolServices(
        llm_repository=llm_repository,
        generation_history_facade=history_facade,
        generation_orchestrator=orchestrator,
        plugin_registry=Mock(),
        settings=settings,
    ))


def _history_facade():
    facade = MagicMock()
    facade.get_by_id.return_value = {
        "id": "gen-1", "status": "completed", "form_data": {"prompt": "a fox"},
        "tags": [], "files": [], "preset_name": "SDXL",
    }
    facade.get_history.return_value = {
        "generations": [dict(facade.get_by_id.return_value)], "total": 1,
    }
    return facade


async def _call(collaborators, name, arguments, origin):
    result = await handle_method(
        collaborators, "tools/call", {"name": name, "arguments": arguments}, "user-1", request_origin=origin
    )
    return json.loads(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_get_returns_the_details_url_from_the_request_origin(mcp_db):
    payload = await _call(
        _collaborators(_history_facade()), "organize_gallery",
        {"operation": "get", "generation_id": "gen-1"}, "http://127.0.0.1:26730",
    )

    assert payload["details_url"] == "http://127.0.0.1:26730/history?generation=gen-1"


@pytest.mark.asyncio
async def test_list_recent_returns_a_details_url_per_generation(mcp_db):
    payload = await _call(
        _collaborators(_history_facade()), "organize_gallery",
        {"operation": "list_recent"}, "https://potion.test",
    )

    assert payload["generations"][0]["details_url"] == "https://potion.test/history?generation=gen-1"


@pytest.mark.asyncio
async def test_configured_public_base_url_beats_the_request_origin(mcp_db):
    payload = await _call(
        _collaborators(_history_facade(), public_base_url="https://public.example.com"), "organize_gallery",
        {"operation": "get", "generation_id": "gen-1"}, "http://127.0.0.1:26730",
    )

    assert payload["details_url"] == "https://public.example.com/history?generation=gen-1"


@pytest.mark.asyncio
async def test_start_generation_returns_the_details_url(mcp_db):
    orchestrator = AsyncMock()
    orchestrator.start_generation.return_value = {"generation_id": "gen-9", "status": {"status": "pending"}}

    payload = await _call(
        _collaborators(orchestrator=orchestrator), "start_generation",
        {"preset_id": "sdxl/base", "prompt": "a fox"}, "https://potion.test",
    )

    assert payload["generation_id"] == "gen-9"
    assert payload["details_url"] == "https://potion.test/history?generation=gen-9"


def _request(headers, scheme="http", server=("127.0.0.1", 26730)):
    return Request({
        "type": "http", "method": "POST", "path": "/api/mcp", "scheme": scheme, "server": server,
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()], "query_string": b"",
    })


def test_request_origin_prefers_forwarded_headers():
    request = _request({"host": "internal:26730", "x-forwarded-proto": "https", "x-forwarded-host": "potion.example.com"})

    assert request_origin(request) == "https://potion.example.com"


def test_request_origin_falls_back_to_the_host_header():
    assert request_origin(_request({"host": "192.168.1.5:26730"})) == "http://192.168.1.5:26730"


def test_request_origin_takes_the_first_of_several_forwarded_hosts():
    request = _request({"host": "internal", "x-forwarded-proto": "https", "x-forwarded-host": "a.com, b.com"})

    assert request_origin(request) == "https://a.com"


def test_request_origin_falls_back_to_the_server_netloc_without_a_host_header():
    assert request_origin(_request({})) == "http://127.0.0.1:26730"


def test_request_origin_ignores_an_empty_forwarded_proto():
    request = _request({"host": "potion.test", "x-forwarded-proto": ""}, scheme="https")

    assert request_origin(request) == "https://potion.test"


@pytest.mark.asyncio
async def test_start_generation_without_a_request_origin_returns_a_relative_url(mcp_db):
    orchestrator = AsyncMock()
    orchestrator.start_generation.return_value = {"generation_id": "gen-9", "status": {"status": "pending"}}

    payload = await _call(
        _collaborators(orchestrator=orchestrator), "start_generation",
        {"preset_id": "sdxl/base", "prompt": "a fox"}, "",
    )

    assert payload["details_url"] == "/history?generation=gen-9"
