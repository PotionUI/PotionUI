"""HTTP contract tests for the clean normalized Prompt API.

The controller holds a `PromptDatabaseCollaborators` bundle (`collaborators`
fixture, a MagicMock standing in for it) and calls `src.features.
prompt_database.operations` functions directly (module-level, no injected
manager) plus raw `collaborators.repository` reads for plain listings.
`mock_operations` patches the `operations` module as imported into
routes.py, so tests assert against it exactly like the previous manager
mock (see tests/features/user_groups/test_routes.py for the established
pattern)."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.features.prompt_database import routes as routes_module
from src.features.prompt_database.routes import (
    PromptDatabaseController,
    build_router,
)
from src.platform.plugins.prompt_importers import PromptImporterDefinition, PromptImporterRegistry
from src.plugin_api.prompts import PromptImportOutcome
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from src.features.segments.dto import RichSegment
from src.features.prompt_database.records import Prompt
from src.features.generation.records import File, Generation
from src.features.downloads.models import Download, DownloadStatus, DownloadType


class MockUser:
    id = "user-1"
    account_type = "ADMIN"


def make_prompt(prompt_id: str = "prompt-1", text: str = "a fox") -> Prompt:
    return Prompt(
        id=prompt_id,
        user_id="user-1",
        name="Fox",
        segments=[RichSegment(content=text, name="Subject")],
        flattened_text=text,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


@pytest.fixture
def collaborators():
    """Stands in for `PromptDatabaseCollaborators` - a MagicMock satisfies
    the controller/operations duck-typing without constructing real
    repository/vector-store/embedding-provider collaborators."""
    return MagicMock()


@pytest.fixture
def mock_operations(monkeypatch):
    """Patch the `operations` module as seen by routes.py."""
    mock = Mock()
    monkeypatch.setattr(routes_module, "operations", mock)
    return mock


@pytest.fixture
def generation_repo_mock():
    """The route reaches `generation_repo` directly (reads go route ->
    repository), so it must be patched here - unpatched, these tests would
    hit whatever database `src.platform.database.db` is pointed at."""
    with patch("src.features.prompt_database.routes.generation_repo") as mock:
        mock.usage_stats_by_source_prompt.return_value = {}
        mock.get_by_source_prompt.return_value = []
        mock.count_by_source_prompt.return_value = 0
        yield mock


@pytest.fixture
def prompt_importer_registry():
    return PromptImporterRegistry()


@pytest.fixture
def built_router(collaborators, generation_repo_mock, prompt_importer_registry):
    return build_router(
        SimpleNamespace(
            prompt_database_controller=PromptDatabaseController(collaborators),
            settings=MagicMock(),
            download_queue=MagicMock(),
            preset_template_loader=SimpleNamespace(_ensure_loaded=lambda: None, presets=[]),
            prompt_importer_registry=prompt_importer_registry,
        )
    )


@pytest.fixture
def client(built_router):
    app = FastAPI()
    app.dependency_overrides[get_current_active_user] = lambda: MockUser()
    app.include_router(built_router)
    with TestClient(app) as test_client:
        yield test_client


def test_router_exposes_only_clean_prompt_resource_prefix(built_router):
    paths = {route.path for route in built_router.routes}
    assert "/api/prompts" in paths
    assert "/api/prompts/{prompt_id}" in paths
    assert "/api/prompts/importers" in paths
    assert "/api/prompts/import/{importer_id}" in paths
    assert "/api/prompts/search" in paths
    assert all(not path.startswith("/api/prompt-database") for path in paths)


def test_create_delegates_complete_ordered_segment_aggregate(client, collaborators, mock_operations):
    mock_operations.create_prompt = AsyncMock(return_value=make_prompt())

    response = client.post(
        "/api/prompts",
        json={
            "name": "Fox",
            "usage_hint": "positive",
            "segments": [
                {"type": "content", "content": "a fox", "name": "Subject"},
                {"type": "break", "enabled": True},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["segments"][0]["name"] == "Subject"
    mock_operations.create_prompt.assert_awaited_once()
    call = mock_operations.create_prompt.await_args
    assert call is not None
    collab_arg, user_id, request = call.args
    assert collab_arg is collaborators
    assert user_id == "user-1"
    assert [segment.type for segment in request.segments] == ["content", "break"]


def test_list_delegates_browse_filters_without_generation_configuration(client, collaborators):
    collaborators.repository.get_all.return_value = [make_prompt()]
    collaborators.repository.count.return_value = 1

    response = client.get(
        "/api/prompts?limit=10&offset=2&source_provider=civitai&usage_hint=negative"
    )

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    collaborators.repository.get_all.assert_called_once_with(
        user_id="user-1",
        limit=10,
        offset=2,
        source_provider="civitai",
        base_model=None,
        model_id=None,
        usage_hint="negative",
        collection_id=None,
        sort_by="created_at",
        sort_order="desc",
    )
    collaborators.repository.count.assert_called_once_with(
        "user-1", "civitai", None, None, "negative", None,
    )


def test_list_merges_usage_aggregates_from_generation_repo(client, collaborators, generation_repo_mock):
    collaborators.repository.get_all.return_value = [make_prompt("prompt-1"), make_prompt("prompt-2")]
    collaborators.repository.count.return_value = 2
    generation_repo_mock.usage_stats_by_source_prompt.return_value = {
        "prompt-1": {"usage_count": 3, "last_used_at": "2026-01-05 00:00:00"},
    }

    response = client.get("/api/prompts")

    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["data"]["items"]}
    assert items["prompt-1"]["usage_count"] == 3
    assert items["prompt-1"]["last_used_at"] == "2026-01-05 00:00:00"
    # prompt-2 was never generated from: zero usage, not a missing key.
    assert items["prompt-2"]["usage_count"] == 0
    assert items["prompt-2"]["last_used_at"] is None
    generation_repo_mock.usage_stats_by_source_prompt.assert_called_once_with(
        ["prompt-1", "prompt-2"], "user-1"
    )


def test_get_delegates_with_user_scope(client, collaborators):
    collaborators.repository.get_by_id.return_value = make_prompt()

    response = client.get("/api/prompts/prompt-1")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == "prompt-1"
    collaborators.repository.get_by_id.assert_called_once_with("prompt-1", "user-1")


def test_put_delegates_atomic_aggregate_replacement(client, collaborators, mock_operations):
    mock_operations.replace_prompt = AsyncMock(return_value=make_prompt(text="replacement"))

    response = client.put(
        "/api/prompts/prompt-1",
        json={"segments": [{"content": "replacement"}]},
    )

    assert response.status_code == 200
    mock_operations.replace_prompt.assert_awaited_once()
    call = mock_operations.replace_prompt.await_args
    assert call is not None
    collab_arg, user_id, prompt_id, request = call.args
    assert collab_arg is collaborators
    assert (user_id, prompt_id) == ("user-1", "prompt-1")
    assert [segment.content for segment in request.segments] == ["replacement"]


def test_delete_delegates_with_user_scope(client, collaborators, mock_operations):
    mock_operations.delete_prompt.return_value = True

    response = client.delete("/api/prompts/prompt-1")

    assert response.status_code == 200
    assert response.json()["message"] == "Prompt deleted"
    mock_operations.delete_prompt.assert_called_once_with(collaborators, "user-1", "prompt-1")


def test_list_importers_serves_the_registry_manifest(client, prompt_importer_registry):
    prompt_importer_registry.register(PromptImporterDefinition(
        importer_id="fixture-importer",
        label="Fixture Importer",
        frontend_component="plugin:fixture-plugin:Importer.js",
        backend=MagicMock(),
        source="fixture-plugin",
    ))

    response = client.get("/api/prompts/importers")

    assert response.status_code == 200
    assert response.json()["data"] == [
        {"id": "fixture-importer", "label": "Fixture Importer", "component": "plugin:fixture-plugin:Importer.js"}
    ]


def test_run_import_dispatches_to_the_registered_importer_backend(client, prompt_importer_registry):
    backend = MagicMock()
    backend.run = AsyncMock(
        return_value=PromptImportOutcome(imported=2, skipped=1, total=3, items=[{"id": "p1"}])
    )
    prompt_importer_registry.register(PromptImporterDefinition(
        importer_id="fixture-importer",
        label="Fixture Importer",
        frontend_component="plugin:fixture-plugin:Importer.js",
        backend=backend,
        source="fixture-plugin",
    ))

    response = client.post("/api/prompts/import/fixture-importer", json={"content": "a fox"})

    assert response.status_code == 200
    assert response.json()["data"] == {
        "imported": 2, "skipped": 1, "total": 3, "items": [{"id": "p1"}], "error": None,
    }
    backend.run.assert_awaited_once_with({"content": "a fox"}, "user-1")


def test_run_import_reports_unknown_importer_as_404(client):
    response = client.post("/api/prompts/import/does-not-exist", json={})

    assert response.status_code == 404


@pytest.fixture
def admin_client(collaborators, tmp_path):
    settings = MagicMock()
    settings.get_setting.side_effect = lambda key, default=None: {
        "prompt_embedding_model": "BAAI/bge-small-en-v1.5",
    }.get(key, default)
    settings.get_models_dir.return_value = str(tmp_path)
    download_queue = MagicMock()
    download_queue.find_active_download_for_repo.return_value = None

    router = build_router(
        SimpleNamespace(
            prompt_database_controller=PromptDatabaseController(collaborators),
            settings=settings,
            download_queue=download_queue,
            prompt_importer_registry=PromptImporterRegistry(),
        )
    )
    app = FastAPI()
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id="a1", username="admin", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client, tmp_path, download_queue


def test_embedding_status_reports_absent_weights_for_saved_model(admin_client):
    client, tmp_path, _ = admin_client
    resp = client.get("/api/prompts/embedding-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["present"] is False
    assert data["path"] == str(tmp_path / "text_embeddings" / "baai-bge-small-en-v1-5")
    assert data["active_download"] is None


def test_embedding_status_honors_unsaved_model_override(admin_client):
    client, tmp_path, _ = admin_client
    resp = client.get("/api/prompts/embedding-status", params={"model_name": "intfloat/e5-small"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["path"] == str(tmp_path / "text_embeddings" / "intfloat-e5-small")


def test_embedding_status_reports_active_download_while_queued(admin_client):
    """A reloading client must see an in-flight fetch through this endpoint
    alone - no page-local downloadId->kind map involved."""
    client, tmp_path, download_queue = admin_client
    active = Download(
        id="dl-1",
        type=DownloadType.HF_REPO,
        url="https://huggingface.co/BAAI/bge-small-en-v1.5",
        destination_path=str(tmp_path / "text_embeddings" / "baai-bge-small-en-v1-5"),
        filename="BAAI/bge-small-en-v1.5",
        status=DownloadStatus.DOWNLOADING,
        progress=0.42,
        total_bytes=1000,
        downloaded_bytes=420,
        repo_id="BAAI/bge-small-en-v1.5",
    )
    download_queue.find_active_download_for_repo.return_value = active

    resp = client.get("/api/prompts/embedding-status")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["active_download"]["id"] == "dl-1"
    assert data["active_download"]["status"] == "downloading"
    assert data["active_download"]["progress"] == 0.42
    download_queue.find_active_download_for_repo.assert_called_once_with("BAAI/bge-small-en-v1.5")


def test_embedding_status_reports_loaded_when_active_provider_matches(admin_client, collaborators):
    """`loaded` is residency (in memory), never inferred from `present`
    (on-disk) - a model can be on disk and evicted, or on disk and loaded."""
    client, _, _ = admin_client
    collaborators.embedding_provider = SimpleNamespace(
        model_name="BAAI/bge-small-en-v1.5", is_loaded=lambda: True
    )

    resp = client.get("/api/prompts/embedding-status")

    assert resp.json()["data"]["loaded"] is True


def test_embedding_status_reports_not_loaded_for_an_unsaved_override_model(admin_client, collaborators):
    """The active provider instance's residency answers for its own model
    only - querying a different (unsaved) model id must not borrow it."""
    client, _, _ = admin_client
    collaborators.embedding_provider = SimpleNamespace(
        model_name="BAAI/bge-small-en-v1.5", is_loaded=lambda: True
    )

    resp = client.get("/api/prompts/embedding-status", params={"model_name": "intfloat/e5-small"})

    assert resp.json()["data"]["loaded"] is False


def _make_generation(gen_id="gen-1"):
    return Generation(
        id=gen_id,
        preset_id="preset-1",
        form_data={},
        user_id="user-1",
        status="completed",
        created_at=datetime(2026, 1, 5),
        files=[File(
            id="file-1", file_path="generations/x.png", file_type="IMAGE",
            user_id="user-1", is_final=True, thumbnail_small="generations/x_thumb.png",
        )],
    )


def test_get_prompt_generations_delegates_with_user_scope_and_pagination(
    client, generation_repo_mock
):
    generation_repo_mock.get_by_source_prompt.return_value = [_make_generation()]
    generation_repo_mock.count_by_source_prompt.return_value = 1

    response = client.get("/api/prompts/prompt-1/generations?limit=5&offset=10")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["limit"] == 5
    assert data["offset"] == 10
    assert data["items"][0]["id"] == "gen-1"
    assert data["items"][0]["preset_id"] == "preset-1"
    assert data["items"][0]["created_at"] == "2026-01-05T00:00:00"
    assert data["items"][0]["files"][0]["thumbnail_small"] == "generations/x_thumb.png"
    generation_repo_mock.get_by_source_prompt.assert_called_once_with(
        "prompt-1", "user-1", limit=5, offset=10
    )
    generation_repo_mock.count_by_source_prompt.assert_called_once_with("prompt-1", "user-1")


def test_get_prompt_generations_defaults_to_no_results(client, generation_repo_mock):
    response = client.get("/api/prompts/prompt-with-no-usage/generations")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0


class TestRouteOrder:
    """FastAPI dispatches in registration order: every static/collection GET
    sibling must be registered before the catch-all `GET /{prompt_id}` -
    otherwise it is swallowed as a prompt id. `/{prompt_id}/generations` is a
    two-segment path so it can never collide with the single-segment
    `/{prompt_id}` regardless of order, but is checked here anyway."""

    def test_static_get_paths_register_before_the_prompt_id_catch_all(self, built_router):
        get_paths = [
            route.path for route in built_router.routes
            if isinstance(route, APIRoute) and "GET" in route.methods
        ]
        catch_all = get_paths.index("/api/prompts/{prompt_id}")
        for static in ("/api/prompts/embedding-status", "/api/prompts/search", "/api/prompts"):
            assert get_paths.index(static) < catch_all, (
                f"{static} is registered after /{{prompt_id}} and can never match"
            )

    def test_prompt_generations_route_is_registered(self, built_router):
        get_paths = [
            route.path for route in built_router.routes
            if isinstance(route, APIRoute) and "GET" in route.methods
        ]
        assert "/api/prompts/{prompt_id}/generations" in get_paths
