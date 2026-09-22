"""Endpoint-level tests for the civitai-provider plugin's `/prompts/fetch`
and `/prompts/options` routes.

Mirrors `test_civitai_export_endpoint.py`'s recipe: load the real `backend.api`
module from disk under a private package name, mount its `router` on a bare
FastAPI app, and override the auth dependencies. `ensure_providers_discovered`,
`get_model_provider_info`, `import_prompts_for_user`, and `list_user_ids` are
patched directly on the loaded module - each is exercised on its own in
`tests/plugin_api/test_prompts.py`/`test_models.py`/`test_identity.py`, so
here the goal is proving this route's own orchestration (validation,
per-model error handling, dedupe-by-text, fan-out to users, the all-models-
failed 502) rather than re-proving the prompt-database plumbing underneath it.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import AccountType, User
from src.features.providers import ProviderConnectionError, ProviderNotFoundError

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_PLUGIN_DIR = os.path.join(REPO_ROOT, "content", "plugins", "marketplace", "civitai-provider")
_BACKEND_DIR = os.path.join(_PLUGIN_DIR, "backend")

_PKG_NAME = "civitai_provider_prompts_fetch_test_plugin"
_BACKEND_PKG = f"{_PKG_NAME}.backend"


def _make_package(name, path):
    pkg = types.ModuleType(name)
    pkg.__path__ = [path]
    pkg.__package__ = name
    sys.modules[name] = pkg
    return pkg


def _load_plugin_module():
    if _PKG_NAME not in sys.modules:
        _make_package(_PKG_NAME, _PLUGIN_DIR)
        _make_package(_BACKEND_PKG, _BACKEND_DIR)

    full_name = f"{_BACKEND_PKG}.api"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, os.path.join(_BACKEND_DIR, "api.py"))
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _BACKEND_PKG
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def _user(user_id="u-admin", account_type=AccountType.ADMIN) -> User:
    return User(
        id=user_id, username=user_id, email=f"{user_id}@example.com",
        password_hash="h", account_type=account_type,
    )


class _FakeProvider:
    def __init__(self, showcase=None, paginated=None, showcase_error=None, paginated_error=None):
        self._showcase = showcase or []
        self._paginated = paginated or []
        self._showcase_error = showcase_error
        self._paginated_error = paginated_error
        self.showcase_calls = []
        self.paginated_calls = []

    async def fetch_showcase_prompts(self, model_version_id):
        self.showcase_calls.append(model_version_id)
        if self._showcase_error:
            raise self._showcase_error
        return list(self._showcase)

    async def fetch_image_prompts(self, **kwargs):
        self.paginated_calls.append(kwargs)
        if self._paginated_error:
            raise self._paginated_error
        return list(self._paginated)


class _FakeRegistry:
    def __init__(self, providers):
        self._providers = providers

    def get_provider(self, provider_id):
        return self._providers.get(provider_id)


def _item(source_id, prompt="a fox", **overrides):
    base = {
        "source_id": source_id, "prompt": prompt, "negative_prompt": "blurry",
        "model_name": "Some Model", "base_model": "SDXL 1.0", "cfg_scale": 7.0,
        "steps": 20, "sampler": "Euler a", "seed": 123, "clip_skip": 2,
        "width": 1024, "height": 1024, "username": "someartist",
        "stats": {"heart_count": 1, "like_count": 2, "laugh_count": 0, "cry_count": 0, "comment_count": 0},
        "tags": [], "nsfw": False, "source_url": f"https://civitai.com/images/{source_id}",
    }
    base.update(overrides)
    return base


@pytest.fixture
def module():
    return _load_plugin_module()


@pytest.fixture
def admin_client(module):
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_admin_user] = lambda: _user()
    return TestClient(app)


@pytest.fixture
def non_admin_client(module):
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_active_user] = lambda: _user("u-plain", AccountType.USER)
    return TestClient(app)


def test_options_returns_enums_and_defaults(admin_client):
    resp = admin_client.get("/api/plugins/civitai-provider/prompts/options")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["sorts"] == module_sorts()
    assert data["nsfw"] == ["None", "Soft", "Mature", "X"]
    assert data["limit_max"] == 200
    assert data["defaults"]["sort"] == "Most Reactions"


def module_sorts():
    return ["Most Reactions", "Most Comments", "Most Collected", "Newest", "Oldest", "Random", "Recently Added"]


def test_options_requires_admin(non_admin_client):
    resp = non_admin_client.get("/api/plugins/civitai-provider/prompts/options")

    assert resp.status_code == 403


def test_fetch_prompts_empty_model_ids_is_400_invalid_request(admin_client):
    resp = admin_client.post("/api/plugins/civitai-provider/prompts/fetch", json={"model_ids": []})

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_request"


def test_fetch_prompts_unknown_sort_is_400_invalid_request(admin_client):
    resp = admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-1"], "sort": "Not A Sort"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_request"


def test_fetch_prompts_limit_out_of_range_is_400_invalid_request(admin_client):
    resp = admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-1"], "limit": 500},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_request"


def test_fetch_prompts_reports_model_not_linked_and_continues(module, admin_client, monkeypatch):
    provider = _FakeProvider()
    monkeypatch.setattr(module, "ensure_providers_discovered", AsyncMock(return_value=_FakeRegistry({"civitai": provider})))
    monkeypatch.setattr(module, "get_model_provider_info", lambda model_id, provider=None: None)
    monkeypatch.setattr(module, "list_user_ids", lambda: ["u-1"])
    import_mock = AsyncMock(return_value={"created": 0, "skipped_duplicates": 0})
    monkeypatch.setattr(module, "import_prompts_for_user", import_mock)

    resp = admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-1"]},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["models"][0]["error"] == "model_not_linked"
    import_mock.assert_not_awaited()


def test_fetch_prompts_happy_path_dedupes_by_text_and_fans_out_to_users(module, admin_client, monkeypatch):
    provider = _FakeProvider(
        showcase=[_item("1", prompt="A fox   in snow")],
        paginated=[_item("2", prompt="a FOX in snow"), _item("3", prompt="a wolf")],
    )
    registry = _FakeRegistry({"civitai": provider})
    monkeypatch.setattr(module, "ensure_providers_discovered", AsyncMock(return_value=registry))
    monkeypatch.setattr(
        module, "get_model_provider_info",
        lambda model_id, provider=None: {
            "provider": "civitai", "provider_model_id": "999", "provider_version_id": "888", "model_name": "Some Model",
        },
    )
    monkeypatch.setattr(module, "list_user_ids", lambda: ["u-should-not-be-called"])
    import_mock = AsyncMock(return_value={"created": 1, "skipped_duplicates": 0})
    monkeypatch.setattr(module, "import_prompts_for_user", import_mock)

    resp = admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-1"], "user_ids": ["u-1", "u-2"], "limit": 50},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["users"] == ["u-1", "u-2"]

    entry = data["models"][0]
    assert entry["error"] is None
    assert entry["version_id"] == "888"
    # 3 images seen (1 showcase + 2 paginated); "a fox in snow" collapses with
    # its differently-cased/spaced duplicate, leaving 2 distinct prompts.
    assert entry["images_seen"] == 3
    assert entry["with_prompt"] == 2
    assert entry["created"] == 2  # import_mock's canned "created": 1, once per user
    assert import_mock.await_count == 2

    entries_arg = import_mock.await_args_list[0].args[1]
    assert len(entries_arg) == 2
    assert {e["source_id"] for e in entries_arg} == {"1", "3"}


def test_fetch_prompts_include_negative_false_drops_negative_prompt(module, admin_client, monkeypatch):
    provider = _FakeProvider(paginated=[_item("1")])
    monkeypatch.setattr(module, "ensure_providers_discovered", AsyncMock(return_value=_FakeRegistry({"civitai": provider})))
    monkeypatch.setattr(
        module, "get_model_provider_info",
        lambda model_id, provider=None: {"provider": "civitai", "provider_model_id": "9", "provider_version_id": None, "model_name": "M"},
    )
    import_mock = AsyncMock(return_value={"created": 1, "skipped_duplicates": 0})
    monkeypatch.setattr(module, "import_prompts_for_user", import_mock)

    admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-1"], "user_ids": ["u-1"], "include_negative": False},
    )

    entries_arg = import_mock.await_args_list[0].args[1]
    assert entries_arg[0]["negative_prompt"] is None


def test_fetch_prompts_user_ids_null_fans_out_to_all_users(module, admin_client, monkeypatch):
    provider = _FakeProvider(paginated=[_item("1")])
    monkeypatch.setattr(module, "ensure_providers_discovered", AsyncMock(return_value=_FakeRegistry({"civitai": provider})))
    monkeypatch.setattr(
        module, "get_model_provider_info",
        lambda model_id, provider=None: {"provider": "civitai", "provider_model_id": "9", "provider_version_id": "8", "model_name": "M"},
    )
    monkeypatch.setattr(module, "list_user_ids", lambda: ["u-a", "u-b", "u-c"])
    import_mock = AsyncMock(return_value={"created": 1, "skipped_duplicates": 0})
    monkeypatch.setattr(module, "import_prompts_for_user", import_mock)

    resp = admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-1"]},
    )

    assert resp.json()["data"]["users"] == ["u-a", "u-b", "u-c"]
    assert import_mock.await_count == 3


def test_fetch_prompts_network_failure_on_every_model_is_502(module, admin_client, monkeypatch):
    provider = _FakeProvider(paginated_error=ProviderConnectionError("down"))
    monkeypatch.setattr(module, "ensure_providers_discovered", AsyncMock(return_value=_FakeRegistry({"civitai": provider})))
    monkeypatch.setattr(
        module, "get_model_provider_info",
        lambda model_id, provider=None: {"provider": "civitai", "provider_model_id": "9", "provider_version_id": "8", "model_name": "M"},
    )
    monkeypatch.setattr(module, "list_user_ids", lambda: ["u-1"])

    resp = admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-1", "model-2"]},
    )

    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "civitai_unavailable"


def test_fetch_prompts_partial_network_failure_returns_200_with_per_model_error(module, admin_client, monkeypatch):
    good_provider_calls = {"count": 0}

    class _MixedProvider(_FakeProvider):
        async def fetch_image_prompts(self, **kwargs):
            if kwargs.get("model_version_id") == "fails":
                raise ProviderConnectionError("down")
            good_provider_calls["count"] += 1
            return [_item("1")]

    provider = _MixedProvider()
    monkeypatch.setattr(module, "ensure_providers_discovered", AsyncMock(return_value=_FakeRegistry({"civitai": provider})))

    def _link(model_id, provider=None):
        version = "fails" if model_id == "model-bad" else "ok"
        return {"provider": "civitai", "provider_model_id": "9", "provider_version_id": version, "model_name": "M"}

    monkeypatch.setattr(module, "get_model_provider_info", _link)
    monkeypatch.setattr(module, "list_user_ids", lambda: ["u-1"])
    import_mock = AsyncMock(return_value={"created": 1, "skipped_duplicates": 0})
    monkeypatch.setattr(module, "import_prompts_for_user", import_mock)

    resp = admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-bad", "model-good"]},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    by_id = {m["model_id"]: m for m in data["models"]}
    assert by_id["model-bad"]["error"] == "civitai_unavailable"
    assert by_id["model-good"]["error"] is None


def test_fetch_prompts_model_not_found_on_civitai_reports_model_not_linked(module, admin_client, monkeypatch):
    provider = _FakeProvider(paginated_error=ProviderNotFoundError("gone"))
    monkeypatch.setattr(module, "ensure_providers_discovered", AsyncMock(return_value=_FakeRegistry({"civitai": provider})))
    monkeypatch.setattr(
        module, "get_model_provider_info",
        lambda model_id, provider=None: {"provider": "civitai", "provider_model_id": "9", "provider_version_id": "8", "model_name": "M"},
    )
    monkeypatch.setattr(module, "list_user_ids", lambda: ["u-1"])

    resp = admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-1"]},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["models"][0]["error"] == "model_not_linked"


def test_fetch_prompts_requires_admin(non_admin_client):
    resp = non_admin_client.post(
        "/api/plugins/civitai-provider/prompts/fetch",
        json={"model_ids": ["model-1"]},
    )

    assert resp.status_code == 403
