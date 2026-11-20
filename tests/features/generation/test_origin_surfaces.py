from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.api_keys.repository import ApiKeyRepository
from src.features.generation.dto import GenerationRequest
from src.features.generation.exceptions import GenerationNotFoundException
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.history_filter import HistoryFilter
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.routes import GenerationController, build_router
from src.features.generation.run_report_recorder import RunReportRecorder
from src.platform.database import get_cursor
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.features.mcp.support import api_key

MCP_ORIGIN = {"kind": "mcp", "key_id": "k1", "key_name": "laptop"}
EDITOR_ORIGIN = {"kind": "editor", "edit_id": "e1", "step_id": "s1", "task": "edit_region", "run_id": "er_1"}
THEIR_ORIGIN = {"kind": "mcp", "key_id": "k2", "key_name": "theirs"}


def _user(user_id="u1", account_type=AccountType.USER):
    return User(
        id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="$2b$12$x",
        account_type=account_type, created_at=datetime.utcnow(), last_login=None,
    )


@pytest.fixture
def data(mock_db):
    with get_cursor() as cursor:
        for user_id in ("u1", "u2"):
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, 'x')",
                (user_id, user_id, f"{user_id}@example.com"),
            )
    keys = ApiKeyRepository()
    keys.create(api_key("k1", "u1"))
    with get_cursor() as cursor:
        cursor.execute("UPDATE api_keys SET name = 'laptop' WHERE id = 'k1'")
    keys.create(api_key("k2", "u2"))
    with get_cursor() as cursor:
        cursor.execute("UPDATE api_keys SET name = 'theirs' WHERE id = 'k2'")
    repository = GenerationRepository()
    for generation_id, user_id, origin, tab_id in (
        ("g-mcp", "u1", MCP_ORIGIN, None),
        ("g-editor", "u1", EDITOR_ORIGIN, None),
        ("g-tab", "u1", None, "tab-1"),
        ("g-theirs", "u2", THEIR_ORIGIN, None),
    ):
        repository.create(Generation(
            id=generation_id, preset_id="p", form_data={}, user_id=user_id, mode="txt2img", origin=origin, tab_id=tab_id,
        ))
    query = GenerationHistoryQuery(repository, origin_key_names=keys.names_for_user)
    return SimpleNamespace(keys=keys, repository=repository, query=query)


def history_ids(data, user_id="u1", **filters):
    result = data.query.get_history(HistoryFilter(user_id=user_id, **filters), limit=50)
    return {item["id"] for item in result["generations"]}


def history_item(data, generation_id, user_id="u1"):
    result = data.query.get_history(HistoryFilter(user_id=user_id), limit=50)
    return next(item for item in result["generations"] if item["id"] == generation_id)


def test_a_client_cannot_set_an_origin_on_a_generation_request():
    request = GenerationRequest(preset_id="p", origin={"kind": "mcp"}, _origin={"kind": "mcp"})

    assert request._origin is None


def test_a_generation_created_with_an_origin_reads_it_back(data):
    assert data.repository.get_by_id("g-mcp").origin == MCP_ORIGIN
    assert data.repository.get_by_id("g-tab").origin is None


def test_history_items_carry_the_origin_with_the_current_key_name(data):
    assert history_item(data, "g-mcp")["origin"] == {**MCP_ORIGIN, "key_current_name": "laptop"}
    assert history_item(data, "g-editor")["origin"] == EDITOR_ORIGIN
    assert history_item(data, "g-tab")["origin"] is None


def test_the_detail_carries_the_same_origin_shape(data):
    detail = data.query.get_by_id("g-mcp", "u1", include_files=False)

    assert detail["origin"] == {**MCP_ORIGIN, "key_current_name": "laptop"}


def test_another_users_generation_and_origin_are_never_visible(data):
    assert "g-theirs" not in history_ids(data)
    with pytest.raises(GenerationNotFoundException):
        data.query.get_by_id("g-theirs", "u1", include_files=False)


def test_a_renamed_key_keeps_its_stored_name_and_shows_the_current_one(data):
    data.keys.update("k1", "renamed", "[]")

    origin = history_item(data, "g-mcp")["origin"]

    assert origin["key_name"] == "laptop" and origin["key_current_name"] == "renamed"


def test_a_deleted_key_has_no_current_name(data):
    with get_cursor() as cursor:
        cursor.execute("DELETE FROM api_keys WHERE id = 'k1'")

    origin = history_item(data, "g-mcp")["origin"]

    assert origin["key_name"] == "laptop" and origin["key_current_name"] is None


def test_key_names_are_only_looked_up_among_the_owners_own_keys(data):
    assert data.keys.names_for_user("u1", ["k1", "k2"]) == {"k1": "laptop"}


@pytest.mark.parametrize("origin,expected", [
    ("mcp", {"g-mcp"}),
    ("editor", {"g-editor"}),
    ("tab", {"g-tab"}),
    ("chat", set()),
])
def test_the_history_origin_filter_selects_by_kind(data, origin, expected):
    assert history_ids(data, origin=origin) == expected


def test_the_origin_filter_cannot_reach_another_users_generations(data):
    assert history_ids(data, user_id="u2", origin="mcp") == {"g-theirs"}
    assert history_ids(data, user_id="u1", origin="mcp") == {"g-mcp"}


def test_the_origin_filter_counts_match(data):
    assert data.repository.count_by_status(HistoryFilter(user_id="u1", origin="tab")) == 1


def test_a_malformed_stored_origin_counts_as_none_recorded(data):
    with get_cursor() as cursor:
        cursor.execute("UPDATE generations SET origin = 'not json' WHERE id = 'g-mcp'")

    assert "g-mcp" in history_ids(data, origin="tab")


@pytest.fixture
def client(data):
    facade = Mock(spec=GenerationHistoryFacade)
    facade.query = data.query
    facade.get_history_async = AsyncMock(
        return_value={"generations": [], "total": 0, "limit": 50, "offset": 0, "filters": {}}
    )
    orchestrator = Mock()
    orchestrator.get_queue_snapshot.return_value = {
        "pending": [{"generation_id": "g-mcp", "backend_id": "b1", "tab_id": None, "enqueued_at": 1.0,
                     "queue_position": 0, "queue_depth": 1, "waiting_reason": None}],
        "running": [
            {"generation_id": "g-tab", "backend_id": "b1", "preset_id": "p", "tab_id": "tab-1", "progress": 0.5},
            {"generation_id": "g-theirs", "backend_id": "b1", "preset_id": "p", "tab_id": None, "progress": 0.1},
        ],
    }
    orchestrator.status_tracker.list_active.return_value = [
        SimpleNamespace(user_id="u1", model_dump=lambda: {"id": "g-mcp", "status": "pending"}),
        SimpleNamespace(user_id="u1", model_dump=lambda: {"id": "g-tab", "status": "running"}),
    ]
    controller = GenerationController(orchestrator, facade, Mock(), Mock(spec=RunReportRecorder))
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(_generation_controller=controller)))
    app.dependency_overrides[get_current_active_user] = lambda: _user()
    http = TestClient(app)
    http.facade = facade
    return http


def test_an_unknown_origin_filter_is_a_422(client):
    response = client.get("/api/generations/history", params={"origin": "bogus"})

    assert response.status_code == 422
    client.facade.get_history_async.assert_not_awaited()


@pytest.mark.parametrize("origin", ["mcp", "editor", "chat", "tab"])
def test_a_known_origin_filter_reaches_the_history_filter(client, origin):
    response = client.get("/api/generations/history", params={"origin": origin})

    assert response.status_code == 200
    assert client.facade.get_history_async.await_args.args[0].origin == origin


def test_the_queue_labels_each_item_and_carries_the_origin(client):
    body = client.get("/api/generations/queue").json()["data"]

    pending, running = body["pending"][0], body["running"]
    assert pending["status"] == "pending" and pending["origin"] == {**MCP_ORIGIN, "key_current_name": "laptop"}
    assert (pending["preset_id"], pending["mode"], pending["has_tab"]) == ("p", "txt2img", False)
    assert pending["created_at"] and pending["queue_position"] == 0
    tab_item = running[0]
    assert (tab_item["status"], tab_item["has_tab"], tab_item["origin"], tab_item["tab_id"]) == ("running", True, None, "tab-1")


def test_the_queue_never_reveals_another_users_origin(client):
    running = client.get("/api/generations/queue").json()["data"]["running"]

    theirs = next(item for item in running if item["generation_id"] == "g-theirs")
    assert theirs["origin"] is None and theirs["mode"] is None


def test_the_active_list_carries_the_origin(client):
    items = client.get("/api/generations/list").json()["data"]

    assert {item["id"]: item["origin"] for item in items} == {
        "g-mcp": {**MCP_ORIGIN, "key_current_name": "laptop"}, "g-tab": None,
    }
