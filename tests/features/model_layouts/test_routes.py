from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.model_layouts.catalog import ModelLayoutCatalog
from src.features.model_layouts.routes import build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.features.model_layouts.helpers import write_layout


def _user(account_type):
    return User(id="u1", username="u", email="u@example.com", password_hash="h", account_type=account_type)


def _client(catalog, role):
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(model_layout_catalog=catalog)))

    async def _fake_user():
        return _user(role)

    app.dependency_overrides[get_current_active_user] = _fake_user
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def catalog(tmp_path):
    write_layout(tmp_path / "marketplace", "alpha", label="Alpha")
    write_layout(tmp_path / "local", "beta", label="Beta")
    (tmp_path / "local" / "broken.yml").write_text("id: [", encoding="utf-8")
    return ModelLayoutCatalog(str(tmp_path))


def test_admin_lists_layouts_and_load_errors(catalog, tmp_path):
    response = _client(catalog, AccountType.ADMIN).get("/api/models/layouts")
    assert response.status_code == 200
    body = response.json()
    assert body["layouts"] == [
        {"id": "alpha", "label": "Alpha", "source": "marketplace", "plugin_id": None},
        {"id": "beta", "label": "Beta", "source": "local", "plugin_id": None},
    ]
    assert list(body["load_errors"]) == [str(tmp_path / "local" / "broken.yml")]


def test_regular_user_is_denied(catalog):
    response = _client(catalog, AccountType.USER).get("/api/models/layouts")
    assert response.status_code == 403


def test_unauthenticated_request_is_denied(catalog):
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(model_layout_catalog=catalog)))
    assert TestClient(app, raise_server_exceptions=False).get("/api/models/layouts").status_code in (401, 403)


def test_plugin_layouts_carry_plugin_id(tmp_path):
    plugin_dir = tmp_path / "plugin"
    write_layout(plugin_dir / "layouts", "gamma", label="Gamma")
    registry = SimpleNamespace(
        get_enabled_plugins=lambda: [SimpleNamespace(id="p1", plugin_dir=plugin_dir, model_layouts=[{"path": "layouts"}])]
    )
    catalog = ModelLayoutCatalog(str(tmp_path / "core"), plugin_registry=registry)
    body = _client(catalog, AccountType.ADMIN).get("/api/models/layouts").json()
    assert body["layouts"] == [{"id": "gamma", "label": "Gamma", "source": "plugin", "plugin_id": "p1"}]


def test_layouts_path_is_not_swallowed_by_the_model_id_route():
    from unittest.mock import MagicMock

    from src.bootstrap.routers import register_routers

    app = FastAPI()
    register_routers(app, MagicMock())
    scope = {"type": "http", "method": "GET", "path": "/api/models/layouts", "root_path": ""}
    from starlette.routing import Match

    matched = next(route for route in app.routes if route.matches(scope)[0] == Match.FULL)
    assert matched.name == "list_layouts"


def test_container_declares_the_catalog_field():
    import dataclasses

    from src.bootstrap.container import AppContainer

    assert "model_layout_catalog" in {f.name for f in dataclasses.fields(AppContainer)}
